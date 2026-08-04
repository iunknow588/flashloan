import json
import os
from pathlib import Path

from db.storage import require_psycopg


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


def trade_stats(database_url: str) -> dict:
    query = """
        SELECT
            COUNT(*) AS candidates,
            COALESCE(SUM(CASE WHEN signal THEN 1 ELSE 0 END), 0) AS signals,
            COALESCE(AVG(net_signal_profit_usd), 0) AS avg_net_profit,
            COALESCE(MAX(net_signal_profit_usd), 0) AS max_net_profit,
            COALESCE(AVG(window_spread_percent), 0) AS avg_spread,
            COALESCE(MAX(window_spread_percent), 0) AS max_spread
        FROM arbitrage_simulations
    """
    row = fetch_one(database_url, query)
    candidates = int(row[0] or 0) if row else 0
    signals = int(row[1] or 0) if row else 0
    return {
        "candidates": candidates,
        "signals": signals,
        "signal_rate_percent": (signals / candidates * 100) if candidates else 0.0,
        "avg_net_profit_usdc": float(row[2] or 0) if row else 0.0,
        "max_net_profit_usdc": float(row[3] or 0) if row else 0.0,
        "avg_window_spread_percent": float(row[4] or 0) if row else 0.0,
        "max_window_spread_percent": float(row[5] or 0) if row else 0.0,
    }


def testnet_trade_log_path(repo_root: Path) -> Path:
    raw = os.getenv("TESTNET_TRADE_LOG", "contracts/deployments/fuji-trades.jsonl").strip()
    path = Path(raw)
    return path if path.is_absolute() else repo_root / path


def fetch_one(database_url: str, query: str, params: tuple = ()) -> tuple | None:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()
