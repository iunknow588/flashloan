# Robinhood Flashloan Arbitrage Development Checklist

## Documentation

- [x] Record that Robinhood must use a dedicated executor.
- [x] Document flash source adapter requirements.
- [x] Document Uniswap V4 calldata adapter requirements.
- [x] Document pause/readback deployment gate.
- [x] Register Robinhood testnet as independent chain `chain:46630`.
- [x] Keep Robinhood testnet out of the default DEX chain selector.
- [x] Add `chain.json` metadata for Robinhood testnet.
- [x] Add real Robinhood testnet adapter addresses after they are verified.
- [x] Document token-conservative `normal_only` testnet policy.
- [x] Document Python predeploy readiness gate.
- [x] Document candidate pool supply gate.
- [x] Document that mainnet `4663` pool supply evidence is not testnet `46630` execution proof.

## Contract

- [x] Add `RobinhoodPausedFlashLoanExecutor`.
- [x] Default constructor state is paused.
- [x] Add Morpho Blue style flash source adapter config for compatibility/fork scaffolding.
- [x] Add Uniswap V4 PoolManager flash-accounting source adapter config.
- [x] Document that Morpho Blue is not publicly verified on Robinhood testnet `46630`.
- [x] Add Uniswap V3 adapter config.
- [x] Add Uniswap V4 adapter config.
- [x] Add repayment guard config.
- [x] Add multi-hop V4 route calldata helper.
- [x] Add V3/V4 mixed route calldata helper.
- [x] Add route continuity validation.
- [x] Add callback execution path for ordered V4 swaps.
- [x] Add final-token repayment guard.
- [x] Add actual Uniswap V4 PoolManager `unlock` / `take` / `settle` flash-accounting execution path.
- [x] Add readback local artifact bytecode comparison.
- [x] Add executor-owned offchain V4 flash accounting readiness gate.
- [x] Add token-conservative normal-path V4 flash probe contract.
- [x] Add local MockV4PoolManager normal-path readiness script.
- [x] Add stage-freeze milestone gate before any further paused redeploy.
- [x] Document that stage freeze locks the current executor ABI/runtime before intentional redeploy.
- [ ] Freeze current executor stage before redeploy.
  - [ ] Confirm no additional executor ABI changes are planned for this stage.
  - [x] Run `npm run stage:robinhood:testnet:predeploy` and archive the report.
- [ ] Redeploy paused executor on Robinhood testnet with the latest V4 flash-accounting entrypoint after stage freeze.
  - [ ] Set `ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true` only for the intentional redeploy command.
  - [ ] Set `ROBINHOOD_TESTNET_DEPLOY_MILESTONE=paused_executor_v4_accounting_freeze` only after stage freeze.
- [ ] Unfreeze only after the stage boundary and evidence baseline have been reviewed.
  - [ ] Confirm no further executor ABI/runtime changes are planned.
  - [ ] Rerun the stage-freeze predeploy check and paused readback immediately before onchain testing.
- [ ] Read back latest paused executor with `matchesLocalArtifact=true`.
- [ ] Deploy/configure token-conservative normal-path V4 flash probe on Robinhood testnet.
- [ ] Keep abnormal on-chain tests deferred until testnet profit-token reserve exists.

## Scripts

- [x] Add Robinhood paused deploy script.
- [x] Require explicit `ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true` before paused testnet deployment.
- [x] Add Robinhood readback script.
- [x] Write readback evidence with code hash.
- [x] Add adapter-code preflight/readback checks.
- [x] Add strict readiness gate before paused deploy.
- [x] Add V3-hop strict readiness option.
- [x] Add Robinhood testnet env example.
- [x] Verify official testnet public RPC and chain ID.
- [x] Verify deployer fallback key and testnet balance gate.
- [x] Verify default V4 PoolManager / flash source code on testnet.
- [x] Verify default V4 Universal Router code on testnet.
- [x] Verify default Permit2 code on testnet.
- [x] Complete paused testnet deployment.
- [x] Complete paused testnet readback with adapter code status.
- [x] Keep `broadcastEnabled=false`.
- [x] Run adapter-code verification against real Robinhood testnet RPC.
- [x] Add preflight/readiness reporting for token-conservative on-chain test policy.
- [x] Add `ready:robinhood:offchain-normal` gate.
- [x] Add `ready:robinhood:executor-offchain-normal` gate.
- [x] Add non-broadcast paused redeploy plan/estimate command.
- [x] Add non-broadcast stage-freeze predeploy readiness command.
- [x] Add Python predeploy readiness command that combines latest chain-test package status with testnet deploy state.
- [x] Add candidate pool supply command for monitoring/search sufficiency.
- [x] Add Robinhood testnet V4 usable-pool evidence gate before paused deploy readiness.
- [x] Add `probe:robinhood:testnet:v4flash:normal` script with broadcast disabled by default.
- [x] Add deployed executor artifact match summary to `ready:robinhood:testnet`.
- [x] Keep CoW-specific APIs scoped to CoW while generic DEX pages default away from Robinhood testnet.

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
- [x] Test normal V4 PoolManager mock flash-accounting cycle settles debt to zero.
- [x] Test executor-owned V4 PoolManager flash-accounting route settles debt to zero.
- [x] Test executor V4 flash accounting repayment guard.
- [x] Test readback local artifact code status.
- [x] Test readiness summary exposes deployed executor artifact mismatch.
- [x] Test Robinhood testnet is not the default DEX chain selector.
- [x] Test paused redeploy plan summary remains non-broadcast.
- [x] Test Python predeploy readiness blocks deployment when latest opportunity misses min profit or fork/static-call evidence.
- [x] Test candidate pool supply does not imply execution readiness.

## Archive

- [x] Archive development plan.
- [x] Archive checklist.
- [x] Archive validation summary.
