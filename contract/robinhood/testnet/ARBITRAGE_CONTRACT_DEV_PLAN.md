# Robinhood Flashloan Arbitrage Contract Development Plan

## Scope

This track prepares a Robinhood-only flashloan arbitrage executor. It must not reuse the Avalanche `UnifiedFlashLoanMevExecutor` as the Robinhood executable contract because that executor is bound to Aave flash loans and Uniswap V3 packed paths.

The current target is a paused testnet rehearsal contract:

- Flash source: Robinhood/Morpho Blue compatible `flashLoan(token, assets, data)`.
- Swap rail: Uniswap V3 exact-input-single calldata plus Uniswap V4 Universal Router calldata for observed mixed V3/V4 hops.
- Execution state: deployed paused, readback verified, broadcast disabled.
- Production state: blocked until fork/static-call evidence passes.

## Contract Architecture

`RobinhoodPausedFlashLoanExecutor` is the Robinhood-specific development contract.

Required modules:

- Owner and pause gate.
- Flash source adapter for Morpho Blue style callbacks.
- Uniswap V3 calldata adapter.
- Uniswap V4 calldata adapter.
- Route validation for token continuity and per-hop `minAmountOut`.
- Repayment guard for principal, flash fee, gas reserve, and minimum profit.
- Readback evidence for owner, pause, adapters, guard, chain ID, and code hash.

Explicit non-goals for this phase:

- No production broadcast enablement.
- No Aave adapter.
- No Avalanche V3 path execution.
- No blind arbitrary router target beyond configured Robinhood V4 adapter.

## Development Sequence

1. Freeze a chain evidence snapshot.
2. Build the Robinhood contract checklist.
3. Implement V4 calldata helpers and route validation.
4. Implement Morpho callback execution guard.
5. Add Hardhat tests for paused deploy, adapters, route continuity, repayment, and callback safety.
6. Compile and run the Robinhood-only test target.
7. Archive the docs, checklist, and validation result.

## Deployment Gate

The deployment gate remains closed unless all of these are true:

- Testnet deploy script deploys `RobinhoodPausedFlashLoanExecutor`, not the Avalanche executor.
- Constructor readback proves `paused == true`.
- Flash source adapter address is configured and has code on the target chain.
- V3 SwapRouter address is configured and has code when a route contains V3 hops.
- V4 Universal Router, PoolManager, and Permit2 addresses are configured and have code.
- Route calldata is built from verified hops and preserves token continuity.
- Every hop has a positive `amountIn` and `amountOutMinimum`.
- Final route output token equals the flash-borrow token.
- Repayment guard requires at least principal plus fee plus configured reserves.
- Fork/static-call or fork transaction simulation passes.
- Broadcast remains disabled until a separate execution-trust review.

## Evidence Outputs

Expected files:

- `contract/robinhood/testnet/README.md`
- `contract/robinhood/testnet/DEVELOPMENT_CHECKLIST.md`
- `contract/robinhood/testnet/contracts.json`
- `contract/contracts-dex/deployments/robinhood-paused-flashloan-robinhoodTestnet.json`
- `contract/contracts-dex/deployments/evidence/*_robinhoodTestnet-robinhood-paused-readback/report.json`
