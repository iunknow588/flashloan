# Run the orchestrator regression suites and keep their output next to the
# other runtime evidence, because both suites take longer than one command
# window and their result has to be readable afterwards.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$logDir = Join-Path $repo 'plan\runtime_evidence\logs\t401_runtime_sup'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$suites = @(
    'scripts\tests\test_mainnet_arbitrage_healer.py',
    'flashloan\app_runtime\tests\unit\test_mainnet_arbitrage_healer_repair_chain.py'
)
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

foreach ($suite in $suites) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($suite)
    $stdout = Join-Path $logDir ("pytest_{0}_{1}.log" -f $name, $stamp)
    $stderr = Join-Path $logDir ("pytest_{0}_{1}.log.err" -f $name, $stamp)
    $process = Start-Process -FilePath 'python' -WorkingDirectory $repo -WindowStyle Hidden `
        -ArgumentList @('-m', 'pytest', $suite, '-q', '--no-header') `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Write-Output ("pytest started suite={0} pid={1} stdout={2}" -f $suite, $process.Id, $stdout)
}
