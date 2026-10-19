$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimePath = Join-Path $projectPath '.runtime\knowledge-update'
$registration = Get-Content -LiteralPath (Join-Path $runtimePath 'startup-registration.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$before = Invoke-RestMethod -Uri 'http://127.0.0.1:8772/api/status' -TimeoutSec 10
$checks = [ordered]@{}
$stopped = $false

function Wait-TaskResult([string]$expectedAction, [long]$notBefore) {
    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    do {
        $task = Get-ScheduledTask -TaskName $registration.task_name
        $info = Get-ScheduledTaskInfo -TaskName $registration.task_name
        $state = Get-Content -LiteralPath (Join-Path $runtimePath 'startup-recovery-status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$task.State -eq 'Ready' -and $info.LastTaskResult -eq 0 -and $state.action -eq $expectedAction -and $state.checked_at -ge $notBefore) { return $true }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

try {
    & (Join-Path $PSScriptRoot 'stop_knowledge_update.ps1') | Out-Null
    $stopped = $true
    Start-Sleep -Seconds 2
    $checks.manual_stop_sets_pause = Test-Path -LiteralPath (Join-Path $runtimePath 'service-paused.json')
    $stoppedPorts = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -in @(8772,8773) })
    $checks.owned_services_stopped = $stoppedPorts.Count -eq 0
    $start = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Start-ScheduledTask -TaskName $registration.task_name
    $checks.scheduled_check_respects_pause = Wait-TaskResult 'paused' $start
    $afterPausePorts = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -in @(8772,8773) })
    $checks.no_automatic_relaunch_after_manual_stop = $afterPausePorts.Count -eq 0
} finally {
    if ($stopped) {
        & (Join-Path $PSScriptRoot 'start_knowledge_update.ps1') | Out-Null
    }
}
$checks.manual_start_clears_pause = -not (Test-Path -LiteralPath (Join-Path $runtimePath 'service-paused.json'))
$state = Get-Content -LiteralPath (Join-Path $runtimePath 'service-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$start = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
Start-ScheduledTask -TaskName $registration.task_name
$checks.scheduled_check_after_resume_is_healthy = Wait-TaskResult 'healthy' $start
$after = Invoke-RestMethod -Uri 'http://127.0.0.1:8772/api/status' -TimeoutSec 10
$newState = Get-Content -LiteralPath (Join-Path $runtimePath 'service-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$checks.healthy_resume_does_not_duplicate_supervisor = $state.supervisor_pid -eq $newState.supervisor_pid
$countsMatch = $true
foreach ($key in @('documents','revisions','chunks','latest_chunks','versions','heads')) {
    if ($before.counts.$key -ne $after.counts.$key) { $countsMatch = $false }
}
$checks.business_counts_unchanged = $countsMatch
$checks.no_official_publication = $after.counts.heads -eq 0
$passed = @($checks.Values | Where-Object { -not $_ }).Count -eq 0
$report = @{captured_at=[DateTime]::UtcNow.ToString('o'); passed=$passed; checks=$checks; counts=$after.counts;
    scope='Actual manual stop/pause, scheduled no-op and manual resume with a healthy scheduled check; the service was restored at the end.'}
[IO.File]::WriteAllText((Join-Path $projectPath 'evidence\knowledge-update\startup-manual-control.json'),($report | ConvertTo-Json -Depth 6),(New-Object Text.UTF8Encoding($false)))
[pscustomobject]@{Passed=$passed; CheckCount=$checks.Count; ServicesRunning=$checks.scheduled_check_after_resume_is_healthy}
if (-not $passed) { throw '手动控制检查未全部通过。' }
