# Deploy the review status semantics that tell a measured loss apart from a
# route that could not be traded.
#
# The change lives in the **review manager** crate, and three artefacts carry
# it:
#
#   * the manager-api binary -- it owns the review library and it is the
#     process that materialises `manager_v4_reviews` (the runtime only
#     measures and publishes the evidence),
#   * the runtime extension module -- the runtime calls
#     `review_manager::run_reviews_write` **in process** when it publishes a
#     measurement, so the module carries the review manager with it.  A
#     module is loaded into the process when the process starts, so a running
#     runtime keeps the old classification in memory: the runtime has to be
#     started again to pick the new module up,
#   * the healer -- the orchestrator that reports the verdicts, and it
#     carries the new `statusCounts` reading.
#
# The build of the module comes **after** the runtime stopped, because the
# running runtime holds the old module open and the linker cannot replace a
# file that is in use.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path

# 1. The healer stops first: it supervises the runtime and it would start a
#    new one on its own.
$healers = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'mainnet_arbitrage_healer\.py' -and $_.ProcessId -ne $PID }
foreach ($item in $healers) {
    Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Output ("healer stopped pid={0}" -f $item.ProcessId)
}
if ($healers) { Start-Sleep -Seconds 3 }

# 2. The runtime stops second: it has to load the new extension module.
$runtimes = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'app_runtime[\\/]run\.py' -and $_.ProcessId -ne $PID }
foreach ($item in $runtimes) {
    Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Output ("runtime stopped pid={0}" -f $item.ProcessId)
}
if ($runtimes) { Start-Sleep -Seconds 3 }

# 3. The extension module is rebuilt now.
$rustSource = Join-Path $repo 'flashloan\rs-src'
Push-Location $rustSource
try {
    & cargo build --offline --release
    if ($LASTEXITCODE -ne 0) { throw "the runtime extension module did not build" }
}
finally {
    Pop-Location
}
$module = Join-Path $rustSource 'target\release\flashloan_rs.dll'
Write-Output ("the runtime extension module is in place: {0} bytes" -f (Get-Item -LiteralPath $module).Length)

# 4. The manager-api is replaced and started again (the script keeps the
#    old binary beside the new one).
$deploy = Join-Path $PSScriptRoot 'deploy_manager_api_round13_20261128.ps1'
& $deploy

# 5. The healer starts again and brings the runtime up with it.
$runner = Join-Path $PSScriptRoot 'run_healer_4663_20261127.ps1'
& $runner supervise --apply --interval 60 --pnl-interval-seconds 300 --observation-hours 6 --leave-runtime-running

Start-Sleep -Seconds 5
$state = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match 'mainnet_arbitrage_healer\.py' -or $_.CommandLine -match 'manager-api\.exe' -or $_.CommandLine -match 'app_runtime[\\/]run\.py') }
foreach ($item in $state) {
    Write-Output ("running pid={0} {1}" -f $item.ProcessId, $item.Name)
}
