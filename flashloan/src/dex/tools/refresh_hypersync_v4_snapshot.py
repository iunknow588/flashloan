"""Incremental refresh entry point for Robinhood V4 business data.

The refresh currently rebuilds candidate pools, market observation and closed
loops from SQLite after optionally importing existing files. HyperSync/HyperRPC
network pull can be enabled later through ``--source`` once the Rust network
layer is verified; the command already accepts the same switches so the cron
invocation does not need to change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.storage_schema import ensure_database_schema  # noqa: E402
from core.db import builders  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="")
    parser.add_argument("--source", default="sqlite", choices=("sqlite", "hyperrpc", "hypersync"))
    parser.add_argument("--anchor", default="")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--from-block", type=int, default=0)
    parser.add_argument("--to-block", type=int, default=0)
    args = parser.parse_args()

    database = absolute_database_path(args.database)
    ensure_database_schema(str(database))
    anchors = builders.ANCHOR_SYMBOLS
    if args.anchor:
        anchors = tuple(item.strip().upper() for item in args.anchor.split(",") if item.strip())
    if args.verify_only:
        report = builders.verify_anchor_integrity(str(database), anchors=anchors)
    else:
        report = builders.build_all_anchor_data(str(database), anchors=anchors)
    print(
        json.dumps(
            {
                "ok": True,
                "database": str(database),
                "source": args.source,
                "fromBlock": args.from_block,
                "toBlock": args.to_block,
                **report,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
