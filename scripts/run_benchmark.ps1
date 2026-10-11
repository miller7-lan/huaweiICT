param([switch]$WarmupOnly)

$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $TaskRoot
$Python = Join-Path $TaskRoot '.venv/Scripts/python.exe'
$StatePath = Join-Path $TaskRoot '.runtime/nexent-state.json'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Project Python is missing; run scripts/setup.ps1 first.' }
if (-not (Test-Path -LiteralPath $StatePath)) { throw 'Native state is missing; run scripts/initialize_accounts.ps1 first.' }
$NativeState = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($RoleName in @('tenant_admin', 'developer', 'citizen', 'staff')) {
    if ($NativeState.accounts.PSObject.Properties.Name -notcontains $RoleName) { throw 'Test accounts are incomplete; run scripts/initialize_accounts.ps1 first.' }
}
foreach ($AgentName in @('gov_coordinator', 'citizen_assistant', 'staff_assistant')) {
    if ($NativeState.agents.PSObject.Properties.Name -notcontains $AgentName) { throw 'Native agents are incomplete; run scripts/initialize_accounts.ps1 first.' }
}
foreach ($ModelName in @('gov-qwen3-4b:ctx8192-v1', 'gov-qwen3-8b:ctx8192-v1')) {
    if ($NativeState.models.PSObject.Properties.Name -notcontains $ModelName) { throw 'Native models are incomplete; run scripts/initialize_accounts.ps1 first.' }
}
& $Python scripts/runtime_sandbox.py
if ($LASTEXITCODE -ne 0) { throw 'Runtime sandbox recovery failed; no benchmark login was attempted.' }
& $Python scripts/check_native_ready.py
if ($LASTEXITCODE -ne 0) { throw 'Native services are not ready; no benchmark login was attempted.' }
if ($WarmupOnly) {
    Write-Output 'Authorized native warmups: sign in with isolated test accounts, publish model versions, and run two warmups only. Pause web conversations until completion.'
} else {
    Write-Output 'Authorized benchmark: sign in with isolated test accounts, publish model versions, and run 40 native core cases. Pause web conversations until completion.'
}
$BenchmarkArguments = @('scripts/benchmark_native.py')
if ($WarmupOnly) { $BenchmarkArguments += '--warmup-only' }
& $Python @BenchmarkArguments
$BenchmarkExitCode = $LASTEXITCODE
& $Python scripts/runtime_sandbox.py
if ($LASTEXITCODE -ne 0) { throw 'Runtime sandbox persistence failed.' }
if ($BenchmarkExitCode -ne 0) {
    if ($WarmupOnly) { throw 'Native warmup checks failed; keep the terminal output and evidence/native-* results for investigation.' }
    throw 'Native benchmark failed or no model passed; keep the terminal output and evidence/native-* results for investigation.'
}
if ($WarmupOnly) {
    Write-Output 'Both warmups passed. Formal core sampling, model selection, UI and fault acceptance still require the full benchmark.'
} else {
    Write-Output 'Core benchmark accepted. Native UI and fault acceptance are still separate required steps.'
}
