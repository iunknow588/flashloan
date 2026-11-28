# Deploy the manager-api that carries the unified review-queue stop.
#
# The binary that runs the review queue was rebuilt with the walk of the
# queue: the walk stops for **one** reason only -- the review time point of
# the entry it stopped at has not come -- and a loop that is parked at a
# marker (it is not to be reviewed for now) is described beside that stop
# instead of being a stop of its own.
#
# The old executable is kept beside the new one, so the swap can be undone.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$release = Join-Path $repo 'flashloan\tools\target\release'
$hotfix = Join-Path $repo 'flashloan\tools\target-hotfix\release'
$binary = Join-Path $release 'manager-api.exe'
$built = Join-Path $hotfix 'manager-api.exe'

if (-not (Test-Path -LiteralPath $built)) { throw "the new binary is missing: $built" }
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

$running = Get-Process -Name 'manager-api' -ErrorAction SilentlyContinue
if ($running) {
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
    Write-Output ('manager-api stopped pids=' + (($running | ForEach-Object { $_.Id }) -join ','))
}

if (Test-Path -LiteralPath $binary) {
    $backup = "$binary.bak-$stamp"
    Copy-Item -LiteralPath $binary -Destination $backup
    Write-Output "the old binary is kept: $backup"
}
Copy-Item -LiteralPath $built -Destination $binary -Force
# The hash is **evidence**, not a step: a session without `Get-FileHash`
# must not abort the deployment **after** the old process was stopped and
# the new binary was put in place.  That order leaves the service down
# with nothing to start it again, so the hash is read when it can be read
# and the deployment carries on either way.
$hash = 'unavailable'
if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
    try { $hash = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash } catch { $hash = 'unavailable' }
}
Write-Output ("the new binary is in place: {0} bytes sha256={1}" -f (Get-Item -LiteralPath $binary).Length, $hash)

$starter = Join-Path $repo 'plan\runtime_evidence\tools\start_manager_api_4663_20261127.ps1'
& $starter
