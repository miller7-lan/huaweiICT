param([switch]$Unregister, [switch]$StartNow)
$ErrorActionPreference = 'Stop'
$projectPath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimePath = Join-Path $projectPath '.runtime\knowledge-update'
$ensurePath = Join-Path $PSScriptRoot 'ensure_knowledge_update.ps1'
$marker = Get-Content -LiteralPath (Join-Path $runtimePath 'phase.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($marker.phase -ne 'knowledge_update' -or $marker.schema_version -ne 1) { throw '独立知识库目录尚未准备好。' }
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($runtimePath)))).Replace('-', '').Substring(0, 16) }
finally { $sha.Dispose() }
$taskName = 'HuaweiICT-KnowledgeUpdate-' + $key
$shellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $ensurePath + '"'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and (@($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $shellPath -or $existing.Actions[0].Arguments -ne $arguments)) {
    throw '同名计划任务不属于此恢复脚本，拒绝覆盖或删除。'
}
$statusPath = Join-Path $runtimePath 'startup-registration.json'
$utf8 = New-Object Text.UTF8Encoding($false)
if ($Unregister) {
    if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
    [IO.File]::WriteAllText($statusPath, (@{installed=$false; task_name=$taskName; checked_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); scope='current_user'} | ConvertTo-Json), $utf8)
    Write-Output '已取消本项目的当前用户登录恢复任务。'
    exit 0
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute $shellPath -Argument $arguments -WorkingDirectory $projectPath
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$periodicTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($loginTrigger,$periodicTrigger) -Principal $principal -Settings $settings -Description 'Huawei ICT isolated knowledge update recovery: current-user logon and bounded five-minute checks; no automatic publication.' -Force | Out-Null
    $actual = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction Stop
    [IO.File]::WriteAllText((Join-Path $runtimePath 'startup-task.xml'), (Export-ScheduledTask -TaskName $taskName), $utf8)
    $record = [ordered]@{installed=$true; task_name=$taskName; mode='windows_current_user_scheduled_task'; checked_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();
        current_user_only=$true; interactive_logon_required=$true; run_level=[string]$actual.Principal.RunLevel;
        trigger_count=@($actual.Triggers).Count; recovery_interval_seconds=300; task_state=[string]$actual.State;
        next_run_time=$info.NextRunTime.ToUniversalTime().ToString('o'); actual_logon_transition_tested=$false; automatic_publication=$false}
    [IO.File]::WriteAllText($statusPath, ($record | ConvertTo-Json -Depth 6), $utf8)
    if ($StartNow) { Start-ScheduledTask -TaskName $taskName }
    [pscustomobject]$record
} catch {
    $record = @{installed=$false; task_name=$taskName; mode='windows_current_user_scheduled_task'; checked_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();
        error_type=$_.Exception.GetType().Name; error=$_.Exception.Message; automatic_publication=$false}
    [IO.File]::WriteAllText($statusPath, ($record | ConvertTo-Json -Depth 6), $utf8)
    throw
}
