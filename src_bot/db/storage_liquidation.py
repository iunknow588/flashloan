from datetime import datetime
from typing import Any, Iterable

from db.storage_liquidation_accounts import (
    load_latest_liquidation_account_reports,
    prune_liquidation_accounts,
    record_liquidation_account_scan,
    record_liquidation_account_scans,
)
from db.storage_liquidation_attempts import (
    liquidation_execution_attempt_stats,
    load_liquidation_execution_attempts_for_account,
    load_liquidation_failure_samples_for_account,
    load_latest_liquidation_execution_attempts_for_accounts,
    load_recent_liquidation_execution_attempts,
    load_recent_liquidation_failure_samples,
    record_liquidation_execution_attempt,
    record_liquidation_failure_sample,
)
from db.storage_liquidation_legacy import (
    liquidation_account_registry_stats,
    liquidation_discovery_scan_progress,
    load_liquidation_accounts,
    load_liquidation_accounts_page,
    load_liquidation_scan_config_library,
    rebuild_liquidation_scan_config_library,
    record_liquidation_discovery_scan,
    record_liquidation_scan_config_snapshot,
    try_acquire_observer_lock,
    upsert_liquidation_accounts,
)

__all__ = [
    "liquidation_account_registry_stats",
    "liquidation_discovery_scan_progress",
    "liquidation_execution_attempt_stats",
    "load_latest_liquidation_account_reports",
    "load_liquidation_accounts",
    "load_liquidation_accounts_page",
    "load_liquidation_execution_attempts_for_account",
    "load_liquidation_failure_samples_for_account",
    "load_latest_liquidation_execution_attempts_for_accounts",
    "load_liquidation_scan_config_library",
    "load_recent_liquidation_execution_attempts",
    "load_recent_liquidation_failure_samples",
    "prune_liquidation_accounts",
    "rebuild_liquidation_scan_config_library",
    "record_liquidation_account_scan",
    "record_liquidation_account_scans",
    "record_liquidation_discovery_scan",
    "record_liquidation_execution_attempt",
    "record_liquidation_failure_sample",
    "record_liquidation_scan_config_snapshot",
    "try_acquire_observer_lock",
    "upsert_liquidation_accounts",
]


def _typing_anchors(
    database_url: str,
    accounts: Iterable[str],
    timestamp: datetime | None,
    payload: dict[str, Any],
) -> None:
    return None
