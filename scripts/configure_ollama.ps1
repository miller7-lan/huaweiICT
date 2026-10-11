$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $TaskRoot
& (Join-Path $TaskRoot '.venv/Scripts/python.exe') scripts/private_runtime.py
if ($LASTEXITCODE -ne 0) { throw 'Private runtime directory setup failed.' }
$OllamaHome = Join-Path $env:LOCALAPPDATA 'Programs/Ollama'
$Ollama = Join-Path $OllamaHome 'ollama.exe'
if (-not (Test-Path -LiteralPath $Ollama)) { throw 'Expected Windows Ollama installation was not found.' }
# Restart only processes from this known Ollama installation.
Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($OllamaHome, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_CONTEXT_LENGTH = '8192'
Start-Process -FilePath $Ollama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput .runtime/ollama-stdout.log -RedirectStandardError .runtime/ollama-stderr.log
$Ready = $false
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2; $Ready = $true; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $Ready) { throw 'Ollama did not start; inspect the private runtime logs.' }
& $Ollama create gov-qwen3-4b:ctx8192-v1 -f models/Modelfile.4b
if ($LASTEXITCODE -ne 0) { throw '4B model creation failed. The pinned base model must finish downloading.' }
& $Ollama create gov-qwen3-8b:ctx8192-v1 -f models/Modelfile.8b
if ($LASTEXITCODE -ne 0) { throw '8B model creation failed. The pinned base model must finish downloading.' }
Write-Output 'Ollama configured: parallel=1, max loaded=1, localhost, pinned 8192 context aliases.'
