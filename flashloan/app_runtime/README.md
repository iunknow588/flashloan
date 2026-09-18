# Active FlashLoan Bot Layout

This is the active Python runtime. It owns current multi-chain market
configuration, Robinhood observation, liquidation, CoW intent flow, and the
current `UnifiedFlashLoanMevExecutor` integration.

The historical DEX-only Python tree is archived locally under
`back/legacy-python/srcs_dex`; it is independent and is not a second
implementation of this runtime. The related local design archive is under
`back/legacy-docs/back-dex`.

This directory is the Web/runtime adapter layer for the active Python runtime. The split implementation lives under `flashloan/src/{core,bot,dex,market}`; `app_runtime` keeps Flask/Web adapters and tests. Chain data and runtime evidence live under `flashloan/src/chains` and `flashloan/src/runtime`.

## Packages

- `src/`: active split implementation under `core`, `bot`, `dex`, and `market`.
- `web/`: Flask control panel and templates.
- `tests/`: Python unit tests.
- `tests/README.md`: test taxonomy, run order, and regression checklist.
- Top-level imports now resolve directly to `flashloan/src` split packages; no compatibility shims remain in `app_runtime`.

Chain-owned JSON registry and flow data live under `flashloan/src/chains`; runtime evidence, state, logs, and cache data live under `flashloan/src/runtime`.

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

The physical directory is `flashloan/src/chains/<network>/`. Use that path
directly.

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
- `chains/robinhood-testnet/v4_token_allowlist.json`: static allowlist for V4
  pool display and balance scanning. Default refresh uses active pools plus
  this allowlist instead of a full initialized pool scan.

### Robinhood Testnet DEX Identity And Error Semantics

- Token identity for route tests and pool review must come from
  `chains/robinhood-testnet/v4_token_allowlist.json`. A symbol is display-only;
  on-chain quoting and pool matching use the allowlist address.
- Internal route dedup and scanner/evidence merging must prefer exact token
  addresses over symbols. Symbols can collide (for example multiple WETH
  addresses), so address path is the unique key whenever it is available.
- `ETH` means the native token `0x0000000000000000000000000000000000000000`.
  `WETH` may have multiple ERC20 wrapped token addresses on this testnet. A
  symbol-only `WETH` route is rejected as ambiguous; route builders must supply
  the exact wrapped-token address they intend to test. They must not conflate
  `ETH` and `WETH`.
- Token `decimals` must come from the allowlist, a trusted chain/ERC20 read, or
  an explicit user-provided token metadata record. Missing or unverified
  decimals are an error: the code must not silently assume `18`.
- Evidence failure statuses are distinct: `rpc_verification_failed` means RPC
  could not be used, `rpc_verification_timeout` means evidence exceeded its
  review budget, `pool_not_initialized`/`missing_effective_pool` means the pool
  is absent or not quoteable, and `not_enough_liquidity` means the pool has no
  LP liquidity for the requested amount.
- The DEX evidence path checks missing pool identity before RPC, then performs a
  live PoolManager `slot0`/`liquidity` read after connecting to the testnet RPC.
  A missing pool or empty LP reports a concrete business error instead of a
  45 second timeout. Cached liquidity data is not treated as live evidence.

### DEX Scan Concurrency

`DEX_SCAN_WORKERS` is a global user-controlled setting on the control panel,
defaulting to `1`. It controls how many on-chain evidence review workers the
DEX opportunity scanner uses. It is not hardcoded per testnet; users may raise
it on a machine known to tolerate parallel RPC/quoter review.

Runtime PID files should live under `runtime/logs/` or `runtime/state/` instead
of the repository root. Do not create versioned PID files such as
`run_server_5001_v2.pid` at the project root.

Evidence and sample loading must pass scope checks for `network`, `chain_id`,
and, when available, `market_id`. Missing Robinhood Testnet evidence should
close the gate rather than falling back to Robinhood mainnet or Avalanche data.
The DEX quote-routes API returns a `scannerState` object: `fresh` when the
scanner snapshot is current, and `stale_refresh_queued` when a real-time scan
was requested because the old snapshot was too old. The UI must not present that
stale snapshot as live evidence. `GET /api/dex/quote-routes?wait=1` synchronously
waits for one scanner refresh when the snapshot is stale, then returns the new
snapshot when available or `stale_refresh_queued` after a timeout/failure.
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

需要把服务日志统一写入 `flashloan/app_runtime/runtime/logs/` 时，使用：

```powershell
python flashloan/app_runtime/start_server.py --port 5000
```

`start_server.py` 会把 stdout/stderr 写到 `runtime/logs/server_<port>_stdout.log` 和 `server_<port>_stderr.log`，避免在仓库根目录或 `app_runtime/` 下散落外部日志文件。

The main entry is the control panel at `http://127.0.0.1:5000/`. The `/healthz` route stays available for liveness checks only.
Liquidation accounts are stored in Postgres when `DATABASE_URL` is set. The control panel can keep either 30 days or 365 days of discovery data, and on startup it backfills the latest window first before doing incremental discovery.
Environment precedence is process environment first, then the nearest `.env`, then parent-directory `.env` files.

## Runtime Files

- `runtime/state/`: latest JSON snapshots such as `latest_arbitrage.json`, `latest_extremes.json`, and `latest_executable_signal.json`.
- `runtime/logs/`: observer/control-panel/server logs produced by manual runs.
- `runtime/cache/`: Aave reserve cache and other low-frequency chain metadata.
- `runtime/config/`: mutable local strategy config.

Only `.env`, `.env.example`, `requirements.txt`, `run.py`, `run_local_preview.py`, this README, and operational directories should live at the `app_runtime/` root.
