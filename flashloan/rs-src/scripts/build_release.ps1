#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet("develop", "wheel")]
    [string]$Target = "develop",
    [string]$Python = "python",
    [string]$Cargo = "cargo"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $pythonVersion = & $Python --version
    $rustVersion = & $Cargo --version
    $maturinVersion = & $Python -m maturin --version
    Write-Host "Python: $pythonVersion"
    Write-Host "Rust: $rustVersion"
    Write-Host "maturin: $maturinVersion"

    if ($Target -eq "wheel") {
        & $Python -m maturin build --release
    } else {
        & $Python -m maturin develop --release
    }
    if ($LASTEXITCODE -ne 0) {
        throw "flashloan_rs build failed"
    }
}
finally {
    Pop-Location
}
