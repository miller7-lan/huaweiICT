param([Parameter(Mandatory=$true)][string]$RuntimePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
try {
    $resolvedRuntime = [IO.Path]::GetFullPath($RuntimePath)
    $projectPath = Split-Path -Parent (Split-Path -Parent $resolvedRuntime)
    $marker = Get-Content -LiteralPath (Join-Path $resolvedRuntime 'phase.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($marker.phase -ne 'knowledge_update' -or $marker.schema_version -ne 1) { throw 'Invalid runtime marker' }
    $registration = Get-Content -LiteralPath (Join-Path $resolvedRuntime 'startup-registration.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($resolvedRuntime)))).Replace('-', '').Substring(0, 16) }
    finally { $sha.Dispose() }
    $expectedName = 'HuaweiICT-KnowledgeUpdate-' + $key
    if ($registration.task_name -ne $expectedName) { throw 'Registration ownership mismatch' }
    try { $task = Get-ScheduledTask -TaskName $expectedName -ErrorAction Stop }
    catch {
        if ($_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound_TaskName,*') {
            @{status='missing'; task_name=$expectedName} | ConvertTo-Json -Compress
            exit 0
        }
        throw
    }
    $info = Get-ScheduledTaskInfo -TaskName $expectedName -ErrorAction Stop
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $taskSid = if ($task.Principal.UserId.StartsWith('S-1-')) { $task.Principal.UserId } else {
        (New-Object Security.Principal.NTAccount($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $expectedShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $expectedArguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $projectPath 'scripts\ensure_knowledge_update.ps1') + '"'
    $owned = @($task.Actions).Count -eq 1 -and $task.Actions[0].Execute -eq $expectedShell -and $task.Actions[0].Arguments -eq $expectedArguments -and
        $task.Actions[0].WorkingDirectory -eq $projectPath -and $taskSid -eq $identity.User.Value -and [string]$task.Principal.RunLevel -eq 'Limited' -and [string]$task.Principal.LogonType -eq 'Interactive'
    $login = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled })
    $periodic = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskTimeTrigger' -and $_.Enabled -and $_.Repetition.Interval -eq 'PT5M' -and -not $_.EndBoundary -and -not $_.Repetition.Duration })
    $loginUserMatches = $false
    if ($login.Count -eq 1 -and $login[0].UserId) {
        $loginSid = if ($login[0].UserId.StartsWith('S-1-')) { $login[0].UserId } else {
            (New-Object Security.Principal.NTAccount($login[0].UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
        }
        $loginUserMatches = $loginSid -eq $identity.User.Value
    }
    [ordered]@{ status='observed'; task_name=$expectedName; enabled=[bool]$task.Settings.Enabled; task_state=[string]$task.State;
        ownership_verified=[bool]$owned; configuration_matches=(@($task.Triggers).Count -eq 2 -and $loginUserMatches -and $periodic.Count -eq 1);
        last_run=$info.LastRunTime.ToUniversalTime().ToString('o'); next_run=$info.NextRunTime.ToUniversalTime().ToString('o');
        last_result=$info.LastTaskResult; missed_runs=$info.NumberOfMissedRuns } | ConvertTo-Json -Compress
} catch {
    @{status='unavailable'; error_kind=$_.Exception.GetType().Name} | ConvertTo-Json -Compress
}
