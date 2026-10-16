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
$deadline = [DateTime]::UtcNow.AddSeconds(45)
$ready = $false
$lastReadinessError = 'Services have not responded yet.'
$mcpRequired = Test-Path -LiteralPath (Join-Path $runtimePath 'knowledge-mcp-token.txt')
do {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8772/api/status' -TimeoutSec 2
        if ($response.mode -ne 'knowledge_update') { throw 'Unexpected web service mode.' }
        foreach ($identity in @('knowledge-worker', 'knowledge-maintenance')) {
            $worker = @($response.workers | Where-Object id -EQ $identity)
            if (-not $worker.Count -or $worker[0].stale) { throw ('Waiting for heartbeat: ' + $identity) }
        }
        if ($mcpRequired) {
            $mcpHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8773/health' -TimeoutSec 2
            if ($mcpHealth.status -ne 'UP' -or -not $mcpHealth.read_only) { throw 'Waiting for read-only MCP.' }
        }
        $ready = $true
    } catch { $lastReadinessError = $_.Exception.Message }
    if (-not $ready -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 1 }
} while (-not $ready -and [DateTime]::UtcNow -lt $deadline)
if (-not $ready) {
    throw ('Readiness was not confirmed within 45 seconds. The launched process may still be running; inspect it and supervisor logs before starting another. Launcher PID: ' + $process.Id + '. Last check: ' + $lastReadinessError)
}
[pscustomobject]@{ LauncherPid=$process.Id; Url='http://127.0.0.1:8772'; Mode=$response.mode; PublishedScopes=$response.counts.heads; ReadinessConfirmed=$true }
