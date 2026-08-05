"""Cache full Binance USDT mover snapshots for the Binance market page."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from market.observer_common import write_json_atomic
from web.binance_market_service import (
    DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH,
    DEFAULT_BINANCE_MARKET_PREVIOUS_MAX_AGE_SECONDS,
    _previous_window_snapshot,
    build_binance_rest_market_snapshot,
    read_binance_market_snapshot,
)


def poll_seconds() -> float:
    raw = os.getenv("BINANCE_MARKET_SNAPSHOT_SECONDS") or os.getenv("BINANCE_REST_POLL_SECONDS", "3")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 3.0


def write_snapshot(*, path: Path = DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH, side_limit: int = 20) -> dict:
    previous_snapshot = _previous_window_snapshot(
        read_binance_market_snapshot(path),
        max_age_seconds=DEFAULT_BINANCE_MARKET_PREVIOUS_MAX_AGE_SECONDS,
    )
    snapshot = build_binance_rest_market_snapshot(
        side_limit=side_limit,
        previous_snapshot=previous_snapshot,
    )
    snapshot["market_state_source"] = "snapshot_daemon"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(str(path), snapshot)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Write cached Binance full-market mover snapshots.")
    parser.add_argument("--once", action="store_true", help="Write one snapshot and exit.")
    parser.add_argument("--side-limit", type=int, default=20, help="Top/bottom rows to cache.")
    parser.add_argument("--path", type=Path, default=DEFAULT_BINANCE_MARKET_SNAPSHOT_PATH)
    args = parser.parse_args()

    interval = poll_seconds()
    while True:
        snapshot = write_snapshot(path=args.path, side_limit=max(1, int(args.side_limit)))
        print(
            "binance market snapshot",
            f"source={snapshot.get('price_source')}",
            f"universe={snapshot.get('observation_universe_size')}",
            f"top={len(snapshot.get('top') or [])}",
            f"bottom={len(snapshot.get('bottom') or [])}",
            flush=True,
        )
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
