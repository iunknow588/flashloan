# FlashLoan Python Layout

This directory keeps runtime files at the root and source code in functional packages.

## Packages

- `core/`: environment loading and shared helpers.
- `db/`: database schema and persistence.
- `market/`: Binance/Aave observers and Aave reserve cache.
- `strategy/`: velocity trigger logic and executable signal generation.
- `execution/`: DEX quote, cost, and payload helpers.
- `web/`: Flask control panel and templates.
- `tools/`: command-line analysis and plotting tools.
- `tests/`: Python unit tests.

## Common Commands

```powershell
python srcs_dex/run.py
python srcs_dex/market/observer.py
python srcs_dex/strategy/build_executable_signal.py --since-minutes 60 --limit 200
python srcs_dex/tools/analyze_thresholds.py --hours 2 --window-seconds 0.2
python -m pytest srcs_dex/tests
```

## Runtime Files

- `runtime/state/`: latest JSON snapshots such as `latest_arbitrage.json`, `latest_extremes.json`, and `latest_executable_signal.json`.
- `runtime/logs/`: observer/control-panel logs produced by manual runs.
- `runtime/cache/`: Aave reserve cache and other low-frequency chain metadata.
- `runtime/config/`: mutable local strategy config.

Only `.env`, `.env.example`, `requirements.txt`, `run.py`, this README, and source packages should live at the `srcs_dex/` root.
