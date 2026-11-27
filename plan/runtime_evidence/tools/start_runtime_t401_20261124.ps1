# Launch the opportunity-console runtime for the T-复.01 evidence run.
#
# The resident-group ramp needs a couple of scan rounds before every
# published closed-loop group owns a worker, so the runtime has to stay
# alive across the whole run.  The script therefore detaches it from this
# shell and records the pid next to the logs.
$ErrorActionPreference = 'Stop'
# The repository root is derived from the script location.  Writing the
# path into the script would make it depend on how the script file is
# decoded, and a path decoded by the wrong code page sends the runtime to a
# directory that does not exist.
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_r3'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# The per-scan batch is **0**: the ramp is the wait, and a group that is
# not admitted in the first scan is not measured in the first scan.  Zero
# means *no wait* - every pending group becomes resident at once, so the
# whole catalog is reviewed in the first sweep and no closed loop keeps a
# stale verdict (a stale profit is no profit).  The review concurrency
# inside the pool still bounds how many groups measure at the same time.
$env:DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN = '0'
$env:DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS = '256'
# The review concurrency.  The default is 16; the Python side clamps the
# override to `CLOSED_LOOP_WORKER_CEILING = 32`, so 32 is the highest
# value that actually takes effect (`concurrencyLimit = 32` in the
# runtime status).  A chain publishes more closed loops than that, so the
# surplus loops would otherwise never get a worker and their profit data
# would never refresh.
$env:DEX_CLOSED_LOOP_MAX_WORKERS = '32'

$stdout = Join-Path $logDir 'runtime_stdout.log'
$stderr = Join-Path $logDir 'runtime_stderr.log'
$proc = Start-Process -FilePath 'python' `
    -ArgumentList @('flashloan\app_runtime\run.py', '--port', '5000') `
    -WorkingDirectory $repo `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru

$pidFile = Join-Path $logDir 'runtime_pid.txt'
Set-Content -Path $pidFile -Value $proc.Id -Encoding ascii
Write-Output ('runtime pid=' + $proc.Id)
Write-Output ('pid file  =' + $pidFile)
Write-Output 'launched'
