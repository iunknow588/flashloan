"""Print a five-stage DEX integrity report for one Robindhood V4 scope.

The report consumes only SQLite business tables plus the persisted compact
DEX overview snapshot.  It never reads JSON evidence files.  A non-zero exit
means one of the five stages exposed a blocking integrity violation, most
commonly an executable pool whose raw liquidity evidence is missing or zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.direction_integrity import build_v4_integrity_report  # noqa: E402
from core.db.storage_schema import ensure_database_schema  # noqa: E402
from dex.intent_trade.chains.robinhood_common.chain_config import (  # noqa: E402
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_MARKET_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
)


def _scope(network: str, chain_id: int | None) -> tuple[str, int, str]:
    normalized = str(network or "").strip().lower()
    if normalized in {ROBINHOOD_TESTNET_NETWORK, "testnet"} or chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
        return ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_MARKET_ID
    return ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID, ROBINHOOD_MARKET_ID


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="")
    parser.add_argument(
        "--network",
        default=ROBINHOOD_NETWORK,
        choices=(ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_NETWORK),
    )
    parser.add_argument("--chain-id", type=int, default=None)
    parser.add_argument("--market-id", default="")
    parser.add_argument("--anchors", default="", help="Comma-separated legal anchor symbols; defaults to configured set.")
    parser.add_argument("--output", default="", help="Optional JSON file path; default writes to stdout.")
    parser.add_argument("--fail-on-warning", action="store_true")
    args = parser.parse_args()

    network, chain_id, market_id = _scope(args.network, args.chain_id)
    database = absolute_database_path(args.database)
    ensure_database_schema(str(database))
    anchors = None
    if args.anchors:
        anchors = tuple(
            item.strip().upper()
            for item in args.anchors.split(",")
            if item.strip()
        )
    report = build_v4_integrity_report(
        str(database),
        network=network,
        chain_id=chain_id,
        protocol="uniswap_v4",
        market_id=args.market_id or market_id,
        anchors=anchors,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    else:
        print(payload)
    if args.fail_on_warning and report.get("warningCount"):
        return 2
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
