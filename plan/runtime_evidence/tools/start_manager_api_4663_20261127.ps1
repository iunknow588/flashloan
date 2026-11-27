# Start the Manager API for the mainnet (chain 4663) self-healing orchestrator.
#
# The healer reads the closed-loop catalog, the review library and the
# broadcast attempts through this service.  Without it the cascade has no
# authoritative catalog and every repair silently degrades to a no-op.
#
# The binary is the release build in flashloan/tools/target/release.  The
# module authorities are set to `manager` because the healer writes through
# this service (POST /v1/reviews/run, /v1/tokens/resolve, /v1/broadcasts/*)
# and every `dryRun=false` request fails closed under `legacy`.

$ErrorActionPreference = 'Stop'
# The repository root is derived from this script's own location so the file
# stays pure ASCII.  A hard-coded CJK path is read as ANSI by Windows
# PowerShell 5.1 and the path silently becomes garbage.
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$binary = Join-Path $repo 'flashloan\tools\target\release\manager-api.exe'
$database = Join-Path $repo 'flashloan\db\local_store_4663.sqlite3'
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_sup'

if (-not (Test-Path -LiteralPath $binary)) { throw "manager-api binary is missing: $binary" }
if (-not (Test-Path -LiteralPath $database)) { throw "manager database is missing: $database" }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$env:MANAGER_DB_PATH = $database
$env:MANAGER_API_BIND = '127.0.0.1:8091'
$env:TOKEN_MANAGER_AUTHORITY = 'manager'
$env:CANDIDATE_MANAGER_AUTHORITY = 'manager'
$env:CANDIDATE_EXCHANGE_MANAGER_AUTHORITY = 'manager'
$env:CLOSED_LOOP_MANAGER_AUTHORITY = 'manager'
$env:REVIEW_MANAGER_AUTHORITY = 'manager'
$env:BROADCAST_MANAGER_AUTHORITY = 'manager'
$env:COMPOSITE_MANAGER_AUTHORITY = 'manager'
$env:DIRECTIONAL_POOL_MANAGER_AUTHORITY = 'manager'
# A closed loop is measured once a minute, so the review that the broadcast
# gate reads is never older than the two minutes the gate accepts.  The
# value is the store's default; it is named here so the cadence of the
# running system is visible in one place.
$env:CLOSED_LOOP_REVIEW_INTERVAL_SECONDS = '60'

$stdout = Join-Path $logDir 'manager_api_stdout_20261127.log'
$stderr = Join-Path $logDir 'manager_api_stderr_20261127.log'
$process = Start-Process -FilePath $binary -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Output ("manager-api started pid={0} stdout={1} stderr={2}" -f $process.Id, $stdout, $stderr)
