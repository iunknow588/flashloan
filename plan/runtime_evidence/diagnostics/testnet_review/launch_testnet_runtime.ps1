# Launch the Robinhood testnet runtime with a bounded RPC footprint.
#
# The review worker count defaults to 16, which saturates a free public RPC
# and makes /healthz time out. The testnet review therefore runs with a
# small worker count and the paid RPC first, so the review path stays
# responsive while the broadcast path stays disabled.

$ErrorActionPreference = "Stop"
$repo = "E:\2026OPC大赛\flashLoan"
$logDir = Join-Path $repo "plan\runtime_evidence\diagnostics\testnet_review"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmss")
$stdout = Join-Path $logDir "runtime_capacity_$stamp.stdout.log"
$stderr = Join-Path $logDir "runtime_capacity_$stamp.stderr.log"

$env:DEX_ACTIVE_CHAIN_ID = "46630"
$env:DEX_ACTIVE_NETWORK = "robinhood-testnet"
$env:FLASHLOAN_RUNTIME_PROFILE = "v4_dex"
$env:DEX_CLOSED_LOOP_REVIEW_WORKERS = "2"
$env:PORT = "5000"

# The paid RPC has to come first: the public endpoint rate-limits a burst of
# extsload calls and the review worker then never completes.
$env:ROBINHOOD_TESTNET_RPCS = @(
  "https://robinhood-testnet.g.alchemy.com/v2/alch_X81ySO9Rq5_9fARKfxR_T",
  "https://rpc.testnet.chain.robinhood.com/rpc",
  "https://robinhood-sepolia-rpc.publicnode.com"
) -join ","

# Broadcast stays off: this is a review-only verification run.
$env:BROADCAST_MANAGER_AUTHORITY = "legacy"
$env:MANAGER_DB_PATH = "flashloan/db/local_store_46630.sqlite3"
$env:MANAGER_API_BASE = "http://127.0.0.1:8091"

Set-Location $repo
$process = Start-Process -FilePath "C:\ProgramData\Miniconda3\python.exe" `
  -ArgumentList "flashloan\app_runtime\run.py" `
  -WorkingDirectory $repo `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -WindowStyle Hidden `
  -PassThru

$record = [ordered]@{
  Pid = $process.Id
  BroadcastEnabled = $false
  NativeRust = $true
  Database = (Join-Path $repo "flashloan\db\local_store_46630.sqlite3")
  Stdout = $stdout
  Stderr = $stderr
  ChainId = 46630
  Network = "robinhood-testnet"
  ReviewWorkers = 2
}
$record | ConvertTo-Json | Set-Content -Path (Join-Path $logDir "active_runtime.json") -Encoding UTF8
Write-Output "pid=$($process.Id) stdout=$stdout stderr=$stderr"
