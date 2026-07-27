import argparse
import json
import os
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from market.aave_reserve_cache import load_aave_reserve_symbols
from core.env_loader import load_env_files
from market.observer import ASSETS, DEFAULT_EXECUTABLE_SYMBOLS, DEFAULT_RPC, env_list
from db.storage import require_psycopg


load_env_files(__file__)


def executable_symbols() -> set[str]:
    raw = os.getenv("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS).strip()
    if raw.upper() in {"AAVE", "AAVE_RESERVES", "AAVE_POOL"}:
        symbols = load_aave_reserve_symbols(
            os.getenv("AVALANCHE_RPC", DEFAULT_RPC).strip(),
            os.getenv("AAVE_POOL_ADDRESS", "").strip(),
        )
        if symbols:
            return symbols
    configured = env_list("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS)
    return {symbol for symbol in configured if symbol in ASSETS}


def fetch_candidates(database_url: str, since_minutes: int, limit: int) -> list[dict]:
    query = """
        SELECT observed_at, window_seconds, sample_count,
               top_symbol_1, top_change_percent_1, top_start_price_1, top_end_price_1,
               bottom_symbol_1, bottom_change_percent_1, bottom_start_price_1, bottom_end_price_1
        FROM binance_window_extremes
        WHERE observed_at >= now() - (%s || ' minutes')::interval
          AND top_change_percent_1 >= %s
          AND bottom_change_percent_1 <= -%s
        ORDER BY observed_at DESC
        LIMIT %s
    """
    min_up = float(os.getenv("TRIGGER_MIN_UP_CHANGE_PERCENT", "1.0"))
    min_down = float(os.getenv("TRIGGER_MIN_DOWN_CHANGE_PERCENT", "1.0"))
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (since_minutes, min_up, min_down, limit))
            rows = cursor.fetchall()
    return [
        {
            "observed_at": row[0].isoformat(),
            "window_seconds": float(row[1]),
            "sample_count": int(row[2]),
            "x_symbol": row[3],
            "x_change_percent": float(row[4]),
            "x_start_price": float(row[5]) if row[5] is not None else 0.0,
            "x_end_price": float(row[6]) if row[6] is not None else 0.0,
            "y_symbol": row[7],
            "y_change_percent": float(row[8]),
            "y_start_price": float(row[9]) if row[9] is not None else 0.0,
            "y_end_price": float(row[10]) if row[10] is not None else 0.0,
        }
        for row in rows
    ]


def build_signal(candidate: dict) -> dict:
    spread = candidate["x_change_percent"] - candidate["y_change_percent"]
    return {
        "observed_at": candidate["observed_at"],
        "window_seconds": candidate["window_seconds"],
        "sample_count": candidate["sample_count"],
        "price_source": "db_binance_window_extremes",
        "strategy": "onchain_dynamic_trigger",
        "best_strategy": "onchain_dynamic_decision",
        "basket_size": 1,
        "candidate_pair_count": 1,
        "evaluated_strategy_count": 4,
        "a_symbol": candidate["x_symbol"],
        "b_symbol": candidate["y_symbol"],
        "x_symbol": candidate["x_symbol"],
        "y_symbol": candidate["y_symbol"],
        "borrow_symbol": candidate["x_symbol"],
        "swap_symbol": candidate["y_symbol"],
        "route_symbols": [candidate["x_symbol"], "ONCHAIN_DYNAMIC", candidate["y_symbol"]],
        "a_change_percent": candidate["x_change_percent"],
        "b_change_percent": candidate["y_change_percent"],
        "x_change_percent": candidate["x_change_percent"],
        "y_change_percent": candidate["y_change_percent"],
        "a_start_price": candidate["x_start_price"],
        "a_end_price": candidate["x_end_price"],
        "b_start_price": candidate["y_start_price"],
        "b_end_price": candidate["y_end_price"],
        "window_spread_percent": spread,
        "min_window_spread_percent": float(os.getenv("TRIGGER_MIN_UP_CHANGE_PERCENT", "1.0"))
        + float(os.getenv("TRIGGER_MIN_DOWN_CHANGE_PERCENT", "1.0")),
        "trigger_signal": True,
        "signal": True,
        "profitable": True,
        "trigger_model": "db_window_velocity_aave_executable_intersection",
        "onchain_decision_required": True,
        "blocked_reasons": [],
        "pairs": [],
        "execution_plan": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-minutes", type=int, default=30)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--output",
        default=os.getenv(
            "EXECUTABLE_SIGNAL_FILE",
            "flashloan/srcs/runtime/state/latest_executable_signal.json",
        ),
    )
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    executable = executable_symbols()
    candidates = fetch_candidates(database_url, args.since_minutes, args.limit)
    filtered = [
        item
        for item in candidates
        if item["x_symbol"] in executable and item["y_symbol"] in executable
    ]
    output = {
        "executable_symbols": sorted(executable),
        "raw_candidate_count": len(candidates),
        "executable_candidate_count": len(filtered),
        "candidates": filtered,
        "signal": build_signal(filtered[0]) if filtered else None,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
