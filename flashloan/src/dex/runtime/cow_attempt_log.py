from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from core.db.storage_cow_execution import (
    build_cow_execution_attempts,
    record_cow_execution_attempts,
)
from core.db.storage_common import database_url
from dex.runtime.cow_submission_pause import cow_submission_pause_guard_status

COW_EXECUTION_RETENTION_DAYS = 7


def _refresh_expected_vs_actual_after_success(
    attempts: list[dict],
    *,
    database_url: str,
) -> dict | None:
    """Refresh canonical realized-profit evidence after a real success.

    The analyzer is deliberately invoked only for the two Robinhood runtime
    stores. Test databases and unrelated chains must not overwrite canonical
    evidence files.
    """
    if str(os.environ.get("ROBINHOOD_EXPECTED_VS_ACTUAL_AUTO_REFRESH", "1")).lower() in {
        "0",
        "false",
        "no",
    }:
        return None
    successful = [
        item
        for item in attempts
        if isinstance(item, dict)
        and str(item.get("state") or "").strip().lower() == "execution_success"
        and str(item.get("tx_hash") or item.get("transaction_hash") or "").strip()
    ]
    if not successful:
        return None
    db_text = str(database_url or "")
    if db_text.startswith("sqlite:///"):
        db_text = db_text[10:]
    db_path = Path(db_text).resolve()
    if db_path.name not in {"local_store_4663.sqlite3", "local_store_46630.sqlite3"}:
        return {"ok": False, "reason": "non_canonical_runtime_store"}
    root = Path(__file__).resolve().parents[4]
    analyzer = root / "scripts" / "analyze_robinhood_v4_expected_vs_actual.py"
    if not analyzer.exists():
        return {"ok": False, "reason": "analyzer_missing"}
    command = [sys.executable, str(analyzer), "--store", str(db_path)]
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": "analyzer_launch_failed", "error": str(exc)[:300]}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "reason": None if completed.returncode == 0 else "analyzer_failed",
    }


def _cow_submission_pause_guard(*, database_url: str | None = None) -> dict:
    try:
        return cow_submission_pause_guard_status(database_url=database_url)
    except TypeError:
        return cow_submission_pause_guard_status()


def record_cow_attempt_list_safely(
    attempts: list[dict],
    *,
    database_url: str | None,
    dedupe_market_candidates: bool = True,
) -> dict:
    if not attempts:
        return {"recorded": 0, "source": "empty", "error": None}
    pause_guard = _cow_submission_pause_guard(database_url=database_url)
    if pause_guard.get("paused"):
        return {
            "recorded": 0,
            "source": "paused",
            "error": None,
            "pause_guard": pause_guard,
        }
    resolved_database_url = str(database_url or "").strip()
    if not resolved_database_url:
        resolved_database_url = str(globals()["database_url"]())
    ids = record_cow_execution_attempts(
        resolved_database_url,
        [attempt for attempt in attempts if isinstance(attempt, dict)],
        dedupe_market_candidates=dedupe_market_candidates,
    )
    result = {
        "recorded": len(ids),
        "source": "sqlite",
        "error": None,
        "ids": ids,
        "log_paths": [],
    }
    refresh = _refresh_expected_vs_actual_after_success(
        attempts,
        database_url=resolved_database_url,
    )
    if refresh is not None:
        result["expectedVsActualRefresh"] = refresh
    return result


def record_cow_execution_attempts_safely(
    payload: dict,
    *,
    market_state: dict,
    database_url: str | None,
) -> dict:
    return record_cow_attempt_list_safely(
        build_cow_execution_attempts(payload, market_state=market_state),
        database_url=database_url,
    )


__all__ = [
    "COW_EXECUTION_RETENTION_DAYS",
    "record_cow_attempt_list_safely",
    "record_cow_execution_attempts_safely",
]
