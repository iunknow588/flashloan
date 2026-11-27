# Run the mainnet self-healing orchestrator against chain 4663.
#
# The tools directory is the **release** build: the healer probes the
# pool-manager for the command set it depends on, and the release build is
# the one that carries token-manager.exe and composite-manager.exe as well.
# The default in the healer points at target/debug, where those two binaries
# do not exist, so every token and closed-loop repair would fail on a missing
# executable instead of on the defect it was meant to fix.
#
# Usage:
#   run_healer_4663_20261127.ps1 <mode> [--apply] [--extra args...]

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_sup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$mode = $args[0]
if (-not $mode) { $mode = 'check' }
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$stdout = Join-Path $logDir ("healer_{0}_{1}.log" -f ($mode -replace '-', '_'), $stamp)
$stderr = Join-Path $logDir ("healer_{0}_{1}.log.err" -f ($mode -replace '-', '_'), $stamp)

$arguments = @(
    'scripts\mainnet_arbitrage_healer.py',
    '--mode', $mode,
    '--chain-id', '4663',
    '--tools', (Join-Path $repo 'flashloan\tools\target\release'),
    '--manager-api', 'http://127.0.0.1:8091',
    '--runtime-api', 'http://127.0.0.1:5000',
    '--window-hours', '24'
) + @($args | Select-Object -Skip 1)

$process = Start-Process -FilePath 'python' -WorkingDirectory $repo -WindowStyle Hidden `
    -ArgumentList $arguments -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Output ("healer started mode={0} pid={1} stdout={2} stderr={3}" -f $mode, $process.Id, $stdout, $stderr)
