# Rebuild the pool-manager release binary.
#
# The deployed flashloan/tools/target/release/pool-manager.exe was built on
# 2026-11-25 14:36, which is *before* migration 20261125_018 landed on the
# store (2026-11-25 23:43).  Its baseline audit still reads the base-pool
# column h.pool_id from manager_v4_closed_loop_hops, a column that migration
# 018 removed, so the command fails with
#   no such column: h.pool_id ...
# and the orchestrator cannot audit the store baseline at all.  The source
# already carries the post-018 query (manager_v4_directional_exchange_pools /
# directional_pool_id / hop_direction); only the binary is behind.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$tools = Join-Path $repo 'flashloan\tools'
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_sup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stdout = Join-Path $logDir 'build_release_pool_manager_20261127.log'
$stderr = Join-Path $logDir 'build_release_pool_manager_20261127.log.err'
$process = Start-Process -FilePath 'cargo' -WorkingDirectory $tools -WindowStyle Hidden `
    -ArgumentList @('build', '--offline', '--release', '-p', 'pool-manager') `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Output ("cargo build started pid={0} stdout={1} stderr={2}" -f $process.Id, $stdout, $stderr)
