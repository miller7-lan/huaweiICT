$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $TaskRoot
$Python = Join-Path $TaskRoot '.venv/Scripts/python.exe'
$Git = (Get-Command git -ErrorAction Stop).Source
$Bash = Join-Path (Split-Path (Split-Path $Git -Parent) -Parent) 'bin/bash.exe'
if (-not (Test-Path -LiteralPath $Bash)) { throw 'Git for Windows Bash was not found beside git.exe.' }
$DockerCandidates = @("$env:LOCALAPPDATA/Programs/DockerDesktop/resources/bin/docker.exe", 'C:/Program Files/Docker/Docker/resources/bin/docker.exe')
$Docker = $DockerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Docker) { throw 'Docker executable not found.' }
$env:PATH = "$(Split-Path -Parent $Docker);$env:PATH"
& $Python scripts/check_native_ready.py
if ($LASTEXITCODE -ne 0) { throw 'Native services are not ready; no account operation was attempted.' }
Write-Output 'User-started operation: initialize the local super administrator, sign in through the API, and create one administrator invitation.'
& $Bash scripts/initialize_super_admin.sh
if ($LASTEXITCODE -ne 0) { throw 'Platform administrator initialization failed.' }
& $Python scripts/create_admin_invite.py
if ($LASTEXITCODE -ne 0) { throw 'Invitation creation failed.' }
