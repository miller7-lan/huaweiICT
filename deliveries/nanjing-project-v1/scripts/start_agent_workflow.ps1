param([int]$Port = 8780)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
& "$taskRoot/.venv/Scripts/python.exe" -m gov_mcp.decision.server --port $Port
exit $LASTEXITCODE
