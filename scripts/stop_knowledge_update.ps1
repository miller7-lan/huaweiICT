param([switch]$ForRestart)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$runtimePath = [IO.Path]::GetFullPath((Join-Path $projectPath '.runtime\knowledge-update'))
$state = Get-Content -LiteralPath (Join-Path $runtimePath 'service-state.json') -Raw | ConvertFrom-Json
$ownedIds = @($state.supervisor_pid) + @($state.processes.PSObject.Properties | ForEach-Object { $_.Value })
$verified = @()
foreach ($ownedId in $ownedIds) {
    $candidateProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ownedId" -ErrorAction SilentlyContinue
    if ($candidateProcess) {
        $ownedCommand = $candidateProcess.CommandLine
        if ($ownedCommand -notlike '*gov_mcp.knowledge_*' -or $ownedCommand -notlike ('*' + $runtimePath + '*')) {
            throw "Process $ownedId no longer matches this runtime; refusing to stop it."
        }
        $verified += $candidateProcess
    }
}
if (-not $ForRestart) {
    $pause = @{paused=$true; reason='manual_service_stop'; set_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()}
    [IO.File]::WriteAllText((Join-Path $runtimePath 'service-paused.json'), ($pause | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
}
foreach ($candidateProcess in $verified) {
    $fresh = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$candidateProcess.ProcessId) -ErrorAction SilentlyContinue
    if ($fresh -and $fresh.CreationDate -eq $candidateProcess.CreationDate -and $fresh.CommandLine -eq $candidateProcess.CommandLine) { Stop-Process -Id $fresh.ProcessId }
}
Write-Output 'Stopped this runtime supervisor and its owned child processes.'
