param([switch]$FromRecovery)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$runtimePath = Join-Path $projectPath '.runtime\knowledge-update'
if (-not (Test-Path -LiteralPath (Join-Path $runtimePath 'phase.json'))) {
    throw 'Run scripts\prepare_knowledge.py first.'
}
if (Get-NetTCPConnection -LocalPort 8772 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8772 is already in use. Inspect the existing service before starting another.'
}
if ((Test-Path -LiteralPath (Join-Path $runtimePath 'knowledge-mcp-token.txt')) -and (Get-NetTCPConnection -LocalPort 8773 -State Listen -ErrorAction SilentlyContinue)) {
    throw 'Port 8773 is already in use. Inspect the existing MCP before starting another.'
}
$pausePath = Join-Path $runtimePath 'service-paused.json'
if ($FromRecovery -and (Test-Path -LiteralPath $pausePath)) { throw 'Automatic recovery is manually paused.' }
if (-not $FromRecovery -and (Test-Path -LiteralPath $pausePath)) { Remove-Item -LiteralPath $pausePath }
$argumentList = @('-m', 'gov_mcp.knowledge_service', '--runtime', ('"' + $runtimePath + '"'))
$process = Start-Process -FilePath $pythonPath -ArgumentList $argumentList -WorkingDirectory $projectPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'supervisor.stdout.log') -RedirectStandardError (Join-Path $runtimePath 'supervisor.stderr.log')
@{ supervisor_pid = $process.Id; started_at = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimePath 'launcher.json') -Encoding utf8
Start-Sleep -Seconds 2
$response = Invoke-RestMethod -Uri 'http://127.0.0.1:8772/api/status' -TimeoutSec 10
[pscustomobject]@{ SupervisorPid=$process.Id; Url='http://127.0.0.1:8772'; Mode=$response.mode; PublishedScopes=$response.counts.heads }
