# Robinhood Flashloan Arbitrage Development Checklist

## Documentation

- [x] Record that Robinhood must use a dedicated executor.
- [x] Document flash source adapter requirements.
- [x] Document Uniswap V4 calldata adapter requirements.
- [x] Document pause/readback deployment gate.
- [ ] Add real Robinhood testnet adapter addresses after they are verified.

## Contract

- [x] Add `RobinhoodPausedFlashLoanExecutor`.
- [x] Default constructor state is paused.
- [x] Add Morpho Blue style flash source adapter config.
- [x] Add Uniswap V3 adapter config.
- [x] Add Uniswap V4 adapter config.
- [x] Add repayment guard config.
- [x] Add multi-hop V4 route calldata helper.
- [x] Add V3/V4 mixed route calldata helper.
- [x] Add route continuity validation.
- [x] Add callback execution path for ordered V4 swaps.
- [x] Add final-token repayment guard.

## Scripts

- [x] Add Robinhood paused deploy script.
- [x] Add Robinhood readback script.
- [x] Write readback evidence with code hash.
- [x] Add adapter-code preflight/readback checks.
- [x] Add strict readiness gate before paused deploy.
- [x] Add V3-hop strict readiness option.
- [x] Add Robinhood testnet env example.
- [x] Keep `broadcastEnabled=false`.
- [ ] Run adapter-code verification against real Robinhood testnet RPC.

## Tests

- [x] Test deploys paused.
- [x] Test adapter config.
- [x] Test unsupported flash source rejection.
- [x] Test paused flash requests are blocked.
- [x] Test V4 exact-input-single calldata shape.
- [x] Test V4 multi-hop continuity.
- [x] Test V3/V4 mixed route continuity.
- [x] Test callback rejects non-Morpho callers.
- [x] Test repayment guard rejects insufficient final balance.
- [x] Test successful mocked flashloan route repays principal.

## Archive

- [x] Archive development plan.
- [x] Archive checklist.
- [x] Archive validation summary.
