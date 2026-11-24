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

$env:DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN = '32'
$env:DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS = '256'

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
