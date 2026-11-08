"""Read-only Robinhood V4 scope index CLI.

Usage:
    python scripts/refresh_robinhood_v4_scope_index.py --chain-id 46630 --verify-only
    python scripts/refresh_robinhood_v4_scope_index.py --chain-id 46630 --pool-id 0x...

The CLI only reads candidate/observation/closed-loop tables and reports the
persisted ``chain_business_initialization`` cursor.  It never writes business
rows and never starts maintenance threads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def _default_database(chain_id: int) -> str:
    candidate = Path("flashloan") / "db" / f"local_store_{int(chain_id)}.sqlite3"
    if candidate.exists():
        return str(candidate)
    from core.db.database_path import absolute_database_path

    return str(absolute_database_path(None))


def _write(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain-id", type=int, default=46630)
    parser.add_argument("--network", default="robinhood-testnet")
    parser.add_argument("--protocol", default="uniswap_v4")
    parser.add_argument("--market-id", default="dex-arbitrage-46630-uniswap-v4")
    parser.add_argument("--database", default=None)
    parser.add_argument("--store", dest="database", default=None)
    parser.add_argument("--pool-id", default=None)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)

    from dex.runtime.v4_scope_indexer import (
        load_scope_indexer_cursor,
        query_pool_scope,
    )

    database = str(args.database or _default_database(args.chain_id)).strip()
    kwargs = {
        "network": args.network,
        "chain_id": args.chain_id,
        "protocol": args.protocol,
        "market_id": args.market_id,
    }
    if args.pool_id:
        payload = query_pool_scope(database, pool_id=args.pool_id, **kwargs)
        payload["database"] = database
        payload["verifyOnly"] = bool(args.verify_only)
        _write(payload)
        return 0 if payload.get("ok") else 1
    if not args.verify_only:
        parser.error("refreshing the projection is not implemented; use --verify-only")
    cursor = load_scope_indexer_cursor(
        database,
        network=args.network,
        chain_id=args.chain_id,
        protocol=args.protocol,
    )
    _write(
        {
            "ok": True,
            "mode": "verify_only",
            "database": database,
            "chainId": args.chain_id,
            "cursor": cursor,
            "note": "read-only projection; no business tables were modified",
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
