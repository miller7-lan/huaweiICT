param([switch]$SkipModelDownload, [switch]$Classify)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $taskBasePython = Join-Path $taskRoot '.venv/Scripts/python.exe'
    $taskPreparationPython = Join-Path $taskRoot '.runtime/preparation/.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $taskBasePython)) { throw 'Prepare the project Python environment first.' }
    if (-not (Test-Path -LiteralPath $taskPreparationPython)) {
        & $taskBasePython -m venv .runtime/preparation/.venv
        if ($LASTEXITCODE -ne 0) { throw 'Preparation virtual environment failed.' }
    }
    & $taskPreparationPython -m pip install --disable-pip-version-check --index-url https://pypi.org/simple -r config/preparation/requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Preparation dependencies failed.' }
    & $taskPreparationPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Preparation dependency check failed.' }
    if (-not $SkipModelDownload) {
        ollama pull qwen3-embedding:0.6b
        if ($LASTEXITCODE -ne 0) { throw 'Embedding model download failed.' }
    }
    $taskPreparationArgs = @('scripts/prepare_topic3_data.py', '--generate-demo', '--embed')
    if ($Classify) { $taskPreparationArgs += '--classify' }
    & $taskPreparationPython @taskPreparationArgs
    if ($LASTEXITCODE -ne 0) { throw 'Input preparation rehearsal failed; see sanitized summary.' }
    if (Test-Path -LiteralPath '.runtime/accounts.json') {
        & $taskBasePython scripts/provision_topic3_embedding.py
        if ($LASTEXITCODE -ne 0) { throw 'Native embedding registration failed; see sanitized summary.' }
    } else {
        Write-Output 'Local input preparation passed; native embedding registration needs an existing local Nexent tenant.'
    }
} finally {
    Pop-Location
}
