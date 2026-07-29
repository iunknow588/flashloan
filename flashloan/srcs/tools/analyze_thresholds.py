import argparse
import os
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files


load_env_files(__file__)


def require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install psycopg[binary]") from exc
    return psycopg


def pct(start: float, end: float) -> float:
    return (end - start) / start * 100 if start > 0 else 0.0


def summarize(values: list[dict], thresholds: list[float]) -> dict:
    if not values:
        return {}
    tops = [row["top_change_percent"] for row in values]
    bottoms = [row["bottom_change_percent"] for row in values]
    spreads = [row["top_change_percent"] - row["bottom_change_percent"] for row in values]
    sample_counts = [row["sample_count"] for row in values]
    summary = {
        "count": len(values),
        "avg_sample_count": sum(sample_counts) / len(sample_counts),
        "min_sample_count": min(sample_counts),
        "avg_top_change_percent": sum(tops) / len(tops),
        "avg_bottom_change_percent": sum(bottoms) / len(bottoms),
        "avg_abs_bottom_change_percent": sum(abs(item) for item in bottoms) / len(bottoms),
        "avg_spread_percent": sum(spreads) / len(spreads),
        "max_top_change_percent": max(tops),
        "min_bottom_change_percent": min(bottoms),
        "max_spread_percent": max(spreads),
    }
    for threshold in thresholds:
        key = f"dual_{threshold:g}pct_trigger_count".replace(".", "_")
        summary[key] = sum(
            1
            for row in values
            if row["top_change_percent"] >= threshold and row["bottom_change_percent"] <= -threshold
        )
    return summary


def load_price_history(database_url: str, hours: float) -> list[tuple]:
    psycopg = require_psycopg()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT observed_at, symbol, price, event_time, source
                FROM binance_price_history
                WHERE event_time >= %s
                ORDER BY event_time, symbol
                """,
                (cutoff,),
            )
            return cursor.fetchall()


def replay_windows(rows: list[tuple], window_seconds: float) -> list[dict]:
    by_symbol: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    event_times: set[datetime] = set()
    latest_by_event: dict[datetime, dict[str, tuple[datetime, float]]] = defaultdict(dict)

    for observed_at, symbol, price, event_time, _source in rows:
        by_symbol[symbol].append((event_time, float(price)))
        event_times.add(event_time)
        latest_by_event[event_time][symbol] = (event_time, float(price))

    symbol_times = {
        symbol: [item[0] for item in series]
        for symbol, series in by_symbol.items()
    }
    results = []
    window = timedelta(seconds=window_seconds)
    for event_time in sorted(event_times):
        changes = []
        for symbol, (_end_time, end_price) in latest_by_event[event_time].items():
            times = symbol_times[symbol]
            series = by_symbol[symbol]
            start_index = bisect_left(times, event_time - window)
            if start_index >= len(series):
                continue
            start_time, start_price = series[start_index]
            if start_time > event_time or start_price <= 0:
                continue
            changes.append(
                {
                    "symbol": symbol,
                    "start_time": start_time,
                    "end_time": event_time,
                    "start_price": start_price,
                    "end_price": end_price,
                    "change_percent": pct(start_price, end_price),
                }
            )
        if len(changes) < 2:
            continue
        gainers = [item for item in changes if item["change_percent"] > 0]
        losers = [item for item in changes if item["change_percent"] < 0]
        if not gainers or not losers:
            continue
        top = max(gainers, key=lambda item: item["change_percent"])
        bottom = min(losers, key=lambda item: item["change_percent"])
        if top["symbol"] == bottom["symbol"]:
            continue
        results.append(
            {
                "observed_at": event_time,
                "sample_count": len(changes),
                "top_symbol": top["symbol"],
                "top_change_percent": top["change_percent"],
                "bottom_symbol": bottom["symbol"],
                "bottom_change_percent": bottom["change_percent"],
            }
        )
    return results


def print_summary(name: str, summary: dict) -> None:
    print(f"[{name}]")
    if not summary:
        print("count=0")
        return
    for key, value in summary.items():
        print(f"{key}={value}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--window-seconds", type=float, nargs="+", default=[0.2])
    parser.add_argument("--thresholds", type=float, nargs="+", default=[1.0])
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    rows = load_price_history(database_url, args.hours)
    for window_seconds in args.window_seconds:
        replayed = replay_windows(rows, window_seconds)
        print_summary(
            f"replayed_price_history_{window_seconds:g}s",
            summarize(replayed, args.thresholds),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
