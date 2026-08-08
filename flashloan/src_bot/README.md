# FlashLoan Bot Layout

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
