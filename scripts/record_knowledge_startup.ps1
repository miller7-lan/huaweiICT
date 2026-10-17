$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimePath = Join-Path $projectPath '.runtime\knowledge-update'
$registration = Get-Content -LiteralPath (Join-Path $runtimePath 'startup-registration.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$recovery = Get-Content -LiteralPath (Join-Path $runtimePath 'startup-recovery-status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$injection = Get-Content -LiteralPath (Join-Path $runtimePath 'startup-fault-injection.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$state = Get-Content -LiteralPath (Join-Path $runtimePath 'service-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$task = Get-ScheduledTask -TaskName $registration.task_name -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName $registration.task_name -ErrorAction Stop
$status = Invoke-RestMethod -Uri 'http://127.0.0.1:8772/api/status' -TimeoutSec 10
$before = Get-Content -LiteralPath (Join-Path $projectPath 'evidence\knowledge-update\intake-step-status.json') -Raw -Encoding UTF8 | ConvertFrom-Json

function Test-Owned($candidate, [string]$module) {
    return $candidate -and $candidate.CommandLine -and ($candidate.CommandLine -match ('(?:^|\s)-m\s+' + [regex]::Escape($module) + '(?:\s|$)')) -and
        ($candidate.CommandLine.IndexOf($runtimePath,[StringComparison]::OrdinalIgnoreCase) -ge 0)
}

$supervisor = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$state.supervisor_pid) -ErrorAction SilentlyContinue
$old = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$injection.supervisor_pid) -ErrorAction SilentlyContinue
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$configuredSid = if ($task.Principal.UserId.StartsWith('S-1-')) {
    $task.Principal.UserId
} else {
    (New-Object Security.Principal.NTAccount($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
}
$countsMatch = $true
foreach ($key in @('documents','revisions','chunks','latest_chunks','versions','heads')) {
    if ($status.counts.$key -ne $before.counts.$key) { $countsMatch = $false }
}
$logicalChildrenHealthy = $true
foreach ($property in $state.processes.PSObject.Properties) {
    $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$property.Value) -ErrorAction SilentlyContinue
    $module = @{web='gov_mcp.knowledge_web';worker='gov_mcp.knowledge_cli';maintenance='gov_mcp.knowledge_maintenance';mcp='gov_mcp.knowledge_server'}[$property.Name]
    if (-not $module -or -not (Test-Owned $candidate $module)) { $logicalChildrenHealthy = $false }
}
$ancestorIds = @([int]$state.supervisor_pid)
$ancestor = $supervisor
while ($ancestor) {
    $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$ancestor.ParentProcessId) -ErrorAction SilentlyContinue
    if (-not (Test-Owned $candidate 'gov_mcp.knowledge_service')) { break }
    $ancestorIds += [int]$candidate.ProcessId
    $ancestor = $candidate
}
$extraSupervisors = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    (Test-Owned $_ 'gov_mcp.knowledge_service') -and [int]$_.ProcessId -notin $ancestorIds
})
$portChecks = @()
foreach ($port in @(8772,8773)) {
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -EQ $port)
    $owned = $listeners.Count -gt 0
    foreach ($listener in $listeners) {
        $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$listener.OwningProcess)
        $module = if ($port -eq 8772) {'gov_mcp.knowledge_web'} else {'gov_mcp.knowledge_server'}
        if (-not (Test-Owned $candidate $module)) { $owned = $false }
    }
    $portChecks += @{port=$port; owned_listener=$owned}
}
$intervals = @($task.Triggers | Where-Object { $_.Repetition.Interval } | ForEach-Object { $_.Repetition.Interval })
$logonTriggers = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' })
$activeWorkers = @($status.workers | Where-Object { $_.id -in @('knowledge-worker','knowledge-maintenance') })
$lastRecoveredAt = if ($recovery.last_recovered_at) { $recovery.last_recovered_at } else { $recovery.checked_at }
$checks = [ordered]@{
    task_registered_for_current_user = ($registration.installed -and $configuredSid -eq $currentIdentity.User.Value)
    task_runs_without_elevation = ([string]$task.Principal.RunLevel -eq 'Limited')
    task_hidden_action = ($task.Actions[0].Arguments -like '*-WindowStyle Hidden*')
    login_and_five_minute_triggers_present = (@($task.Triggers).Count -eq 2 -and $logonTriggers.Count -eq 1 -and 'PT5M' -in $intervals)
    scheduled_action_completed_successfully = ([string]$task.State -eq 'Ready' -and $info.LastTaskResult -eq 0)
    natural_trigger_followed_fault = ($info.LastRunTime.ToUniversalTime() -ge [DateTime]::Parse($injection.expected_next_run).ToUniversalTime() -and $lastRecoveredAt -ge $injection.started_at)
    owned_supervisor_replaced = ((Test-Owned $supervisor 'gov_mcp.knowledge_service') -and $state.supervisor_pid -ne $injection.supervisor_pid -and -not (Test-Owned $old 'gov_mcp.knowledge_service'))
    no_extra_runtime_supervisor = ($extraSupervisors.Count -eq 0)
    four_components_alive = ($logicalChildrenHealthy -and @($state.processes.PSObject.Properties).Count -eq 4)
    listeners_owned_by_this_runtime = (@($portChecks | Where-Object { -not $_.owned_listener }).Count -eq 0)
    active_worker_heartbeats_fresh = ($activeWorkers.Count -eq 2 -and @($activeWorkers | Where-Object { $_.stale -or $_.heartbeat -lt $injection.started_at }).Count -eq 0)
    recovery_record_ready = ($recovery.ready -and $recovery.recovery_count -ge 1)
    real_business_counts_unchanged = $countsMatch
    no_official_knowledge_published = ($status.counts.heads -eq 0)
}
$passed = @($checks.Values | Where-Object { -not $_ }).Count -eq 0
$record = [ordered]@{captured_at=[DateTime]::UtcNow.ToString('o'); passed=$passed; checks=$checks;
    task_name=$registration.task_name; mode=$registration.mode; recovery_interval_seconds=300; last_task_result=$info.LastTaskResult;
    injected_supervisor_pid=$injection.supervisor_pid; recovered_supervisor_pid=$state.supervisor_pid;
    injection_time=$injection.started_at; natural_task_run=$info.LastRunTime.ToUniversalTime().ToString('o'); recovery_completed=$lastRecoveredAt;
    port_checks=$portChecks; active_workers=$activeWorkers; counts=$status.counts; actual_logon_transition_tested=$false; shutdown_or_logged_out_collection_supported=$false;
    scope='Actual current-user scheduled-task registration and natural timed recovery after killing only the owned supervisor; no reboot/logoff performed, no policy publication.'}
$path = Join-Path $projectPath 'evidence\knowledge-update\startup-recovery.json'
[IO.File]::WriteAllText($path, ($record | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
[pscustomobject]@{Passed=$passed; CheckCount=$checks.Count; RecoveryCount=$recovery.recovery_count; ActualLogonTested=$false}
if (-not $passed) { throw '自动恢复检查未全部通过，请查看记录。' }
