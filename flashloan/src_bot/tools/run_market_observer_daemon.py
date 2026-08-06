"""Start only the market observer in full Binance velocity mode."""

import asyncio
import os
import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def configure_market_observer_env() -> dict[str, str]:
    updates = {
        "BINANCE_SYMBOL_SELECTION": "velocity",
        "BINANCE_TOP_SYMBOL_LIMIT": "0",
        "BINANCE_VELOCITY_SIDE_LIMIT": os.getenv("BINANCE_VELOCITY_SIDE_LIMIT", "5"),
        "BINANCE_CANDIDATE_DB_SIDE_LIMIT": os.getenv("BINANCE_CANDIDATE_DB_SIDE_LIMIT", "5"),
        "BINANCE_WS_CHUNK_SIZE": os.getenv("BINANCE_WS_CHUNK_SIZE", "200"),
        "AAVE_VERIFICATION_ENABLED": os.getenv("AAVE_VERIFICATION_ENABLED", "true"),
        "REPORT_ONLY_ALERTS": os.getenv("REPORT_ONLY_ALERTS", "true"),
        "COW_REALTIME_QUOTE_ENABLED": "true",
        "COW_REALTIME_QUOTE_COOLDOWN_SECONDS": os.getenv("COW_REALTIME_QUOTE_COOLDOWN_SECONDS", "0.25"),
        "COW_REALTIME_QUOTE_MAX_INFLIGHT": os.getenv("COW_REALTIME_QUOTE_MAX_INFLIGHT", "2"),
        "COW_FLASHLOAN_PURE_INTENT_ENABLED": os.getenv("COW_FLASHLOAN_PURE_INTENT_ENABLED", "true"),
        "COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT": os.getenv(
            "COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT", "0.618"
        ),
        "COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC": os.getenv(
            "COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC", "0"
        ),
        "COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC": os.getenv(
            "COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC", "0"
        ),
        "BINANCE_SCAN_PROFILE": "200ms",
        "BINANCE_CHANGE_WINDOW_SECONDS": "0.2",
        "SAMPLE_SECONDS": "0.2",
        "BINANCE_EXTREME_WRITE_SECONDS": "0.2",
        "BINANCE_PAIR_PRICE_WRITE_SECONDS": "0.2",
    }
    os.environ.update(updates)
    return updates


def main() -> int:
    configure_market_observer_env()
    from market import observer

    asyncio.run(observer.main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
