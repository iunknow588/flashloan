"""Refresh the Avalanche L1 pool static table on a fixed interval."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from market.tools.build_avalanche_l1_pool_static_table import build_table, _write_json_atomic


def _interval_seconds() -> float:
    raw = os.getenv("AVALANCHE_L1_POOL_STATIC_REFRESH_SECONDS", "3600")
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 3600.0


def refresh_once(*, output: Path, latest_output: Path, discovery_cache: Path, price_state: Path, rpc_url: str) -> dict:
    report = build_table(
        discovery_cache_path=discovery_cache,
        price_state_path=price_state,
        rpc_url=rpc_url,
    )
    _write_json_atomic(output, report)
    _write_json_atomic(latest_output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--discovery-cache", type=Path, default=SRC_ROOT / "runtime" / "cache" / "avalanche_v3_pools.json")
    parser.add_argument("--price-state", type=Path, default=SRC_ROOT / "runtime" / "state" / "latest_extremes.json")
    parser.add_argument("--output", type=Path, default=SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json")
    parser.add_argument("--latest-output", type=Path, default=SRC_ROOT / "runtime" / "state" / "latest_avalanche_l1_pool_static_table.json")
    args = parser.parse_args()

    interval = _interval_seconds()
    while True:
        report = refresh_once(
            output=args.output,
            latest_output=args.latest_output,
            discovery_cache=args.discovery_cache,
            price_state=args.price_state,
            rpc_url=args.rpc_url,
        )
        summary = report.get("summary") or {}
        print(
            "avalanche l1 pool static table refreshed",
            f"staticPools={summary.get('staticPoolCount')}",
            f"qualityPools={summary.get('qualityPoolCount')}",
            flush=True,
        )
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
