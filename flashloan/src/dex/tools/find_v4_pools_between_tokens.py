"""Find every Uniswap V4 pool between two token addresses.

This is the reliability entry point for two-token discovery.  It reads the
existing SQLite pool index through the Rust/Python bridge; the optional
``--source hyperrpc`` path can be added later for direct on-chain verification.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

SRC_ROOT = __file__.resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core import rs_bridge  # noqa: E402


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("token_a", help="First token address or symbol.")
    parser.add_argument("token_b", help="Second token address or symbol.")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--database", default="")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    database = absolute_database_path(args.database)
    if not database.exists():
        print(json.dumps({"ok": False, "reason": "database_missing", "path": str(database)}))
        return 1
    payload = rs_bridge.find_pools_between_tokens(
        args.network,
        args.chain_id,
        args.token_a,
        args.token_b,
        database_url=str(database),
    )
    if not payload.get("ok"):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    pools = payload.get("pools") or []
    fixed = [
        pool
        for pool in pools
        if not pool.get("dynamic_fee") and not pool.get("special_fee")
    ]
    summary = {
        "ok": True,
        "network": args.network,
        "chainId": args.chain_id,
        "tokenA": args.token_a,
        "tokenB": args.token_b,
        "currency0": payload.get("currency0"),
        "currency1": payload.get("currency1"),
        "poolCount": len(pools),
        "fixedFeePoolCount": len(fixed),
        "source": payload.get("source") or "rust_sqlite",
        "database": str(database),
    }
    if not args.summary_only:
        summary["pools"] = pools
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
