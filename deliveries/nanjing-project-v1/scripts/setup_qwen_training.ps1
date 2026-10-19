param([switch]$SkipTorch, [switch]$DirectNetwork)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if ($DirectNetwork) {
    # Applies only to this setup process and its children, never system settings.
    Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY -ErrorAction SilentlyContinue
}
$trainingPython = Join-Path $taskRoot '.runtime/training/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $trainingPython)) {
    & ./.venv/Scripts/python.exe -m venv .runtime/training/.venv
    if ($LASTEXITCODE -ne 0) { throw 'Training virtual environment creation failed' }
}
if (-not $SkipTorch) {
    & $trainingPython -m pip install --disable-pip-version-check --progress-bar off --timeout 20 --retries 1 torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
    if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed; production environment was not modified' }
}
& $trainingPython -m pip install --disable-pip-version-check --progress-bar off --timeout 20 --retries 1 -r config/training/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Training dependency installation failed' }
& $trainingPython -m pip freeze | Set-Content -LiteralPath .runtime/training/installed-requirements.txt -Encoding utf8
& ./.venv/Scripts/python.exe scripts/prepare_qwen_training.py
if ($LASTEXITCODE -ne 0) { throw 'Training data preparation failed' }
