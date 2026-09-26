import json
import os
from pathlib import Path

from core.kv_rows import load_rows


def testnet_trade_log_path(repo_root: Path) -> Path:
    raw = os.getenv("TESTNET_TRADE_LOG", "contracts/deployments/fuji-trades.jsonl").strip()
    path = Path(raw)
    return path if path.is_absolute() else repo_root / path


def testnet_trade_stats(repo_root: Path) -> dict:
    path = testnet_trade_log_path(repo_root)
    entries = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    total = len(entries)
    success = sum(1 for item in entries if item.get("success"))
    failed = total - success
    profit_units = sum(int(item.get("profitUnits") or 0) for item in entries if item.get("success"))
    return {
        "log_path": str(path),
        "total": total,
        "success": success,
        "failed": failed,
        "success_rate_percent": (success / total * 100) if total else 0.0,
        "profit_units": profit_units,
        "last_tx": next((item.get("txHash") for item in reversed(entries) if item.get("txHash")), None),
    }


def trade_stats(database_url: str | None) -> dict | None:
    if not database_url:
        return None
    rows = load_rows(database_url, "arbitrage_simulations")
    candidates = len(rows)
    signals = sum(1 for row in rows if bool(row.get("signal") or row.get("profitable")))
    net_values = [float(row.get("net_signal_profit_usd") or 0) for row in rows if row.get("net_signal_profit_usd") is not None]
    spread_values = [float(row.get("window_spread_percent") or 0) for row in rows if row.get("window_spread_percent") is not None]
    return {
        "candidates": candidates,
        "signals": signals,
        "signal_rate_percent": (signals / candidates * 100) if candidates else 0.0,
        "avg_net_profit_usdc": sum(net_values) / len(net_values) if net_values else 0.0,
        "max_net_profit_usdc": max(net_values) if net_values else 0.0,
        "avg_window_spread_percent": sum(spread_values) / len(spread_values) if spread_values else 0.0,
        "max_window_spread_percent": max(spread_values) if spread_values else 0.0,
    }
