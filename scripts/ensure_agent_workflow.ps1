param([ValidateSet('Check','Ensure','Register','Unregister','Pause','Resume')][string]$Action='Check')
$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimePath = Join-Path $projectPath '.runtime\agent-workflow'
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$inspectPath = Join-Path $PSScriptRoot 'inspect_agent_workflow.py'
$pausePath = Join-Path $runtimePath 'recovery-paused.json'
$statusPath = Join-Path $runtimePath 'recovery-status.json'
$utf8 = New-Object Text.UTF8Encoding($false)
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($runtimePath.ToLowerInvariant())))).Replace('-','').Substring(0,16) }
finally { $sha.Dispose() }
$taskName = 'HuaweiICT-AgentWorkflow-' + $key
$shellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$taskArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Action Ensure'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()

function Test-TaskOwner($task) {
    if (-not $task) { return $false }
    $sid = if ($task.Principal.UserId.StartsWith('S-1-')) { $task.Principal.UserId } else {
        (New-Object Security.Principal.NTAccount($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    return @($task.Actions).Count -eq 1 -and $task.Actions[0].Execute -eq $shellPath -and
        $task.Actions[0].Arguments -eq $taskArguments -and $task.Actions[0].WorkingDirectory -eq $projectPath -and
        $sid -eq $identity.User.Value -and [string]$task.Principal.RunLevel -eq 'Limited' -and
        [string]$task.Principal.LogonType -eq 'Interactive'
}

function Get-Guard {
    $raw = & $pythonPath $inspectPath --require-paused
    if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_GUARD_REQUIRES_PAUSED_OFFLINE_WORKFLOW' }
    return $raw | ConvertFrom-Json
}

function Test-OwnedProcess($candidate) {
    return $candidate -and $candidate.CommandLine -and
        $candidate.CommandLine -match '(?:^|\s)-m\s+gov_mcp\.decision\.server(?:\s|$)' -and
        $candidate.CommandLine -match '--port\s+8780(?:\s|$)' -and
        $candidate.CommandLine -match ('--runtime\s+"?' + [regex]::Escape($runtimePath) + '"?(?:\s|$)')
}

function Get-Listeners {
    return @(Get-NetTCPConnection -State Listen -LocalPort 8780 -ErrorAction SilentlyContinue)
}

function Test-Ready {
    $listeners = @(Get-Listeners)
    if (-not $listeners.Count) { return $false }
    foreach ($listener in $listeners) {
        $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess)
        if (-not (Test-OwnedProcess $candidate)) { throw 'UNOWNED_PORT_8780_REFUSE_RECOVERY' }
    }
    $guard = Get-Guard
    if (-not $guard.heartbeat_fresh) { throw 'OWNED_LISTENER_STALE_REQUIRES_INSPECTION' }
    try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8780/' -TimeoutSec 5; return $response.StatusCode -eq 200 }
    catch { throw 'OWNED_LISTENER_UNHEALTHY_REQUIRES_INSPECTION' }
}

$mutex = New-Object Threading.Mutex($false, ('Local\HuaweiICTAgentWorkflow-' + $key))
$locked = $false
try {
    if ($Action -ne 'Check') {
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { @{action='already_checking';model_calls=0} | ConvertTo-Json -Compress; exit 0 }
    }
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and -not (Test-TaskOwner $existing)) { throw 'UNOWNED_TASK_REFUSE_CHANGE' }
    if ($Action -eq 'Unregister') {
        if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        @{action='unregistered';model_calls=0} | ConvertTo-Json -Compress; exit 0
    }
    if ($Action -eq 'Pause') {
        [IO.File]::WriteAllText($pausePath, '{"paused":true}', $utf8)
        @{action='recovery_paused';business_control_changed=$false;model_calls=0} | ConvertTo-Json -Compress; exit 0
    }
    $before = Get-Guard
    if ($Action -eq 'Register') {
        $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
        $taskAction = New-ScheduledTaskAction -Execute $shellPath -Argument $taskArguments -WorkingDirectory $projectPath
        $logon = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
        $periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger @($logon,$periodic) -Principal $principal -Settings $settings -Description 'Restore the paused local ICT workflow and check the prepared official-source observer when due; never enable inference, paid jobs or publication.' -Force | Out-Null
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        if (-not (Test-TaskOwner $existing)) { throw 'TASK_READBACK_FAILED' }
        @{action='registered';task_name=$taskName;current_user_only=$true;automatic_publication=$false;model_calls=0} | ConvertTo-Json -Compress; exit 0
    }
    if ($Action -eq 'Resume' -and (Test-Path -LiteralPath $pausePath)) { Remove-Item -LiteralPath $pausePath }
    $paused = Test-Path -LiteralPath $pausePath
    $ready = Test-Ready
    $outcome = if ($ready) { 'healthy' } else { 'missing' }
    if (-not $ready -and -not $paused -and $Action -in @('Ensure','Resume')) {
        # No foreign process is ever stopped. An existing hung listener fails closed.
        $env:PYTHONPATH = Join-Path $projectPath 'src'
        $env:PYTHONIOENCODING = 'utf-8'
        $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssffff')
        Start-Process -FilePath $pythonPath -ArgumentList @('-m','gov_mcp.decision.server','--runtime',('"' + $runtimePath + '"'),'--port','8780') -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimePath ($stamp + '.stdout.log')) -RedirectStandardError (Join-Path $runtimePath ($stamp + '.stderr.log')) | Out-Null
        for ($index=0; $index -lt 20; $index++) {
            Start-Sleep -Seconds 1
            if (Test-Ready) { $ready=$true; break }
        }
        if (-not $ready) { throw 'RECOVERY_START_TIMEOUT' }
        $outcome = 'recovered'
    }
    $after = Get-Guard
    if ($before.head -ne $after.head -or $before.version_count -ne $after.version_count) { throw 'WORKFLOW_BUSINESS_STATE_CHANGED' }
    $record = [ordered]@{action=$outcome;ready=[bool]$ready;recovery_paused=[bool]$paused;task_installed=[bool]$existing;task_name=$taskName;checked_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();head=$after.head;version_count=$after.version_count;heartbeat_at=$after.heartbeat_at;business_paused=$after.paused;model_calls=0}
    if ($Action -ne 'Check') { [IO.File]::WriteAllText($statusPath, ($record | ConvertTo-Json), $utf8) }
    $record | ConvertTo-Json -Compress
    $watchMarker = Join-Path $projectPath '.runtime\official-source-watch\watch-config.json'
    if ($Action -eq 'Ensure' -and $ready -and -not $paused -and (Test-Path -LiteralPath $watchMarker)) {
        & $pythonPath (Join-Path $PSScriptRoot 'watch_official_sources.py') cycle | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'OFFICIAL_SOURCE_WATCH_CYCLE_FAILED' }
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
