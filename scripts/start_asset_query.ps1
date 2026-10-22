param([switch]$Classify)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
    & $taskPython scripts/check_native_ready.py --wait-seconds 30
    if ($LASTEXITCODE -ne 0) { throw 'Start the existing local Nexent platform before running this entry point.' }
    if (-not (Test-Path -LiteralPath '.runtime/preparation/run/embedding.json')) {
        & ./scripts/setup_topic3_preparation.ps1 -Classify:$Classify
        if ($LASTEXITCODE -ne 0) { throw 'Input preparation failed.' }
    }
    elseif ($Classify) {
        & $taskPython scripts/classify_topic3_data.py
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Classification needs review; parsed assets and retrieval remain available. See the classification summary.'
        }
    }
    & $taskPython scripts/prepare_asset_query.py
    if ($LASTEXITCODE -ne 0) { throw 'Snapshot preparation failed. Existing snapshots are immutable.' }
    docker image inspect ict-gov-blank-mcp --format '{{.Id}}' > $null
    if ($LASTEXITCODE -ne 0) {
        docker compose -f compose.mcp.yml build gov-blank-mcp
        if ($LASTEXITCODE -ne 0) { throw 'The shared MCP base image could not be built.' }
    }
    docker compose -f compose.asset-query.yml up -d govdocs-mcp
    if ($LASTEXITCODE -ne 0) { throw 'The independent asset MCP could not start.' }
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 15; $taskAttempt++) {
        try {
            $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8774/health' -TimeoutSec 3
            if ($taskHealth.status -eq 'UP') { $taskReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $taskReady) { throw 'The independent asset MCP is not healthy.' }
    & $taskPython scripts/provision_asset_query.py
    if ($LASTEXITCODE -ne 0) { throw 'Nexent configuration failed; see the sanitized stage information.' }
    & $taskPython scripts/export_asset_query.py
    if ($LASTEXITCODE -ne 0) { throw 'The actual Agent and Skill export was not verified.' }
    Write-Output 'Ready: open Nexent http://127.0.0.1:3000 and select the asset query Agent. Source viewer: http://127.0.0.1:8774'
} finally {
    Pop-Location
}
