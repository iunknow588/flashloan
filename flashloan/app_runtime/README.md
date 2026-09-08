# Active FlashLoan Bot Layout

This is the active Python runtime. It owns current multi-chain market
configuration, Robinhood observation, liquidation, CoW intent flow, and the
current `UnifiedFlashLoanMevExecutor` integration.

The historical DEX-only Python tree is archived locally under
`back/legacy-python/srcs_dex`; it is independent and is not a second
implementation of this runtime. The related local design archive is under
`back/legacy-docs/back-dex`.

This directory is the Web/runtime adapter layer for the active Python runtime. The split implementation lives under `flashloan/src/{core,bot,dex,market}`; `app_runtime` keeps Flask/Web adapters, tests, chain data, and runtime evidence.

## Packages

- `src/`: active split implementation under `core`, `bot`, `dex`, and `market`.
- `web/`: Flask control panel and templates.
- `tests/`: Python unit tests.
- `tests/README.md`: test taxonomy, run order, and regression checklist.
- `chains/`: compatibility junction to `flashloan/src/chains`; the physical
  chain-owned JSON registry and flow data now lives under `flashloan/src/chains`.
- `runtime/`: runtime evidence, state, logs, and cache data.
- Top-level imports now resolve directly to `flashloan/src` split packages; no compatibility shims remain in `app_runtime`.

## Shared Chain Selection

All control-panel pages use the shared chain/market selection contract:

- `core/chain_selection.py`: resolves `market_id`, `chain_id`, `feature`, enabled
  state, and execution/observation capabilities. It does not access Flask
  request state or execute transactions.
- `web/chain_selection.py`: converts query parameters into a validated
  immutable selection and builds the page/API payload.
- `web/chain_selection_routes.py`: exposes `/api/chain-selection` and the
  compatibility alias `/api/markets`.
- `web/static/chain_selector.js`: keeps the selection in the URL and carries it
  across page navigation. Pages should consume `market_id` plus `chain_id`;
  `chain_id` alone is not unique because BNB has both Aave and Venus markets.
- The shared payload exposes `global_chain_id` and compatible `chain_key`
  values such as `chain:56` or `network:hyperliquid`, plus a `chain_config`
  summary sourced from `chains/<network>/chain.json` and `flows.json`.
- `web/control_panel_app.py`: validates selection parameters before API route
  execution, stores the resolved value in `g.chain_selection`, and exposes
  `X-Chain-Selection-Market`, `X-Chain-Selection-Global-Id`, and
  `X-Chain-Selection-Chain-Id` response headers.

The functional boundary is intentional: selection belongs to `core`/`web`;
market observation belongs to `market`; liquidation discovery and scanning
belong to `execution`/liquidation modules; quote construction and broadcast
gates belong to `intent_trade`/execution. A page must not assemble RPC URLs,
router addresses, or executor addresses from its own hard-coded chain IDs.

Binance/DEX pages remain multi-network analysis views, but their API requests
inherit the global selection query and return `chain_selection` metadata. Their
page-level default is the registered BNB market because the current CoW market
registry is BNB-only.

## Chain-Owned Data

Each chain owns one directory under `chains/<network>/`:

The physical directory is `flashloan/src/chains/<network>/`; `app_runtime/chains`
is a Junction that keeps legacy path-based tools working without hosting a
second copy of chain data.

- `chain.json`: chain ID, display metadata, contracts, chain-owned data-file
  paths, flash-loan protocol candidates, default protocols, and supported
  protocol count.
- `flows.json`: chain-owned processing-flow rules, handler names, allowed
  protocols, data-file keys, and cross-chain fallback policy.

Runtime code should read these files through `flashloan/src/core/chain_data_registry.py`
instead of copying addresses, cache paths, or flow rules into page handlers and
tools.

Each `flows.json` owns a chain-isolation policy. It must require `network`,
`chain_id`, and `market_id` scope fields, forbid cross-chain fallback, and list
all other registered networks and chain IDs as forbidden sources. The registry
audit exposes `dataIndependence` and `processingIndependence` for every chain
and fails when a flow references undeclared data files, omits scope matching, or
adds handler entries that are not declared as chain-owned flows.

The supported protocol list and count are chain-level data. A `flows.json` file
may narrow a flow to fewer `allowed_protocols`, or list `probe_protocols` for
negative checks, but it must not expand the chain's supported protocol set.

Robinhood Chain and Robinhood Testnet are separate chain data owners:

- `chains/robinhood/chain.json`: chain ID `4663`, mainnet/shadow evidence only.
- `chains/robinhood-testnet/chain.json`: chain ID `46630`, isolated Uniswap V4
  PoolManager flash-accounting tests only. It does not declare Aave or Morpho
  lending liquidity.

Evidence and sample loading must pass scope checks for `network`, `chain_id`,
and, when available, `market_id`. Missing Robinhood Testnet evidence should
close the gate rather than falling back to Robinhood mainnet or Avalanche data.
Run the registry audit after chain-data or flow-spec edits:

```powershell
python flashloan/src/market/tools/audit_chain_data_registry.py
```

## Common Commands

```powershell
python flashloan/app_runtime/run.py
python flashloan/src/market/strategy/build_executable_signal.py --since-minutes 60 --limit 200
python flashloan/src/market/tools/analyze_thresholds.py --hours 2 --window-seconds 0.2
python -m pytest flashloan/app_runtime/tests
```

The main entry is the control panel at `http://127.0.0.1:5000/`. The `/healthz` route stays available for liveness checks only.
Liquidation accounts are stored in Postgres when `DATABASE_URL` is set. The control panel can keep either 30 days or 365 days of discovery data, and on startup it backfills the latest window first before doing incremental discovery.
Environment precedence is process environment first, then the nearest `.env`, then parent-directory `.env` files.

## Runtime Files

- `runtime/state/`: latest JSON snapshots such as `latest_arbitrage.json`, `latest_extremes.json`, and `latest_executable_signal.json`.
- `runtime/logs/`: observer/control-panel logs produced by manual runs.
- `runtime/cache/`: Aave reserve cache and other low-frequency chain metadata.
- `runtime/config/`: mutable local strategy config.

Only `.env`, `.env.example`, `requirements.txt`, `run.py`, `run_local_preview.py`, this README, and operational directories should live at the `app_runtime/` root.
