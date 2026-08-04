import argparse
import json
import os
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from market.aave_reserve_cache import load_aave_reserve_symbols
from market.observer import DEFAULT_RPC


load_env_files(__file__)


def symbol_set(items: list[dict]) -> set[str]:
    return {
        str(item.get("symbol", "")).upper()
        for item in items
        if item.get("symbol")
    }


def summarize_aave_hits(extremes: dict, reserve_symbols: set[str]) -> dict:
    top_symbols = symbol_set(list(extremes.get("top") or []))
    bottom_symbols = symbol_set(list(extremes.get("bottom") or []))
    candidate_symbols = top_symbols | bottom_symbols
    hits = sorted(candidate_symbols & {symbol.upper() for symbol in reserve_symbols})
    misses = sorted(candidate_symbols - set(hits))
    total = len(candidate_symbols)
    return {
        "observed_at": extremes.get("observed_at"),
        "window_seconds": extremes.get("window_seconds"),
        "sample_count": int(extremes.get("sample_count") or 0),
        "top_count": len(top_symbols),
        "bottom_count": len(bottom_symbols),
        "candidate_symbol_count": total,
        "aave_hit_count": len(hits),
        "aave_hit_rate": (len(hits) / total) if total else 0.0,
        "aave_hit_symbols": hits,
        "aave_miss_symbols": misses,
    }


def default_extremes_path() -> Path:
    return SRC_ROOT / "runtime" / "state" / "latest_extremes.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Aave reserve hits for latest velocity extremes.")
    parser.add_argument("--extremes", default=str(default_extremes_path()))
    args = parser.parse_args()

    path = Path(args.extremes)
    if not path.exists():
        raise RuntimeError(f"extremes file not found: {path}")

    extremes = json.loads(path.read_text(encoding="utf-8"))
    reserve_symbols = load_aave_reserve_symbols(
        os.getenv("AVALANCHE_RPC", DEFAULT_RPC).strip(),
        os.getenv("AAVE_POOL_ADDRESS", "").strip(),
    )
    summary = summarize_aave_hits(extremes, reserve_symbols)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
