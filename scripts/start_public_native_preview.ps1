param([switch]$Live)
$ErrorActionPreference = 'Stop'
$previewRoot = Split-Path $PSScriptRoot -Parent
$previewPython = Join-Path $previewRoot '.venv\Scripts\python.exe'
$previewRuntime = Join-Path $previewRoot '.runtime\public-native-preview'
if (!(Test-Path -LiteralPath (Join-Path $previewRuntime 'manifest.json'))) { throw 'Run prepare_public_native_preview.py --prepare first.' }
if (Get-NetTCPConnection -LocalPort 8781 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8781 is occupied; the existing service was preserved.' }
$env:PYTHONPATH = Join-Path $previewRoot 'src'
$previewArgs = @('-m', 'gov_mcp.public_preview_server', '--runtime', ('"' + $previewRuntime + '"'), '--port', '8781')
if ($Live) { $previewArgs += '--live' }
$previewProcess = Start-Process -FilePath $previewPython -ArgumentList $previewArgs -WorkingDirectory $previewRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $previewRuntime 'server.stdout.log') -RedirectStandardError (Join-Path $previewRuntime 'server.stderr.log')
@{ pid=$previewProcess.Id; port=8781; live=[bool]$Live; automatic_startup=$false } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $previewRuntime 'server-process.json') -Encoding utf8
Write-Output ('Public preview process started: ' + $previewProcess.Id)
