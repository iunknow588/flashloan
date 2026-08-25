# Robinhood Testnet Deployment Notes

- Network: `robinhood-testnet`
- Chain ID: `46630`
- Purpose: deploy/readback/pause-gate rehearsal only
- Broadcast status: disabled
- Executor status: paused-deploy script ready
- Executor contract: `RobinhoodPausedFlashLoanExecutor`
- Development plan: `ARBITRAGE_CONTRACT_DEV_PLAN.md`
- Development checklist: `DEVELOPMENT_CHECKLIST.md`

## Deployment Gate

Do not deploy a broadcast-capable executor until these items are complete:

- Robinhood flashloan provider adapter is selected and verified.
- Uniswap V3/V4 swap calldata adapters are implemented for observed Robinhood mixed hops.
- Calldata builder enforces route address continuity and per-hop `minAmountOut`.
- Repayment constraint covers principal, fee, gas reserve, and minimum profit threshold.
- Testnet readback writes owner, adapter, pause, code hash, and chain ID evidence.
- Multi-hop V4 route calldata enforces hop token continuity and positive per-hop `minAmountOut`.
- Ordered flash callback enforces final route token equals the borrowed token.

The first testnet deployment should be a paused executor/readback rehearsal. It must not enable production broadcast.

## Dedicated Commands

Run from `contract/contracts-dex`:

```bash
npm run test:robinhood
npm run preflight:robinhood:testnet
npm run ready:robinhood:testnet
npm run deploy:robinhood:testnet:paused
npm run readback:robinhood:testnet
```

Use `preflight:robinhood:testnet` to write a full diagnostic report. Use
`ready:robinhood:testnet` as the strict gate; it exits non-zero until RPC,
required adapter code, and deployer checks all pass. The paused deploy script
also runs this preflight internally and refuses to deploy when blockers remain.

Required adapter env vars for deployment:

- `ROBINHOOD_TESTNET_FLASH_SOURCE` or `ROBINHOOD_TESTNET_MORPHO`
- `ROBINHOOD_TESTNET_V3_SWAP_ROUTER` when V3 hops are used
- `ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER`
- `ROBINHOOD_TESTNET_V4_POOL_MANAGER`
- `ROBINHOOD_TESTNET_PERMIT2`

If the frozen opportunity contains any V3 hop, set
`ROBINHOOD_TESTNET_REQUIRE_V3=true` so readiness also requires
`ROBINHOOD_TESTNET_V3_SWAP_ROUTER` to have code.

Template: `.env.testnet.example`

## Current Implementation Status

- Dedicated Robinhood paused executor: ready for testnet rehearsal.
- Robinhood testnet preflight script: implemented.
- Robinhood testnet strict readiness gate: implemented.
- V3-hop readiness hard gate: implemented through `ROBINHOOD_TESTNET_REQUIRE_V3`.
- Morpho-style flash source adapter: implemented and covered by tests.
- Uniswap V3 exact-input-single calldata adapter: implemented and covered by tests.
- Uniswap V4 exact-input-single calldata adapter: implemented and covered by tests.
- V3/V4 mixed route calldata builder: implemented and covered by tests.
- Uniswap V4 ordered route calldata helper: implemented and covered by tests.
- Repayment guard: implemented and covered by tests.
- Production broadcast: disabled.

The preflight report checks RPC chain ID and adapter address code status before deployment. Readback reports include executor code hash plus adapter code status.
