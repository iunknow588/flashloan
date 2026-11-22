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

    # The machine target must be the 64-bit host target. A 32-bit artifact
    # cannot be loaded by the 64-bit Python interpreter, and the failure
    # surfaces as a Wow64 compatibility error at import time rather than as a
    # build error, so it is checked here before anything is produced.
    $HostTarget = (& rustc -vV | Select-String -Pattern '^host:').ToString().Split(':')[1].Trim()
    if ($HostTarget -ne "x86_64-pc-windows-msvc") {
        throw "Unexpected host target '$HostTarget'. This extension module must be built for x86_64-pc-windows-msvc."
    }
    Write-Host "Host target: $HostTarget"

    if ($Target -eq "wheel") {
        & $Python -m maturin build --release --target $HostTarget
    } else {
        & $Python -m maturin develop --release --target $HostTarget
    }
    if ($LASTEXITCODE -ne 0) {
        throw "flashloan_rs build failed"
    }

    # Verify the produced module really is a 64-bit PE. The build can report
    # success while the artifact keeps a PE32 header when the linker falls
    # back to a different subsystem, which is the failure mode this check
    # exists to catch.
    $Artifact = Join-Path $Root "target\release\flashloan_rs.dll"
    if (-not (Test-Path $Artifact)) {
        throw "flashloan_rs build reported success but $Artifact is missing"
    }
    $Bytes = [System.IO.File]::ReadAllBytes($Artifact)
    $PeOffset = [System.BitConverter]::ToInt32($Bytes, 0x3C)
    $Machine = [System.BitConverter]::ToUInt16($Bytes, $PeOffset + 4)
    if ($Machine -ne 0x8664) {
        throw ("flashloan_rs.dll is not x86-64 (PE machine type 0x{0:X4}). " +
               "A 32-bit module cannot be imported by the 64-bit Python runtime.") -f $Machine
    }
    Write-Host "Verified: flashloan_rs.dll is x86-64 (PE32+)"
}
finally {
    Pop-Location
}
