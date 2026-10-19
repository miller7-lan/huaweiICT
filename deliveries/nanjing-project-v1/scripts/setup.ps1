param([string]$PythonCommand = 'py', [string]$PythonVersion = '-3.12')
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $TaskRoot
$Git = (Get-Command git -ErrorAction Stop).Source
$NexentPath = Join-Path $TaskRoot 'vendor/nexent'
$ExpectedCommit = '519b9b17c7f4cbc623256ac318ea96a20f33772e'
if (-not (Test-Path -LiteralPath $NexentPath)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $TaskRoot 'vendor') | Out-Null
    & $Git clone --depth 1 --branch v2.7.0 https://github.com/ModelEngine-Group/nexent.git $NexentPath
    if ($LASTEXITCODE -ne 0) { throw 'Pinned Nexent source download failed.' }
}
$ActualCommit = & $Git -C $NexentPath rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $ActualCommit -ne $ExpectedCommit) {
    throw 'Existing Nexent checkout does not match v2.7.0; inspect it before continuing.'
}
$Python = Join-Path $TaskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $VersionArgs = @()
    if ($PythonVersion) { $VersionArgs += $PythonVersion }
    & $PythonCommand @VersionArgs -m venv (Join-Path $TaskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed. Install Python 3.12 or select an existing Python 3.11+ executable.' }
}
& $Python -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
& $Python -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Local package installation failed.' }
& $Python scripts/private_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Private runtime directory setup failed.' }
Write-Output 'Pinned source and local Python environment are ready. Follow README.md for deployment.'
