param([switch]$Demo, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
    & $taskPython scripts/check_native_ready.py --wait-seconds 30
    if ($LASTEXITCODE -ne 0) { throw 'Start the existing Nexent platform first.' }
    $taskPrepare = @('scripts/prepare_knowledge.py')
    $taskProvision = @('scripts/provision_knowledge.py')
    if ($Demo) { $taskPrepare += '--demo'; $taskProvision += '--demo' }
    & $taskPython @taskPrepare
    if ($LASTEXITCODE -ne 0) { throw 'Maintenance runtime preparation failed.' }
    docker image inspect ict-gov-knowledge --format '{{.Id}}' > $null
    if ($LASTEXITCODE -ne 0) {
        if ($SkipBuild) { throw 'Knowledge image is missing.' }
        docker image inspect ict-gov-blank-mcp --format '{{.Id}}' > $null
        if ($LASTEXITCODE -ne 0) {
            docker compose -f compose.mcp.yml build gov-blank-mcp
            if ($LASTEXITCODE -ne 0) { throw 'The shared MCP base image could not be built.' }
        }
        docker build -f Dockerfile.knowledge -t ict-gov-knowledge .
        if ($LASTEXITCODE -ne 0) { throw 'Knowledge image build failed.' }
    }
    $taskService = if ($Demo) { 'govkb-demo' } else { 'govkb-maintenance' }
    $taskPort = if ($Demo) { 8778 } else { 8775 }
    $taskCompose = @('compose', '-f', 'compose.knowledge.yml')
    if ($Demo) { $taskCompose += @('--profile', 'demo') }
    $taskCompose += @('up', '-d', '--force-recreate', $taskService)
    & docker @taskCompose
    if ($LASTEXITCODE -ne 0) { throw 'Knowledge service failed to start.' }
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
        try {
            $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$taskPort/health" -TimeoutSec 2
            if ($taskHealth.status -eq 'UP') { $taskReady = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $taskReady) { throw 'Knowledge service did not become ready.' }
    & $taskPython @taskProvision
    if ($LASTEXITCODE -ne 0) { throw 'Independent native maintenance Agent provisioning failed.' }
    Write-Output "Knowledge workbench ready: http://127.0.0.1:$taskPort . Official sources remain empty until configured."
} finally { Pop-Location }
