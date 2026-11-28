# Deploy the broadcast policy that has no freshness window.
#
# Three artifacts carry the change, and all three are loaded into a
# running process, so the processes are restarted:
#
#   * the manager-api binary -- it owns the review library that publishes
#     the broadcast requests (the freshness gate is gone there),
#   * the runtime extension module -- the direct path that sends the
#     transaction right after the measurement (the freshness gate is gone
#     there too).  A module is loaded into the process when the process
#     starts, so a running runtime keeps the old code in memory: the
#     runtime has to be started again to pick the new one up,
#   * the healer -- the orchestrator that keeps the library moving (its
#     busy branch now runs the management writes, and its watchdog watches
#     the library instead of the silence).
#
# The old manager-api binary is kept beside the new one, so the swap can
# be undone.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path

function Stop-MatchingProcess([string]$pattern, [string]$label) {
    $matches = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern -and $_.ProcessId -ne $PID }
    if (-not $matches) {
        Write-Output ("{0}: nothing to stop" -f $label)
        return
    }
    foreach ($item in $matches) {
        Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output ("{0} stopped pid={1}" -f $label, $item.ProcessId)
    }
    Start-Sleep -Seconds 3
}

# 1. The healer stops first: it supervises the runtime and would start a
#    new one on its own.
Stop-MatchingProcess 'mainnet_arbitrage_healer\.py' 'healer'

# 2. The runtime stops second: it has to load the new extension module.
Stop-MatchingProcess 'app_runtime[\\/]run\.py' 'runtime'

# 3. The extension module is rebuilt now.  The build comes **after** the
#    runtime stopped, because the running runtime holds the old module
#    open and the linker cannot replace a file that is in use.
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
