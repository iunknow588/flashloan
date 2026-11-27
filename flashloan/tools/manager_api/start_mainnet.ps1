# Start the Manager API against the mainnet store.
#
# The Manager API is the **authoritative** interface of the manager
# libraries: the runtime reads the closed loops, the reviews and the
# broadcast attempts through it, and the healer writes the review
# evidence back through it.  Its configuration is therefore deployment
# configuration, and this script is the one place that spells it out:
#
# * ``MANAGER_DB_PATH``  -- the store the API serves.  A path that does
#   not exist is **not** a default: the API then has no library at all
#   and every read is refused.
# * the eight ``*_AUTHORITY`` variables -- who may write.  The default is
#   ``legacy``, which refuses every write.  The mainnet deployment runs
#   the manager as the authority of every layer, so the healer and the
#   runtime may materialize reviews and submit broadcasts.
# * ``BROADCAST_GUARD_*`` -- the guardrails of the broadcast.  They stay
#   **absent** unless a deployment names them: an absent guardrail is a
#   refusal, not a passing one, so the broadcast layer refuses every
#   submission with a recorded reason instead of guessing.
#
# Usage::
#
#     powershell -File start_mainnet.ps1
#     powershell -File start_mainnet.ps1 -Database D:\other.sqlite3

param(
    [string]$Database = "",
    [string]$Bind = "127.0.0.1:8091"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $here "..\..\..")
if ([string]::IsNullOrWhiteSpace($Database)) {
    # The store is derived from the **place of this script**, so the
    # script carries no absolute path and works in every checkout.
    $Database = Join-Path $repoRoot "flashloan\db\local_store_4663.sqlite3"
}
$binary = Join-Path $here "..\target\release\manager-api.exe"
if (-not (Test-Path -LiteralPath $binary)) {
    throw "manager-api.exe is missing; run: cargo build --release -p manager-api"
}
if (-not (Test-Path -LiteralPath $Database)) {
    throw "the store $Database does not exist; the API would serve nothing"
}

$env:MANAGER_DB_PATH = $Database
$env:MANAGER_API_BIND = $Bind
foreach ($name in @(
    "TOKEN_MANAGER_AUTHORITY",
    "CANDIDATE_MANAGER_AUTHORITY",
    "CANDIDATE_EXCHANGE_MANAGER_AUTHORITY",
    "CLOSED_LOOP_MANAGER_AUTHORITY",
    "REVIEW_MANAGER_AUTHORITY",
    "BROADCAST_MANAGER_AUTHORITY",
    "COMPOSITE_MANAGER_AUTHORITY",
    "DIRECTIONAL_POOL_MANAGER_AUTHORITY")) {
    Set-Item -Path "env:$name" -Value "manager"
}

Write-Output "manager-api: store=$Database bind=$Bind"
& $binary
