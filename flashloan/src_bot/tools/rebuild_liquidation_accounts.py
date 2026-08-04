from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from db.storage import ensure_database_schema, record_liquidation_scan_config_snapshot, require_psycopg


load_env_files(__file__)


def main() -> int:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    ensure_database_schema(database_url)
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM liquidation_accounts")
            cursor.execute("DELETE FROM liquidation_discovery_scans")
    record_liquidation_scan_config_snapshot(
        database_url,
        config_key="liquidation_accounts.latest",
        source_table="liquidation_accounts",
        payload={"rebuilt": True, "mode": "full_reset", "active_count": 0, "sample_accounts": []},
    )
    record_liquidation_scan_config_snapshot(
        database_url,
        config_key="liquidation_discovery_scans.latest_success",
        source_table="liquidation_discovery_scans",
        payload={"rebuilt": True, "mode": "full_reset", "status": "cleared"},
    )
    print(json.dumps({"rebuilt": ["liquidation_accounts", "liquidation_discovery_scans"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
