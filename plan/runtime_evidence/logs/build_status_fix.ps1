$ErrorActionPreference='Continue'
Set-Location 'E:\2026OPC大赛\flashLoan\flashloan\tools'
cargo test -p closed-loop-manager 2>&1
Set-Location 'E:\2026OPC大赛\flashLoan\flashloan\rs-src'
cargo check --lib 2>&1
