from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.db.storage_cow_execution import (
    load_recent_cow_execution_attempts,
    prune_cow_execution_attempts,
    record_cow_execution_attempts,
)


def test_prune_cow_execution_attempts_deletes_old_rows(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    old_created_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    ids = record_cow_execution_attempts(
        path,
        [
            {
                "network": "bnb",
                "pair": "USDC/BTC",
                "state": "execution_success",
                "created_at": old_created_at,
            }
        ],
    )

    assert len(ids) == 1
    assert len(load_recent_cow_execution_attempts(path, limit=10)) == 1

    assert prune_cow_execution_attempts(path, retention_days=7) == 1
    assert load_recent_cow_execution_attempts(path, limit=10) == []
