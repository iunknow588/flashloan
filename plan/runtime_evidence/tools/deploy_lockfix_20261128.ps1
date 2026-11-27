# Deploy the write-lock fix: stop the runtime, rebuild the extension, start it.
#
# The Rust extension is loaded by the runtime, so the DLL cannot be replaced
# while the runtime runs.  The deployment therefore stops first, builds, and
# only then starts again, so the runtime never runs with the old extension
# and the new one at the same time.  The Python side of the fix lives in
# ``flashloan/src/core/db/storage_business_io.py`` and is picked up by the
# same restart.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_sup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = '20261128_lockfix'

function Get-MatchingProcess([string]$needle) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like ("*" + $needle + "*") }
}

# The orchestrator **is** the runtime's supervisor: it restarts the runtime
# the moment it stops answering.  The deployment therefore stops the
# orchestrator first, or the runtime comes back while the extension is being
# built and the DLL cannot be replaced.
foreach ($process in @(Get-MatchingProcess 'mainnet_arbitrage_healer.py')) {
    Write-Output ('stopping orchestrator pid=' + $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
foreach ($process in @(Get-MatchingProcess 'flashloan\app_runtime\run.py')) {
    Write-Output ('stopping runtime pid=' + $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
foreach ($process in @(Get-MatchingProcess 'supervise_runtime_t401_20261124.py')) {
    Write-Output ('stopping runtime supervisor pid=' + $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    $listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) { break }
    Start-Sleep -Milliseconds 500
}
Write-Output 'runtime stopped'

$buildOut = Join-Path $logDir ("cargo_build_{0}.log" -f $stamp)
$build = Start-Process -FilePath 'cargo' `
    -ArgumentList @('build', '--release') `
    -WorkingDirectory (Join-Path $repo 'flashloan\rs-src') `
    -RedirectStandardOutput $buildOut `
    -RedirectStandardError ($buildOut + '.err') `
    -WindowStyle Hidden `
    -PassThru
$build.WaitForExit()
# ``Start-Process -PassThru`` leaves ``ExitCode`` empty until the process
# object is refreshed, so the code is read from the process itself.
$build.Refresh()
$buildExit = $build.ExitCode
Write-Output ('cargo exit=' + $buildExit)
if ($null -ne $buildExit -and $buildExit -ne 0) {
    Get-Content $buildOut -Tail 40
    Get-Content ($buildOut + '.err') -Tail 40
    throw 'the extension build failed; the runtime stays down'
}

$dll = Join-Path $repo 'flashloan\rs-src\target\release\flashloan_rs.dll'
$info = Get-Item $dll
Write-Output ('dll=' + $info.FullName + ' bytes=' + $info.Length + ' built=' + $info.LastWriteTime)

$healerOut = Join-Path $logDir ("healer_supervise_{0}.log" -f $stamp)
$healerErr = Join-Path $logDir ("healer_supervise_{0}.log.err" -f $stamp)
$started = Start-Process -FilePath 'python' `
    -ArgumentList @(
        'scripts\mainnet_arbitrage_healer.py',
        '--mode', 'supervise',
        '--chain-id', '4663',
        '--tools', (Join-Path $repo 'flashloan\tools\target\release'),
        '--manager-api', 'http://127.0.0.1:8091',
        '--runtime-api', 'http://127.0.0.1:5000',
        '--window-hours', '24',
        '--apply',
        '--interval', '60',
        '--pnl-interval-seconds', '300',
        '--observation-hours', '6',
        '--leave-runtime-running'
    ) `
    -WorkingDirectory $repo `
    -RedirectStandardOutput $healerOut `
    -RedirectStandardError $healerErr `
    -WindowStyle Hidden `
    -PassThru
Write-Output ('orchestrator pid=' + $started.Id)
Write-Output ('orchestrator log=' + $healerOut)
Write-Output 'restart requested'

