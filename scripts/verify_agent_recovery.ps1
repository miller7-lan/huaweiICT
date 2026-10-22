param([ValidatePattern('^[A-Za-z0-9-]+\.json$')][string]$EvidenceName='live-result.json')
# Explicit process-fault validation; never called by the recurring recovery task.
$ErrorActionPreference='Stop'
$projectPath=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimePath=Join-Path $projectPath '.runtime\agent-workflow'
$ensurePath=Join-Path $PSScriptRoot 'ensure_agent_workflow.ps1'
$outputPath=Join-Path (Join-Path $projectPath 'evidence\agent-recovery-20261021') $EvidenceName
if (Test-Path -LiteralPath $outputPath) { throw 'EXISTING_RECOVERY_EVIDENCE_REFUSE_OVERWRITE' }
function Observe([string]$mode='Check') { return (& $ensurePath -Action $mode | ConvertFrom-Json) }
function Listeners { return @(Get-NetTCPConnection -LocalPort 8780 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique) }
function Stop-Owned {
    $owned=@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match '-m gov_mcp\.decision\.server ' -and $_.CommandLine.Contains($runtimePath) })
    $ports=@(Listeners)
    if (-not $ports.Count -or @($ports | Where-Object { $_ -notin $owned.ProcessId }).Count) { throw 'UNOWNED_RECOVERY_LISTENER' }
    foreach ($candidate in $owned) {
        $fresh=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $candidate.ProcessId) -ErrorAction SilentlyContinue
        if ($fresh -and $fresh.CreationDate -eq $candidate.CreationDate -and $fresh.CommandLine -eq $candidate.CommandLine) {
            Stop-Process -Id $fresh.ProcessId -ErrorAction SilentlyContinue
        }
    }
}
$before=Observe
if (-not $before.ready -or -not $before.task_installed) { throw 'REGISTERED_HEALTHY_WORKFLOW_REQUIRED' }
$record=[ordered]@{status='FAILED';before=$before;actual_os_logon_transition_tested=$false;model_calls=0;publication=$false}
try {
    $first=Listeners
    $ignored=Observe 'Ensure'
    $record.duplicate_prevented=($first -join ',') -eq ((Listeners) -join ',')
    $ignored=Observe 'Pause'
    Stop-Owned
    $paused=Observe 'Ensure'
    $record.manual_pause_prevented_restart=(-not $paused.ready -and $paused.recovery_paused -and -not (Listeners).Count)
    $resumed=Observe 'Resume'
    $record.resume_restored=$resumed.ready
    Stop-Owned
    $started=Get-Date
    Start-ScheduledTask -TaskName $before.task_name
    $after=$null
    for ($index=0; $index -lt 40; $index++) {
        Start-Sleep -Seconds 1
        try { $after=Observe; if ($after.ready) { break } } catch {}
    }
    $record.after=$after
    $record.recovery_elapsed_seconds=((Get-Date)-$started).TotalSeconds
    $record.scheduled_task_restored_listener=[bool]$after.ready
    $record.head_unchanged=$before.head -eq $after.head
    $record.version_count_unchanged=$before.version_count -eq $after.version_count
    $record.business_remained_paused=$after.business_paused
    for ($index=0; $index -lt 20; $index++) {
        if ([string](Get-ScheduledTask -TaskName $before.task_name).State -ne 'Running') { break }
        Start-Sleep -Seconds 1
    }
    $task=Get-ScheduledTask -TaskName $before.task_name
    $taskInfo=Get-ScheduledTaskInfo -TaskName $before.task_name
    $record.scheduled_task_exit_zero=([string]$task.State -eq 'Ready' -and $taskInfo.LastTaskResult -eq 0)
    $record.task_trigger_types=@($task.Triggers | ForEach-Object {$_.CimClass.CimClassName})
    $checks=@('duplicate_prevented','manual_pause_prevented_restart','resume_restored','scheduled_task_restored_listener','head_unchanged','version_count_unchanged','business_remained_paused','scheduled_task_exit_zero')
    if (@($checks | Where-Object { $record[$_] -ne $true }).Count) { throw 'RECOVERY_ACCEPTANCE_FAILED' }
    $record.status='PASS_LOCAL_PROCESS_RECOVERY'
} finally {
    if ($record.status -ne 'PASS_LOCAL_PROCESS_RECOVERY') { $ignored=Observe 'Resume' }
    [IO.File]::WriteAllText($outputPath, ($record | ConvertTo-Json -Depth 7), (New-Object Text.UTF8Encoding($false)))
}
$record | ConvertTo-Json -Depth 7 -Compress
