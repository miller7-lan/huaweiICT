param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimePath = Join-Path $projectPath '.runtime\knowledge-update'
$statusPath = Join-Path $runtimePath 'startup-recovery-status.json'
$pausePath = Join-Path $runtimePath 'service-paused.json'
$utf8 = New-Object Text.UTF8Encoding($false)
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($runtimePath)))).Replace('-', '').Substring(0, 16) }
finally { $sha.Dispose() }
$mutex = New-Object Threading.Mutex($false, ('Local\HuaweiICTKnowledgeUpdate-' + $key))
$locked = $false
$previous = $null

function Test-OwnedModule($candidate, [string]$module) {
    if (-not $candidate -or -not $candidate.CommandLine) { return $false }
    return ($candidate.CommandLine -match ('(?:^|\s)-m\s+' + [regex]::Escape($module) + '(?:\s|$)')) -and
        ($candidate.CommandLine.IndexOf($runtimePath, [StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Get-OwnedComponents {
    $found = @()
    foreach ($candidate in @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")) {
        $owned = (Test-OwnedModule $candidate 'gov_mcp.knowledge_web') -or
                 (Test-OwnedModule $candidate 'gov_mcp.knowledge_server') -or
                 (Test-OwnedModule $candidate 'gov_mcp.knowledge_maintenance') -or
                 ((Test-OwnedModule $candidate 'gov_mcp.knowledge_cli') -and $candidate.CommandLine -match '\bworker\b' -and $candidate.CommandLine -match '--cycles\s+0(?:\s|$)')
        if ($owned) { $found += $candidate }
    }
    return $found
}

function Read-ServiceState {
    $statePath = Join-Path $runtimePath 'service-state.json'
    if (Test-Path -LiteralPath $statePath) { return Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json }
    return $null
}

function Get-OwnedSupervisor($state) {
    if (-not $state) { return $null }
    $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$state.supervisor_pid) -ErrorAction SilentlyContinue
    if (Test-OwnedModule $candidate 'gov_mcp.knowledge_service') { return $candidate }
    return $null
}

function Stop-VerifiedComponent($candidate) {
    $fresh = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$candidate.ProcessId) -ErrorAction SilentlyContinue
    if ($fresh -and $fresh.CreationDate -eq $candidate.CreationDate -and $fresh.CommandLine -eq $candidate.CommandLine) {
        Stop-Process -Id $fresh.ProcessId -ErrorAction SilentlyContinue
    }
}

function Test-Ready($state) {
    if (-not (Get-OwnedSupervisor $state)) { return $false }
    $age = ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) - [double]$state.checked_at
    if ($age -gt 90) { return $false }
    foreach ($name in $state.processes.PSObject.Properties.Name) {
        $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$state.processes.$name) -ErrorAction SilentlyContinue
        $module = @{web='gov_mcp.knowledge_web'; worker='gov_mcp.knowledge_cli'; maintenance='gov_mcp.knowledge_maintenance'; mcp='gov_mcp.knowledge_server'}[$name]
        if (-not $module -or -not (Test-OwnedModule $candidate $module)) { return $false }
    }
    try {
        $value = Invoke-RestMethod -Uri 'http://127.0.0.1:8772/api/status' -TimeoutSec 5
        if ($value.mode -ne 'knowledge_update') { return $false }
        foreach ($identity in @('knowledge-worker', 'knowledge-maintenance')) {
            $worker = @($value.workers | Where-Object id -EQ $identity)
            if (-not $worker.Count -or $worker[0].stale) { return $false }
        }
        if ($state.processes.mcp) {
            $mcpHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8773/health' -TimeoutSec 5
            if ($mcpHealth.status -ne 'UP') { return $false }
        }
        # The venv launcher and the actual listener can have different PIDs.
        foreach ($port in @(8772) + $(if ($state.processes.mcp) { @(8773) } else { @() })) {
            $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -EQ $port)
            if (-not $listeners.Count) { return $false }
            foreach ($listener in $listeners) {
                $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$listener.OwningProcess)
                $module = if ($port -eq 8772) { 'gov_mcp.knowledge_web' } else { 'gov_mcp.knowledge_server' }
                if (-not (Test-OwnedModule $candidate $module)) { return $false }
            }
        }
    } catch { return $false }
    return $true
}

function Save-RecoveryStatus([string]$action, [bool]$ready, [string]$failure = $null) {
    $state = Read-ServiceState
    $count = if ($previous) { [int]$previous.recovery_count } else { 0 }
    if ($action -eq 'recovered') { $count++ }
    $lastRecoveredAt = if ($action -eq 'recovered') { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } elseif ($previous.last_recovered_at) { $previous.last_recovered_at } elseif ($previous.action -eq 'recovered') { $previous.checked_at } else { $null }
    $lastRecoveredPid = if ($action -eq 'recovered') { $state.supervisor_pid } elseif ($previous.last_recovered_supervisor_pid) { $previous.last_recovered_supervisor_pid } elseif ($previous.action -eq 'recovered') { $previous.supervisor_pid } else { $null }
    $record = [ordered]@{ checked_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); action=$action; ready=$ready;
        recovery_count=$count; last_recovered_at=$lastRecoveredAt; last_recovered_supervisor_pid=$lastRecoveredPid;
        supervisor_pid=$(if ($state) {$state.supervisor_pid} else {$null}); error=$failure;
        no_publication_performed=$true; mode='current_user_local_service_recovery' }
    $temporary = $statusPath + '.tmp'
    [IO.File]::WriteAllText($temporary, ($record | ConvertTo-Json -Depth 6), $utf8)
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
    if ($action -ne 'healthy' -and ($action -eq 'recovered' -or $previous.action -ne $action -or $previous.error -ne $failure)) {
        [IO.File]::AppendAllText((Join-Path $runtimePath 'startup-recovery-audit.jsonl'), (($record | ConvertTo-Json -Compress) + [Environment]::NewLine), $utf8)
    }
    return [pscustomobject]$record
}

try {
    $marker = Get-Content -LiteralPath (Join-Path $runtimePath 'phase.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($marker.phase -ne 'knowledge_update' -or $marker.schema_version -ne 1) { throw '运行目录标记不符，停止恢复。' }
    if (-not (Test-Path -LiteralPath (Join-Path $projectPath '.venv\Scripts\python.exe'))) { throw '项目Python环境不存在。' }
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { Write-Output '已有本项目恢复检查正在运行。'; exit 0 }
    if (Test-Path -LiteralPath $statusPath) { $previous = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    if (Test-Path -LiteralPath $pausePath) { Save-RecoveryStatus 'paused' $false; exit 0 }
    $state = Read-ServiceState
    if (Test-Ready $state) { Save-RecoveryStatus 'healthy' $true; exit 0 }
    if ($CheckOnly) { Save-RecoveryStatus 'check_failed' $false; exit 1 }
    $supervisor = Get-OwnedSupervisor $state
    if ($supervisor) {
        # Give the live supervisor a full component-repair tick before replacing it.
        Start-Sleep -Seconds 17
        $state = Read-ServiceState
        if (Test-Ready $state) { Save-RecoveryStatus 'healthy' $true; exit 0 }
        if (Test-Path -LiteralPath $pausePath) { Save-RecoveryStatus 'paused' $false; exit 0 }
        $supervisor = Get-OwnedSupervisor $state
        if ($supervisor) { Stop-VerifiedComponent $supervisor }
    }
    foreach ($candidate in @(Get-OwnedComponents)) { Stop-VerifiedComponent $candidate }
    Start-Sleep -Seconds 1
    foreach ($port in @(8772,8773)) {
        if (@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -EQ $port).Count) {
            throw "端口 $port 仍被占用；不会停止未确认归属的程序。"
        }
    }
    if (Test-Path -LiteralPath $pausePath) { Save-RecoveryStatus 'paused' $false; exit 0 }
    & (Join-Path $PSScriptRoot 'start_knowledge_update.ps1') -FromRecovery | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds(35)
    do {
        if (Test-Ready (Read-ServiceState)) { Save-RecoveryStatus 'recovered' $true; exit 0 }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw '本项目恢复后尚未就绪，请检查私有运行日志。'
} catch {
    if ($locked -and (Test-Path -LiteralPath $runtimePath)) { Save-RecoveryStatus 'failed' $false $_.Exception.Message | Out-Null }
    Write-Error $_.Exception.Message
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
