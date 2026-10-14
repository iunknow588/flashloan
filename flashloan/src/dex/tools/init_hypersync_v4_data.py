"""Initialise Robinhood V4 business data from existing JSON/SQLite sources.

The script currently bootstraps the independent tables from the existing
evidence files and then rebuilds candidate pools, market observation and
closed loops directly from SQLite. HyperSync/HyperRPC network pull can be
enabled through ``--source`` once the Rust network layer is verified.
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
from core.db.import_existing_json import import_existing_json  # noqa: E402
from core.db import builders  # noqa: E402
from dex.intent_trade.chains.robinhood.flash_loan_pool_views import (  # noqa: E402
    clear_robinhood_v4_pool_cache,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="")
    parser.add_argument("--runtime-root", default="")
    parser.add_argument("--import-existing-files", action="store_true", default=True)
    parser.add_argument("--no-import-existing-files", dest="import_existing_files", action="store_false")
    parser.add_argument("--source", default="sqlite", choices=("sqlite", "hyperrpc", "hypersync"))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--anchor", default="")
    args = parser.parse_args()

    database = absolute_database_path(args.database)
    ensure_database_schema(str(database))
    if args.import_existing_files and not args.verify_only:
        import_result = import_existing_json(
            str(database),
            runtime_root=args.runtime_root or None,
        )
    else:
        import_result = {"ok": True, "skipped": True}

    anchors = builders.ANCHOR_SYMBOLS
    if args.anchor:
        anchors = tuple(item.strip().upper() for item in args.anchor.split(",") if item.strip())
    if args.verify_only:
        report = builders.verify_anchor_integrity(str(database), anchors=anchors)
    else:
        report = builders.build_all_anchor_data(str(database), anchors=anchors)
    clear_robinhood_v4_pool_cache(database_url=str(database))
    print(
        json.dumps(
            {
                "ok": True,
                "database": str(database),
                "import": import_result,
                "source": args.source,
                "verify_only": args.verify_only,
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
