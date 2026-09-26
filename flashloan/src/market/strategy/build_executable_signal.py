import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.aave_reserve_cache import load_aave_reserve_symbols
from core.config_schema import parse_env_float, parse_env_int
from core.env_loader import load_env_files
from market.observer import ASSETS, DEFAULT_EXECUTABLE_SYMBOLS, DEFAULT_RPC, env_list
from core.db.storage_common import configured_database_url
from core.kv_rows import load_rows
from market.strategy.limits import DEFAULT_TRIGGER_MIN_DOWN_CHANGE_PERCENT, DEFAULT_TRIGGER_MIN_UP_CHANGE_PERCENT


load_env_files(__file__)


def executable_symbols() -> set[str]:
    raw = os.getenv("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS).strip()
    if raw.upper() in {"AAVE", "AAVE_RESERVES", "AAVE_POOL"}:
        reserve_limit = parse_env_int("AAVE_RESERVE_SYMBOL_LIMIT", 1000, minimum=0)[0]
        symbols = load_aave_reserve_symbols(
            os.getenv("AVALANCHE_RPC", DEFAULT_RPC).strip(),
            os.getenv("AAVE_POOL_ADDRESS", "").strip(),
            limit=reserve_limit,
        )
        if symbols:
            return symbols
    configured = env_list("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS)
    return {symbol for symbol in configured if symbol in ASSETS}


def fetch_candidates(database_url: str | None, since_minutes: int, limit: int) -> list[dict]:
    if not database_url:
        return []
    min_up = parse_env_float("TRIGGER_MIN_UP_CHANGE_PERCENT", DEFAULT_TRIGGER_MIN_UP_CHANGE_PERCENT, minimum=0)[0]
    min_down = parse_env_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", DEFAULT_TRIGGER_MIN_DOWN_CHANGE_PERCENT, minimum=0)[0]
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=int(since_minutes))
    rows = load_rows(database_url, "binance_window_extremes")
    candidates = []
    for row in rows:
        top = row.get("top") or []
        bottom = row.get("bottom") or []
        if not top or not bottom:
            continue
        try:
            observed_at = _parse_observed_at(row.get("observed_at"))
        except Exception:
            continue
        if observed_at < cutoff:
            continue
        x = top[0]
        y = bottom[0]
        x_change = float(x.get("change_percent") or 0)
        y_change = float(y.get("change_percent") or 0)
        if x_change < min_up or y_change > -min_down:
            continue
        candidates.append(
            {
                "observed_at": observed_at.isoformat(),
                "window_seconds": float(row.get("window_seconds") or 0),
                "sample_count": int(row.get("sample_count") or 0),
                "x_symbol": x.get("symbol"),
                "x_change_percent": x_change,
                "x_start_price": float(x.get("start_price") or 0),
                "x_end_price": float(x.get("end_price") or x.get("current_price") or 0),
                "y_symbol": y.get("symbol"),
                "y_change_percent": y_change,
                "y_start_price": float(y.get("start_price") or 0),
                "y_end_price": float(y.get("end_price") or y.get("current_price") or 0),
            }
        )
    candidates.sort(key=lambda item: str(item["observed_at"]), reverse=True)
    return candidates[:max(1, int(limit))]


def _parse_observed_at(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_candidate(candidate: dict) -> dict:
    spread = candidate["x_change_percent"] - candidate["y_change_percent"]
    return {
        "observed_at": candidate["observed_at"],
        "window_seconds": candidate["window_seconds"],
        "sample_count": candidate["sample_count"],
        "price_source": "db_binance_window_extremes",
        "stage": "aave_candidate",
        "strategy": "velocity_aave_candidate",
        "best_strategy": None,
        "basket_size": 1,
        "candidate_pair_count": 1,
        "evaluated_strategy_count": 0,
        "a_symbol": candidate["x_symbol"],
        "b_symbol": candidate["y_symbol"],
        "x_symbol": candidate["x_symbol"],
        "y_symbol": candidate["y_symbol"],
        "borrow_symbol": None,
        "swap_symbol": None,
        "route_symbols": [],
        "a_change_percent": candidate["x_change_percent"],
        "b_change_percent": candidate["y_change_percent"],
        "x_change_percent": candidate["x_change_percent"],
        "y_change_percent": candidate["y_change_percent"],
        "a_start_price": candidate["x_start_price"],
        "a_end_price": candidate["x_end_price"],
        "b_start_price": candidate["y_start_price"],
        "b_end_price": candidate["y_end_price"],
        "window_spread_percent": spread,
        "min_window_spread_percent": parse_env_float("TRIGGER_MIN_UP_CHANGE_PERCENT", DEFAULT_TRIGGER_MIN_UP_CHANGE_PERCENT, minimum=0)[0]
        + parse_env_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", DEFAULT_TRIGGER_MIN_DOWN_CHANGE_PERCENT, minimum=0)[0],
        "trigger_signal": False,
        "signal": False,
        "profitable": False,
        "executable_signal": False,
        "dex_quote_verified": False,
        "net_profit_verified": False,
        "simulation_required": True,
        "trigger_model": "db_window_velocity_aave_candidate_intersection",
        "onchain_decision_required": True,
        "blocked_reasons": [
            "dex_quote_not_verified",
            "net_profit_not_verified",
            "fork_or_fuji_simulation_not_verified",
        ],
        "pairs": [],
        "execution_plan": None,
    }


def build_verified_signal(candidate: dict) -> dict | None:
    built = build_candidate(candidate)
    if (
        built.get("dex_quote_verified")
        and built.get("net_profit_verified")
        and built.get("executable_signal")
    ):
        return built
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-minutes", type=int, default=30)
    parser.add_argument("--limit", type=int, default=100)
    default_output = SRC_ROOT / "runtime" / "state" / "latest_executable_signal.json"
    parser.add_argument(
        "--output",
        default=os.getenv("EXECUTABLE_SIGNAL_FILE", str(default_output)),
    )
    args = parser.parse_args()

    database_url = configured_database_url()

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
        "candidate": build_candidate(filtered[0]) if filtered else None,
        "signal": build_verified_signal(filtered[0]) if filtered else None,
        "blocked_reasons": []
        if filtered
        else ["no_recent_aave_matched_velocity_candidate"],
        "next_required_stage": "dex_quote",
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
