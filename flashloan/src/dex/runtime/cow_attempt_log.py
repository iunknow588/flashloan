from __future__ import annotations

from pathlib import Path

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from core.db.storage_cow_execution import (
    append_cow_execution_attempts_jsonl,
    build_cow_execution_attempts,
)
from dex.runtime.cow_submission_pause import cow_submission_pause_guard_status

# Runtime evidence is shared through the canonical src/runtime directory.
SRC_ROOT = Path(__file__).resolve().parents[2]
COW_EXECUTION_ATTEMPT_LOG_PATH = SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts.jsonl"
COW_EXECUTION_ATTEMPT_LOG_ROOT = SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts"
COW_EXECUTION_RETENTION_DAYS = 7


def cow_execution_attempt_log_path(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    market_state: dict | None = None,
    fallback: Path | None = None,
) -> Path:
    state = market_state if isinstance(market_state, dict) else {}
    state_filter = state.get("cow_filter") if isinstance(state.get("cow_filter"), dict) else {}

    resolved_network = str(
        network
        or state_filter.get("network")
        or state.get("network")
        or state.get("cow_network")
        or ""
    ).strip().lower()
    resolved_chain_id = chain_id
    if resolved_chain_id is None:
        for value in (
            state_filter.get("chain_id"),
            state.get("chain_id"),
            state.get("cow_chain_id"),
        ):
            if value is None or value == "":
                continue
            try:
                resolved_chain_id = int(value)
                break
            except (TypeError, ValueError):
                continue
    row = None
    if resolved_chain_id is not None:
        row = chain_data_by_chain_id(int(resolved_chain_id))
    elif resolved_network:
        row = chain_data_by_network(resolved_network)
    if row:
        resolved_network = str(row.get("network") or resolved_network or "").strip().lower()
        if resolved_chain_id is None:
            try:
                resolved_chain_id = int(row.get("chain_id"))
            except (TypeError, ValueError):
                resolved_chain_id = resolved_chain_id
    if not resolved_network and resolved_chain_id is None:
        if fallback is not None:
            return fallback
        return COW_EXECUTION_ATTEMPT_LOG_PATH
    if not resolved_network:
        row = chain_data_by_network(state.get("network") or state.get("cow_network"))
        if row:
            resolved_network = str(row.get("network") or "").strip().lower()
    scope_network = resolved_network or "unknown"
    scope_chain = str(resolved_chain_id if resolved_chain_id is not None else "no-chain-id")
    return COW_EXECUTION_ATTEMPT_LOG_ROOT / scope_network / scope_chain / "attempts.jsonl"


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
    grouped_attempts: dict[Path, list[dict]] = {}
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        path = cow_execution_attempt_log_path(
            network=attempt.get("network") or attempt.get("cow_network"),
            chain_id=attempt.get("chain_id") or attempt.get("cow_chain_id"),
            market_state=attempt.get("market_state"),
        )
        grouped_attempts.setdefault(path, []).append(attempt)
    file_count = 0
    log_paths: list[str] = []
    for path, rows in grouped_attempts.items():
        file_count += append_cow_execution_attempts_jsonl(
            path,
            rows,
            retention_days=COW_EXECUTION_RETENTION_DAYS,
            dedupe_market_candidates=dedupe_market_candidates,
        )
        log_paths.append(str(path))
    return {"recorded": file_count, "source": "jsonl", "error": None, "log_paths": log_paths}


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
    "COW_EXECUTION_ATTEMPT_LOG_PATH",
    "COW_EXECUTION_ATTEMPT_LOG_ROOT",
    "COW_EXECUTION_RETENTION_DAYS",
    "cow_execution_attempt_log_path",
    "record_cow_attempt_list_safely",
    "record_cow_execution_attempts_safely",
]
