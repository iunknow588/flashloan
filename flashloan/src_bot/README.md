# Active FlashLoan Bot Layout

This is the active Python runtime. It owns current multi-chain market
configuration, Robinhood observation, liquidation, CoW intent flow, and the
current `UnifiedFlashLoanMevExecutor` integration.

The historical DEX-only Python tree is archived locally under
`back/legacy-python/srcs_dex`; it is independent and is not a second
implementation of this runtime. The related local design archive is under
`back/legacy-docs/back-dex`.

This directory keeps the active single-console bot runtime and source code in functional packages.

## Packages

- `core/`: environment loading and shared helpers.
- `db/`: database schema and persistence.
- `market/`: market observers and Aave reserve cache.
- `strategy/`: signal generation and opportunity filtering.
- `execution/`: quote, cost, payload, and liquidation helpers.
- `cow_flashloan/`: CoW route evaluation, flashloan capability checks, and order submission helpers.
- `web/`: Flask control panel and templates.
- `tools/`: command-line analysis and plotting tools.
- `tests/`: Python unit tests.
- `tests/README.md`: test taxonomy, run order, and regression checklist.

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
- `web/control_panel_app.py`: validates selection parameters before API route
  execution, stores the resolved value in `g.chain_selection`, and exposes
  `X-Chain-Selection-Market` plus `X-Chain-Selection-Chain-Id` response headers.

The functional boundary is intentional: selection belongs to `core`/`web`;
market observation belongs to `market`; liquidation discovery and scanning
belong to `execution`/liquidation modules; quote construction and broadcast
gates belong to `intent_trade`/execution. A page must not assemble RPC URLs,
router addresses, or executor addresses from its own hard-coded chain IDs.

Binance/DEX pages remain multi-network analysis views, but their API requests
inherit the global selection query and return `chain_selection` metadata. Their
page-level default is the registered BNB market because the current CoW market
registry is BNB-only.

## Common Commands

```powershell
python flashloan/src_bot/run.py
python flashloan/src_bot/strategy/build_executable_signal.py --since-minutes 60 --limit 200
python flashloan/src_bot/tools/analyze_thresholds.py --hours 2 --window-seconds 0.2
python -m pytest flashloan/src_bot/tests
```

The main entry is the control panel at `http://127.0.0.1:5000/`. The `/healthz` route stays available for liveness checks only.
Liquidation accounts are stored in Postgres when `DATABASE_URL` is set. The control panel can keep either 30 days or 365 days of discovery data, and on startup it backfills the latest window first before doing incremental discovery.
Environment precedence is process environment first, then the nearest `.env`, then parent-directory `.env` files.

## Runtime Files

- `runtime/state/`: latest JSON snapshots such as `latest_arbitrage.json`, `latest_extremes.json`, and `latest_executable_signal.json`.
- `runtime/logs/`: observer/control-panel logs produced by manual runs.
- `runtime/cache/`: Aave reserve cache and other low-frequency chain metadata.
- `runtime/config/`: mutable local strategy config.

Only `.env`, `.env.example`, `requirements.txt`, `run.py`, this README, and source packages should live at the `src_bot/` root.
