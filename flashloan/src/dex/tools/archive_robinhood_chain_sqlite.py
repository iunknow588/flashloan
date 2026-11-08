"""Export a Robinhood chain-scoped SQLite archive with ``VACUUM INTO``.

The live database remains the only official data source
(``flashloan/db/local_store.sqlite3``).  This command creates a
read-only timestamped archive copy for one Robinhood chain, so operators can
preserve per-chain evidence without creating a second runtime database or a
nested chain-specific DB that live code reads.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from dex.intent_trade.chains.robinhood_common.chain_config import (  # noqa: E402
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_TESTNET_CHAIN_ID,
)


SUPPORTED_CHAIN_IDS = {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_archive_dir() -> Path:
    return SRC_ROOT / "runtime" / "evidence" / "chain_archive"


def _validate_source_database(database: Path) -> None:
    resolved = database.resolve()
    expected = (SRC_ROOT.parent / "db" / "local_store.sqlite3").resolve()
    if resolved != expected:
        raise ValueError(
            "refusing to archive a non-official database; expected "
            f"{expected}, got {resolved}"
        )
    if not resolved.is_file():
        raise FileNotFoundError(f"official database not found: {resolved}")


def build_chain_archive_path(
    *,
    chain_id: int,
    database: Path | None = None,
    output_dir: Path | None = None,
    stamp: str | None = None,
) -> Path:
    """Return the archive path for one chain without creating any file."""
    chain_id = int(chain_id)
    if chain_id not in SUPPORTED_CHAIN_IDS:
        raise ValueError(f"unsupported Robinhood chain_id: {chain_id}")
    if database is not None:
        _validate_source_database(Path(database))
    root = Path(output_dir) if output_dir is not None else _default_archive_dir()
    return root / f"chain_{chain_id}_archive_{stamp or _stamp()}.sqlite3"


def archive_chain_sqlite(
    *,
    chain_id: int,
    database: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, object]:
    """Create a timestamped per-chain archive copy of the official DB."""
    source = Path(database)
    _validate_source_database(source)
    target = Path(output_path) if output_path is not None else build_chain_archive_path(
        chain_id=chain_id,
        database=source,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"archive target already exists: {target}")

    conn = sqlite3.connect(str(source))
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
        conn.commit()
    finally:
        conn.close()
    return {
        "chainId": int(chain_id),
        "database": str(source),
        "archivePath": str(target),
        "archiveBytes": target.stat().st_size,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schemaVersion": "robinhood-chain-sqlite-archive-v1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="", help="Must be the official local_store.sqlite3.")
    parser.add_argument(
        "--chain-id",
        type=int,
        required=True,
        choices=sorted(SUPPORTED_CHAIN_IDS),
        help="Robinhood chain to scope the archive (4663 or 46630).",
    )
    parser.add_argument("--output", default="", help="Optional explicit archive path.")
    args = parser.parse_args()
    database = absolute_database_path(args.database)
    result = archive_chain_sqlite(
        chain_id=args.chain_id,
        database=database,
        output_path=args.output or None,
    )
    print(__import__("json").dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
