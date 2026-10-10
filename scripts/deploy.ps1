param([ValidateSet('general','mainland')][string]$RegistryProfile = 'general')
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $TaskRoot
$Python = Join-Path $TaskRoot '.venv/Scripts/python.exe'
$Git = 'C:/Program Files/Git/cmd/git.exe'
$Bash = 'C:/Program Files/Git/bin/bash.exe'
$DockerCandidates = @("$env:LOCALAPPDATA/Programs/DockerDesktop/resources/bin/docker.exe", 'C:/Program Files/Docker/Docker/resources/bin/docker.exe')
$Docker = $DockerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Docker) { throw 'Docker Desktop is not installed. Install it after enabling WSL 2.' }
& $Python scripts/check_docker.py $Docker
if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable. WSL 2 and Docker Desktop must be running.' }
$env:PATH = "$(Split-Path -Parent $Docker);$env:PATH"
$PinnedCommit = & $Git -C vendor/nexent rev-parse HEAD
if ($PinnedCommit -ne '519b9b17c7f4cbc623256ac318ea96a20f33772e') { throw 'Nexent checkout does not match the pinned v2.7.0 commit.' }
& $Python scripts/prepare_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Runtime preparation failed.' }
& $Python scripts/prepare_deploy.py
if ($LASTEXITCODE -ne 0) { throw 'Deployment preparation failed.' }
$DataRoot = (Join-Path $TaskRoot '.runtime/nexent-data').Replace('\','/')
$DataRoot = '/' + $DataRoot.Substring(0,1).ToLower() + $DataRoot.Substring(2)
Push-Location vendor/nexent
try {
    & $Bash deploy.sh --defaults docker --version v2.7.0 --components infrastructure,application,supabase --port-policy development --image-source registry --registry-profile $RegistryProfile --sandbox-mode lightweight --root-dir $DataRoot *> (Join-Path $TaskRoot '.runtime/nexent-deploy.log')
    if ($LASTEXITCODE -ne 0) { throw 'Official Nexent deployment failed; inspect the private .runtime/nexent-deploy.log.' }
} finally { Pop-Location }
& $Docker compose -f compose.mcp.yml up -d --build
if ($LASTEXITCODE -ne 0) { throw 'MCP container startup failed.' }
& $Python scripts/provision_nexent.py
if ($LASTEXITCODE -ne 0) { throw 'Native provisioning failed; no acceptance claim can be made.' }
Write-Output 'Native Nexent is provisioned at http://127.0.0.1:3000. Run the benchmark and native UI checks.'
