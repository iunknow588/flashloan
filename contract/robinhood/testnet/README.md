# Robinhood Testnet Deployment Notes

- Network: `robinhood-testnet`
- Market ID: `robinhood-testnet-dex-paused`
- Chain ID: `46630`
- Chain key: `chain:46630`
- Flash-loan pool page default: yes; `/flash-loan/pools` defaults to `robinhood-testnet-dex-paused` on chain ID `46630`
- Generic DEX pages still keep their own defaults unless a Robinhood Testnet chain scope is requested
- Purpose: deploy/readback/pause-gate rehearsal only
- Broadcast status: closed after one successful normal flash probe live test; reopen only with the explicit normal-probe gate
- Executor status: paused testnet deployment completed and read back
- Executor contract: `RobinhoodPausedFlashLoanExecutor`
- Development plan: `ARBITRAGE_CONTRACT_DEV_PLAN.md`
- Development checklist: `DEVELOPMENT_CHECKLIST.md`
- Chain metadata: `chain.json`

## Deployment Gate

Do not deploy or probe on Robinhood Testnet unless the current chain evidence
confirms a usable V4 PoolManager pool. Do not treat a PoolManager contract code
check as liquidity proof.

Confirmed on 2026-08-25:

- Robinhood Testnet has Uniswap V4 PoolManager activity at `0x8366a39CC670B4001A1121B8F6A443A643e40951`.
- Direct RPC log scan on chain ID `46630` found `16203` initialized V4 pools and `525` recently swapped pools with non-zero `liquidity` in the last `200000` blocks.
- PoolManager held about `393` native testnet ETH during the check.
- This supports V4 flash-accounting tests only. It is not an Aave/Morpho flash-loan pool.
- No public Morpho Blue or Aave-style lending pool is currently verified for Robinhood Testnet `46630`.

Latest lending-liquidity check on 2026-08-26:

- Command: `python flashloan/src_bot/tools/check_robinhood_testnet_lending_liquidity.py`
- Evidence: `flashloan/src_bot/runtime/evidence/robinhood-testnet/lending_liquidity/latest.json`
- RPC chain ID: `46630`
- V4 PoolManager code: present, `24009` bytes
- Mainnet Morpho Blue address `0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010`: no code on Robinhood Testnet
- Aave V3 candidate address count: `0`
- Morpho Blue candidate address count: `0`
- `hasVerifiedBorrowableLiquidity=false`
- `supportsAaveOrMorphoFlashLoans=false`

Therefore Robinhood Testnet is currently treated as an isolated Uniswap V4
PoolManager flash-accounting test environment, not as a lending-pool flash-loan
environment. If future testnet Aave/Morpho addresses appear, they must be added
through `ROBINHOOD_TESTNET_AAVE_*` or `ROBINHOOD_TESTNET_MORPHO_*` env vars and
rechecked with non-zero borrow-token balances before enabling that path.

Do not deploy a broadcast-capable executor until these items are complete:

- Robinhood V4 PoolManager flash-accounting source is selected and verified.
- Uniswap V3/V4 swap calldata adapters are implemented for observed Robinhood mixed hops.
- Calldata builder enforces route address continuity and per-hop `minAmountOut`.
- Repayment constraint covers principal, fee, gas reserve, and minimum profit threshold.
- Testnet readback writes owner, adapter, pause, code hash, and chain ID evidence.
- Multi-hop V4 route calldata enforces hop token continuity and positive per-hop `minAmountOut`.
- Ordered flash callback enforces final route token equals the borrowed token.

The first testnet deployment should be a paused executor/readback rehearsal. It must not enable production broadcast.

## Stage-Freeze Rule

Do not redeploy again just because the previous paused executor is old. The next
paused redeploy is deferred until the current V4 PoolManager flash-accounting
implementation is stage-frozen.

Stage freeze requires:

- local Robinhood tests pass;
- executor-owned off-chain V4 flash accounting normal readiness passes;
- no open executor ABI or repayment-guard changes are planned for this stage;
- `stage:robinhood:testnet:predeploy` passes after fresh usable-pool evidence and token-conservative staticcall evidence;
- the deploy command is intentionally run with both deployment env gates.

Until then, the correct result is deployment deferred and
`readyForIntentionalPausedRedeploy=false`.

The Python predeploy gate must also pass before deployment discussion. It links
the latest chain-test package to the testnet deployment state:

```bash
python flashloan/src_bot/tools/check_robinhood_predeploy_readiness.py
python flashloan/src_bot/tools/check_robinhood_candidate_pool_supply.py
python flashloan/src_bot/tools/check_robinhood_testnet_lending_liquidity.py
```

The candidate-pool supply command is mainnet-scoped by design. For Robinhood
Testnet, `lastCandidatePoolSupplyEvidence` must stay unset/null; testnet
readiness can only use chain ID `46630` V4 PoolManager evidence and the
token-conservative normal probe/readback evidence.

## Dedicated Commands

Run from `contract/contracts-dex`:

```bash
npm run test:robinhood
npm run test:robinhood:offchain-normal
npm run ready:robinhood:offchain-normal
npm run ready:robinhood:executor-offchain-normal
npm run stage:robinhood:testnet:predeploy
npm run preflight:robinhood:testnet
npm run ready:robinhood:testnet
npm run ready:robinhood:testnet:live
npm run plan:robinhood:testnet:paused-redeploy
npm run probe:robinhood:testnet:v4flash:normal
npm run probe:robinhood:testnet:v4flash:normal:live
npm run deploy:robinhood:testnet:paused
npm run readback:robinhood:testnet
```

Use `preflight:robinhood:testnet` to write a full diagnostic report. Use
`ready:robinhood:testnet` as the strict gate; it exits non-zero until RPC,
required adapter code, deployer checks, and usable V4 PoolManager pool evidence
all pass. The paused deploy script also runs this preflight internally and
refuses to deploy when blockers remain.

The paused deploy command still broadcasts a testnet transaction. It requires
`ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true`; keep that unset/false while you
are only running readiness and readback checks.

Use `plan:robinhood:testnet:paused-redeploy` to estimate the next paused
redeploy without broadcasting.

Use `stage:robinhood:testnet:predeploy` as the non-broadcast milestone gate.
It answers whether the local V4 accounting work, metadata, current paused
readback, budget estimate, and token-conservative policy are coherent enough to
freeze the current stage. It does not authorize deployment by itself.

Use `ready:robinhood:testnet:live` before a normal-probe live transaction. Use
`probe:robinhood:testnet:v4flash:normal:live` only after the readiness report
is clean; it sets `ROBINHOOD_TESTNET_ALLOW_NORMAL_FLASH_BROADCAST=true` for that
single command.

Required adapter env vars for deployment:

- `ROBINHOOD_TESTNET_FLASH_SOURCE` or `ROBINHOOD_TESTNET_V4_POOL_MANAGER`
- `ROBINHOOD_TESTNET_V3_SWAP_ROUTER` when V3 hops are used
- `ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER`
- `ROBINHOOD_TESTNET_V4_POOL_MANAGER`
- `ROBINHOOD_TESTNET_PERMIT2`, defaults to `0x000000000022D473030F116dDEE9F6B43aC78BA3`
- `ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true`, only for an intentional paused redeploy
- `ROBINHOOD_TESTNET_DEPLOY_MILESTONE=paused_executor_v4_accounting_freeze`, only after the current code stage is frozen

Do not use `ROBINHOOD_TESTNET_MORPHO` as a Robinhood testnet source; no public Morpho Blue deployment is currently verified on chain ID `46630`.

If the frozen opportunity contains any V3 hop, set
`ROBINHOOD_TESTNET_REQUIRE_V3=true` so readiness also requires
`ROBINHOOD_TESTNET_V3_SWAP_ROUTER` to have code.

Template: `.env.testnet.example`

Static chain addresses and defaults live in `chain.json`. Keep `contract/robinhood/testnet/.env` for runtime overrides only; global accounts stay in `flashloan/src_bot/.env`.

## Token-Conservative Test Policy

Robinhood testnet defaults to normal-function testing only:

- `ROBINHOOD_TESTNET_ONCHAIN_TEST_MODE=normal_only`
- `ROBINHOOD_TESTNET_ENABLE_ABNORMAL_ONCHAIN_TESTS=false`

Use `callStatic` and `estimateGas` before any broadcast. When a transaction is
required, use the smallest viable successful-path amount. Do not broadcast
intentional revert cases such as under-repayment, unsettled deltas, token
mismatch, or forced slippage failure until normal flash-accounting tests have
earned enough test tokens for abnormal testing.

The off-chain normal readiness gate has passed:

- Evidence: `contract/contracts-dex/deployments/evidence/20260825T135140349Z-30844_hardhat-robinhood-v4-flash-offchain-normal/report.json`
- `readyForTestnetNormalProbe=true`
- Mock cycle: `unlock` -> `take` -> `sync` -> `settle`
- Final mock debt: `0`

The executor-owned off-chain normal readiness gate has passed:

- Evidence: `contract/contracts-dex/deployments/evidence/20260825T140129376Z-28472_hardhat-robinhood-executor-v4-flash-offchain-normal/report.json`
- `readyForPausedRedeploy=true`
- `readyForTestnetExecutorNormalProbe=true`
- Mock route hop count: `1`
- Final mock debt: `0`

## Current Implementation Status

- Dedicated Robinhood paused executor: ready for testnet rehearsal.
- Robinhood testnet preflight script: implemented.
- Robinhood testnet strict readiness gate: implemented.
- Robinhood testnet readiness summary includes deployed executor artifact match status.
- V3-hop readiness hard gate: implemented through `ROBINHOOD_TESTNET_REQUIRE_V3`.
- Official public RPC: verified on chain ID `46630`.
- Global deployer/account key: available through `flashloan/src_bot/.env` via `LIQUIDATION_EXECUTION_PRIVATE_KEY`; testnet balance check passes.
- Permit2: default address verified with code on testnet.
- V4 PoolManager flash source kind `4`: selected and verified on testnet.
- Morpho-style flash source adapter: implemented for compatibility tests, but not enabled as a Robinhood testnet source because no public deployment is verified on chain ID `46630`.
- Uniswap V3 exact-input-single calldata adapter: implemented and covered by tests.
- Uniswap V4 exact-input-single calldata adapter: implemented and covered by tests.
- V3/V4 mixed route calldata builder: implemented and covered by tests.
- Uniswap V4 ordered route calldata helper: implemented and covered by tests.
- Repayment guard: implemented and covered by tests.
- Production broadcast: disabled.
- Paused testnet executor: deployed at `0x1D3Af86E2C5a1aA85429885ADc1BB8e09f3dC17E`.
- Flash-loan pool page default: Robinhood Testnet `robinhood-testnet-dex-paused` / `chain:46630`; generic DEX pages keep their own defaults.
- CoW-specific APIs remain scoped to the CoW execution rail when no market ID is supplied.
- Local executor artifact now includes the V4 PoolManager `unlock` / `take` / `settle` entrypoint.
- Current paused testnet executor now matches the latest local artifact; the stage freeze boundary is preserved and the paused redeploy path is cleared.
- Paused redeploy requires explicit `ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true`.
- Paused redeploy also requires explicit stage-freeze milestone `ROBINHOOD_TESTNET_DEPLOY_MILESTONE=paused_executor_v4_accounting_freeze`.
- Stage freeze is used to lock the current paused executor ABI/runtime and evidence baseline before any intentional redeploy; do not unfreeze until no further executor changes are planned.
- Release criteria for unfreeze: keep the stage boundary fixed, preserve testnet-scoped readback evidence, and rerun predeploy checks before the intentional redeploy.
- Latest readback: `contract/contracts-dex/deployments/evidence/20260829T033247046Z-25660_robinhoodTestnet-robinhood-paused-readback/report.json`.
- Latest stage-freeze predeploy check: `flashloan/src_bot/runtime/evidence/robinhood-testnet/predeploy_readiness/20260829T033523Z/report.json`.
- Latest Python predeploy readiness check: `flashloan/src_bot/runtime/evidence/robinhood-testnet/predeploy_readiness/20260829T033523Z/report.json`.
- Current Python predeploy readiness status: ready for deployment discussion and paused redeploy.
- Mainnet candidate pool supply check `flashloan/src_bot/runtime/evidence/robinhood/candidate_pool_supply/20260825T151126Z/report.json` is explicitly not used for Robinhood Testnet.

The preflight report checks RPC chain ID and adapter address code status before deployment. Readback reports include executor code hash plus adapter code status.

Current Status / Remaining Observations:

- Actual V4 PoolManager `unlock` / `take` / `settle` execution flow is implemented and now deployed to Robinhood testnet.
- Paused testnet executor artifact match now passes: latest readback has `matchesLocalArtifact=true`.
- Latest readiness summary has `readyForLatestPausedExecutor=true`.
- Deploy command safety was verified: without `ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true`, it fails before broadcast.
- Latest paused redeploy plan: `contract/contracts-dex/deployments/evidence/20260825T143620366Z-34324_robinhoodTestnet-robinhood-paused-redeploy-plan/report.json`.
- Plan result: deployment remains deferred until fresh usable-pool evidence, token-conservative staticcall evidence, and explicit stage approval are present.
- Stage predeploy result: `readyForStageFreeze=true`, `stageFreezeApproved=true`, `readyForIntentionalPausedRedeploy=true`, `deploymentDeferred=true`, `broadcastAttempted=false`.
- Python predeploy result: `auditOk=true`, `readyForForkStaticcall=true`, `readyForDeploymentDiscussion=true`, `readyForPausedRedeploy=true`.
- Candidate pool supply result for Robinhood Testnet is intentionally zero/null; the previous mainnet `4663` candidate report is archived only as `mainnetCandidatePoolSupplyEvidenceNotUsedForTestnet` and must not be used as Robinhood Testnet `46630` execution proof.
- Chain-test package result for Robinhood Testnet is now present and scoped to `flashloan/src_bot/runtime/evidence/robinhood-testnet/chain_test/latest/plan.json`; `check_robinhood_predeploy_readiness.py` no longer falls back to mainnet evidence.
- Testnet V4 pool evidence: direct RPC/explorer checks show active V4 pools with non-zero liquidity; the probe is configured now, and the paused redeploy path still keeps the profit/fork-staticcall gate.
- Latest normal testnet probe check: `flashloan/src_bot/runtime/evidence/robinhood-testnet/normal_testnet_probe/latest.json`.
- Normal testnet probe now resolves `probe.address`, `token`, and `amount`; normal-probe broadcast can be reopened only with the explicit live-test command.
- Latest live testing readiness: `flashloan/src_bot/runtime/evidence/robinhood-testnet/live_testing_readiness/20260829T033905Z/report.json`.
- Live testing readiness currently has `broadcastEnabled=false`, `normalTestnetProbeReady=true`, and `chainTestReady=true`.
- Live normal probe broadcast hash: `0x00144975ae2d33961fd4414e2171dcb7cdbc4f0ce1c0b16770ad185c8a0268d2`.
- Broadcast is closed again after paused redeploy; reopen it only for another explicit normal probe.
- Token-conservative normal-path testnet probe is deployed and recorded on chain ID `46630`.
- Fresh closed opportunity capture and fork/static-call evidence are complete for the current Robinhood Testnet route.
- V3 SwapRouter is still required if the frozen route contains V3 hops.

Do not use this paused deployment as a production executor. It is a chain-specific Robinhood testnet rehearsal contract. Normal flash probe broadcasting was used for the live test, then closed again after paused redeploy; reopen it only with the explicit normal-probe gate.
