"""Rebuild Robinhood V4 business tables from SQLite only.

This is the DB-first/DB-only maintenance entry point for the Robinhood
mainnet and testnet V4 candidate/observation/loop tables.  The V4 pool,
liquidity snapshot and chain token rows must already be present in SQLite;
the command never reads runtime page shards or JSON evidence files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db import builders  # noqa: E402
from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.storage_schema import ensure_database_schema  # noqa: E402
from dex.intent_trade.chains.robinhood.flash_loan_pool_views import (  # noqa: E402
    clear_robinhood_v4_pool_cache,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (  # noqa: E402
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_MARKET_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
    all_anchor_symbols,
)


def _network_scope(network: str, chain_id: int | None) -> tuple[str, int, str]:
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
    parser.add_argument("--anchors", default="", help="Comma-separated anchor symbols; defaults to all anchors stored in SQLite.")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--no-clear-cache", dest="clear_cache", action="store_false")
    parser.set_defaults(clear_cache=True)
    args = parser.parse_args()

    network, chain_id, market_id = _network_scope(args.network, args.chain_id)
    database = absolute_database_path(args.database)
    ensure_database_schema(str(database))

    import_result = {"ok": True, "skipped": True, "source": "sqlite_only"}

    anchors = tuple(
        all_anchor_symbols(
            network,
            database_url=str(database),
        )
    )
    if args.anchors:
        anchors = tuple(
            item.strip().upper()
            for item in args.anchors.split(",")
            if item.strip()
        )
    if not anchors:
        raise SystemExit("no anchors resolved from SQLite; pass --anchors explicitly")

    if args.verify_only:
        report = builders.verify_anchor_integrity(
            str(database),
            network=network,
            chain_id=chain_id,
            market_id=market_id,
            anchors=anchors,
        )
    else:
        report = builders.build_all_anchor_data(
            str(database),
            network=network,
            chain_id=chain_id,
            market_id=market_id,
            anchors=anchors,
        )

    cleared = 0
    if args.clear_cache:
        cleared = clear_robinhood_v4_pool_cache(
            database_url=str(database),
            network=network,
            chain_id=chain_id,
        )

    print(
        json.dumps(
            {
                "ok": bool(report.get("ok")),
                "database": str(database),
                "network": network,
                "chainId": chain_id,
                "marketId": market_id,
                "anchorSymbols": anchors,
                "import": import_result,
                "verifyOnly": args.verify_only,
                "cacheEntriesCleared": cleared,
                **report,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
