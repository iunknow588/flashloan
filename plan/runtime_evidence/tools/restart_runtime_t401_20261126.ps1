# Restart the opportunity-console runtime with a freshly built Rust extension.
#
# The supervisor is stopped **first**.  It restarts the runtime on its own,
# so stopping only the runtime would put the old process straight back in
# place and the new extension would never be loaded.  Starting the
# supervisor again launches the runtime with the new extension in one step,
# so the runtime never runs with the old extension and the new one at the
# same time.
#
# The script prints the pids and the port check so the deployment can be
# read back from the log instead of from recollection.

param(
    # The Rust extension is loaded by the runtime, so the DLL cannot be
    # replaced while the runtime runs.  The deployment therefore stops
    # first, builds, and only then starts again: `-StopOnly` performs the
    # first step on its own.
    [switch]$StopOnly
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_sup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp = '20261126_rpcfix2'
$supervisorOut = Join-Path $logDir ("supervisor_stdout_{0}.log" -f $stamp)
$supervisorErr = Join-Path $logDir ("supervisor_stderr_{0}.log" -f $stamp)

function Get-MatchingProcess([string]$needle) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like ("*" + $needle + "*") }
}

$supervisors = @(Get-MatchingProcess 'supervise_runtime_t401_20261124.py')
foreach ($process in $supervisors) {
    Write-Output ('stopping supervisor pid=' + $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

$runtimes = @(Get-MatchingProcess 'flashloan\app_runtime\run.py')
foreach ($process in $runtimes) {
    Write-Output ('stopping runtime pid=' + $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

# The port has to be free before the new runtime binds it, otherwise the
# new process fails to start and the supervisor would restart it in a loop.
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    $listener = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) { break }
    Start-Sleep -Milliseconds 500
}

if ($StopOnly) {
    Write-Output 'stopped only; the runtime is down and the extension is unlocked'
    return
}

$started = Start-Process -FilePath 'python' `
    -ArgumentList @('plan/runtime_evidence/tools/supervise_runtime_t401_20261124.py', '5000') `
    -WorkingDirectory $repo `
    -RedirectStandardOutput $supervisorOut `
    -RedirectStandardError $supervisorErr `
    -WindowStyle Hidden `
    -PassThru
Write-Output ('supervisor pid=' + $started.Id)
Write-Output ('supervisor log=' + $supervisorOut)
Write-Output 'restart requested'

