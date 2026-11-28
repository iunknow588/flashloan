
# Start the mainnet runtime (chain 4663) with the environment the
# self-healing orchestrator gives it.
#
# The orchestrator starts the runtime itself, but a runtime that was stopped
# by hand has to come back with the same environment, or it runs a different
# profile than the one the orchestrator assumes.  The environment below is the
# one `mainnet_arbitrage_healer.runtime_environment` builds.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$runtime = Join-Path $repo 'flashloan\app_runtime\run.py'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$logDir = Join-Path $repo 'plan\runtime_evidence\diagnostics\mainnet_self_healing_orchestrator'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path -LiteralPath $runtime)) { throw "runtime entry point is missing: $runtime" }

$env:FLASHLOAN_RUNTIME_PROFILE = 'v4_dex'
$env:DEX_ACTIVE_CHAIN_ID = '4663'
$env:DEX_ACTIVE_NETWORK = 'robinhood'
$env:DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN = '0'
$env:PORT = '5000'
$env:PYTHONUNBUFFERED = '1'

$stdout = Join-Path $logDir ("runtime_{0}.stdout.log" -f $stamp)
$stderr = Join-Path $logDir ("runtime_{0}.stderr.log" -f $stamp)
$process = Start-Process -FilePath 'python' -WorkingDirectory $repo -WindowStyle Hidden `
    -ArgumentList @($runtime, '--port', '5000') -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr -PassThru
Write-Output ("runtime started pid={0} stdout={1} stderr={2}" -f $process.Id, $stdout, $stderr)

