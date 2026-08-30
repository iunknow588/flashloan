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
- [x] Freeze current executor stage before redeploy.
  - [x] Confirm no additional executor ABI changes are planned for this stage.
  - [x] Run `npm run stage:robinhood:testnet:predeploy` and archive the report.
- [x] Redeploy paused executor on Robinhood testnet with the latest V4 flash-accounting entrypoint after stage freeze.
  - [x] Set `ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true` only for the intentional redeploy command.
  - [x] Set `ROBINHOOD_TESTNET_DEPLOY_MILESTONE=paused_executor_v4_accounting_freeze` only after stage freeze.
- [x] Unfreeze only after the stage boundary and evidence baseline have been reviewed.
  - [x] Confirm no further executor ABI/runtime changes are planned.
  - [x] Rerun the stage-freeze predeploy check and paused readback immediately before paused executor redeploy.
- [x] Read back latest paused executor with `matchesLocalArtifact=true`.
- [x] Deploy/configure token-conservative normal-path V4 flash probe on Robinhood testnet.
- [x] Keep abnormal on-chain tests deferred until testnet profit-token reserve exists.
- [x] Open chain-test fork/static-call scope for Robinhood Testnet live testing.
  - [x] Confirm normal probe address, token, and amount resolve from testnet-scoped metadata.
  - [x] Confirm normal-probe broadcast config is open.
  - [x] Run the first small-notional normal-probe transaction on Robinhood Testnet.
- [x] Capture passing fork/static-call evidence for the paused redeploy discussion.
- [x] Run Robinhood Testnet dynamic route scan and record discovered candidates.
- [x] Add dynamic V4 route preflight script for the latest chain-test plan.
- [x] Build dynamic 3-hop V4 route calldata from PoolKey index and latest plan.
- [x] Temporarily open testnet execution gate for dynamic-route static-call rehearsal and restore `paused=true`.
- [x] Set Permit2 approvals for dynamic route intermediate tokens during the guarded test rehearsal.
- [x] Record dynamic route static-call failure without broadcasting the route transaction.
- [x] Add direct PoolManager route builder for native-value dynamic execution and redeploy the paused executor.
- [ ] Replace V4 `reference_quote_adapter` / spot-index quote with a real Robinhood Testnet V4 Quoter/SDK or swap static-call adapter before dynamic route submission.
- [x] Decode/resolve Universal Router failure selector `0x5090d6c6` from dynamic route static-call.
  - [x] Confirm `0x5090d6c6` maps to `AlreadyUnlocked()`, caused by nested PoolManager unlock when Universal Router is called inside the executor's V4 unlock callback.
- [x] Resolve the live-chain direct-route empty-revert on the first swap target for the reference-quoted 3-hop ETH path.
  - [x] Redeploy direct PoolManager executor with bytecode hash matching the local artifact.
  - [x] Decode live `ERC20InsufficientBalance` and adjust later exact-input hops to spend actual previous-hop output.
  - [x] Confirm the route now reaches repayment guard instead of PoolManager/router settlement failure.
- [ ] Replace the stale 3-hop ETH plan with a fresh live-verified profitable candidate.
  - Current direct-route preflight result: `BadFlashRepayment(balance=992686570454, required=1000000000000000000)`.
  - Current executor: `0x69d9e4ac531d48E388c54EA19B11AABeCE1d59bB`.
  - Current blocker: route executed in static-call but returned less ETH than required repayment, so broadcast was correctly skipped.
- [ ] Rebuild chain-test package from a real V4 quote/static-call verified dynamic candidate.
- [ ] Run fork/static-call for the dynamic 3-hop V4 route before opening dynamic route broadcast.

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
- [x] Keep paused executor deploy broadcast closed unless `ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true`.
- [x] Run adapter-code verification against real Robinhood testnet RPC.
- [x] Add preflight/readiness reporting for token-conservative on-chain test policy.
- [x] Add `ready:robinhood:offchain-normal` gate.
- [x] Add `ready:robinhood:executor-offchain-normal` gate.
- [x] Add non-broadcast paused redeploy plan/estimate command.
- [x] Add non-broadcast stage-freeze predeploy readiness command.
- [x] Add Python predeploy readiness command that combines latest chain-test package status with testnet deploy state.
- [x] Add candidate pool supply command for monitoring/search sufficiency.
- [x] Add Robinhood testnet V4 usable-pool evidence gate before paused deploy readiness.
- [x] Add `probe:robinhood:testnet:v4flash:normal` script.
- [x] Enable normal flash probe broadcast gate with `ROBINHOOD_TESTNET_ALLOW_NORMAL_FLASH_BROADCAST=true`.
- [x] Add `probe:robinhood:testnet:v4flash:normal:live` command for an explicit live normal-probe transaction.
- [x] Add `ready:robinhood:testnet:live` checker for normal-probe live testing readiness.
- [x] Add `preflight:robinhood:testnet:dynamic-v4-route` for dynamic 3-hop calldata/static-call rehearsal.
- [x] Decode `ERC20InsufficientBalance` / `BadFlashRepayment` in dynamic route preflight evidence.
- [x] Run live testing readiness checker and archive report.
  - Current status: `chainTestReady=true`.
  - Current status: normal-probe broadcast gate is closed again after live testing.
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
- [x] Test Robinhood Testnet live readiness requires broadcast and normal probe gates.
- [x] Test Robinhood Testnet live readiness CLI writes a scoped report.

## Archive

- [x] Archive development plan.
- [x] Archive checklist.
- [x] Archive validation summary.
