# Rebuild the release binaries that own the scope load mode.
#
# The startup load mode is the **default** of the scope manager, so the
# binaries that materialize a scope have to carry it:
#   * pool-manager.exe  - the scope materializer and the baseline audit
#   * manager-api.exe   - the write owner of every layer below the scope
#
# The manager-api has to be stopped before the build: Windows refuses to
# overwrite a running executable.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$tools = Join-Path $repo 'flashloan\tools'
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_sup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$running = Get-Process -Name 'manager-api' -ErrorAction SilentlyContinue
if ($running) {
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
    Write-Output ('manager-api stopped pids=' + (($running | ForEach-Object { $_.Id }) -join ','))
}

$stdout = Join-Path $logDir 'build_release_scope_mode_20261127.log'
$stderr = Join-Path $logDir 'build_release_scope_mode_20261127.log.err'
$process = Start-Process -FilePath 'cargo' -WorkingDirectory $tools -WindowStyle Hidden `
    -ArgumentList @('build', '--offline', '--release', '-p', 'pool-manager', '-p', 'manager-api') `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Output ("cargo build started pid={0} stdout={1} stderr={2}" -f $process.Id, $stdout, $stderr)
