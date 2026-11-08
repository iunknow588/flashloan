"""Shared RuntimeCore adapter and comparison helpers."""

from __future__ import annotations

import sys
from typing import Any

from flask import has_app_context
from core.manager_api_client import ManagerApiClient, ManagerApiClientError, manager_ui_data_source
from dex.runtime.closed_loop_identity import (
    directed_symbol_key_from_symbols,
    physical_loop_key_from_route,
    worker_group_key_from_symbols,
)

CANDIDATE_SNAPSHOT_LIMIT = 1000
CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT = 200
CLOSED_LOOP_SNAPSHOT_LIMIT = 1000
CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT = 200
CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT = 1000
CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT = 200
RUNTIME_CORE_OVERVIEW_VIEW = "overview"
RUNTIME_CORE_OVERVIEW_LIMIT = CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT
SUBMISSION_SNAPSHOT_LIMIT = 1000
RUNTIME_CORE_MANAGER_VIEWS = {
    "/v1/candidate-edge-pools": "candidate_edge",
    "/v1/candidate-exchange-pools": "candidate_exchange",
    "/v1/closed-loops": "closed_loops",
    "/v1/reviews": "reviews",
    "/v1/broadcasts": "broadcasts",
}
SUBMISSION_FAILURE_CATEGORY_ORDER = ("all", "broadcast", "submission", "code", "rpc", "execution", "quote", "blocked", "deferred", "other")

def _compat_manager_client():
    facade = sys.modules.get("web.data_routes.manager_page_compare")
    return getattr(facade, "ManagerApiClient", ManagerApiClient) if facade is not None else ManagerApiClient

from .manager_page_compare_common import (
    RUNTIME_CORE_OVERVIEW_LIMIT,
    RUNTIME_CORE_OVERVIEW_VIEW,
    ManagerApiClientError,
    _compat_runtime_core_manager_overview_envelope,
    _int_or_none,
    _manager_read_error,
)


def overview_manager_page_payload(
    *,
    chain_id: int,
    protocols: list[str],
    chain_selection: dict[str, Any] | None = None,
    request_id: str | None = None,
    overview_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the manager-owned overview projection through RuntimeCore."""

    envelope = overview_envelope or _compat_runtime_core_manager_overview_envelope(
        chain_id=int(chain_id),
        limit=RUNTIME_CORE_OVERVIEW_LIMIT,
        request_id=request_id,
    )
    data = envelope.get("data")
    if envelope.get("ok") is False or not isinstance(data, dict):
        return _manager_read_error(
            envelope.get("code") or "RUNTIME_CORE_INVALID_RESPONSE",
            envelope.get("detail")
            or "overview manager response must contain an object data field",
            upstream_status=envelope.get("upstreamStatus"),
        )
    contract_error = _overview_manager_contract_error(data, chain_id=chain_id)
    if contract_error is not None:
        return contract_error
    payload = dict(data)
    payload.update(
        {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": "dex_overview",
            "chain_id": int(chain_id),
            "requested_protocols": list(protocols),
            "chain_selection": dict(chain_selection or {}),
            "managerEnvelopeVersion": envelope.get("version"),
            "runtimeDataSource": envelope.get("dataSource") or "rust_runtime_core",
            "runtimeCore": dict(envelope.get("runtimeCore") or {}),
        }
    )
    return payload


def runtime_core_manager_overview_envelope(
    *,
    chain_id: int,
    limit: int = RUNTIME_CORE_OVERVIEW_LIMIT,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the overview projection from the request-bound RuntimeCore.

    This is the only page read path for migrated overview-backed endpoints.  The
    Flask adapter converts RuntimeCore errors to a fail-closed manager payload.
    """
    _ = request_id
    from flask import g

    try:
        chain_runtime = getattr(g, "chain_runtime", None)
    except RuntimeError:
        chain_runtime = None
    if chain_runtime is None:
        return _manager_read_error(
            "RUNTIME_CORE_UNAVAILABLE",
            "chain runtime is not bound to this request",
            upstream_status=503,
        )
    if not hasattr(chain_runtime, "manager_view"):
        return _manager_read_error(
            "RUNTIME_CORE_VIEW_UNAVAILABLE",
            "chain runtime does not expose RuntimeCore manager views",
            upstream_status=503,
        )
    try:
        envelope = chain_runtime.manager_view(RUNTIME_CORE_OVERVIEW_VIEW, int(limit))
    except Exception as exc:
        return _manager_read_error(
            "RUNTIME_CORE_VIEW_READ_FAILED",
            f"RuntimeCore overview view failed: {str(exc)[:500]}",
            upstream_status=503,
        )
    if not isinstance(envelope, dict):
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore overview view must return an object",
            upstream_status=503,
        )
    if envelope.get("dataSource") != "rust_runtime_core":
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore overview view has an unexpected dataSource",
            upstream_status=503,
        )
    data = envelope.get("data")
    if not isinstance(data, dict):
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore overview view must contain an object data field",
            upstream_status=503,
        )
    runtime_core = envelope.get("runtimeCore") if isinstance(envelope.get("runtimeCore"), dict) else {}
    if _int_or_none(runtime_core.get("chainId")) != int(chain_id):
        return _manager_read_error(
            "RUNTIME_CORE_SCOPE_MISMATCH",
            "RuntimeCore overview view chainId does not match the requested chain",
            upstream_status=503,
        )
    return envelope


def _overview_manager_contract_error(
    data: dict[str, Any],
    *,
    chain_id: int,
) -> dict[str, Any] | None:
    if _int_or_none(data.get("chainId")) != int(chain_id):
        return _manager_read_error(
            "MANAGER_OVERVIEW_SCOPE_MISMATCH",
            "overview manager response chainId does not match the requested chain",
        )
    if not isinstance(data.get("enabled"), bool):
        return _manager_read_error(
            "MANAGER_API_INVALID_RESPONSE",
            "overview manager response field enabled must be a boolean",
        )
    for field in (
        "source",
        "network",
        "protocol",
        "scopeKey",
        "generatedAt",
        "decisionBasis",
        "marketObservationSource",
        "marketObservationSourceStage",
    ):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                f"overview manager response field {field} must be a non-empty string",
            )
    for field in (
        "summary",
        "routeFramework",
        "flashLoanLiquidity",
        "poolStaticTable",
        "versions",
        "dataQuality",
        "pipelineStatus",
    ):
        if not isinstance(data.get(field), dict):
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                f"overview manager response field {field} must be an object",
            )
    for field in (
        "selectedProtocols",
        "arbitragePaths",
        "arbitragePathGroups",
        "watchPairs",
        "liquidPoolRegistry",
    ):
        if not isinstance(data.get(field), list):
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                f"overview manager response field {field} must be an array",
            )
    return None


def _closed_loop_control_snapshot(chain_id: int) -> dict[str, Any]:
    try:
        from core.runtime.chain_runtime import chain_runtime_registry

        payload = chain_runtime_registry().get(chain_id).closed_loop_control_status()
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc)[:300],
            "workers": [],
            "executionTasks": [],
        }
    workers = [
        worker
        for worker in payload.get("workers", [])
        if isinstance(worker, dict)
    ] if isinstance(payload, dict) else []
    execution_queue = (
        payload.get("executionQueue")
        if isinstance(payload, dict) and isinstance(payload.get("executionQueue"), dict)
        else {}
    )
    execution_tasks = [
        task
        for task in execution_queue.get("recent", [])
        if isinstance(task, dict)
    ]
    return {
        "available": True,
        "workers": workers,
        "executionTasks": execution_tasks,
        "reviewQueue": payload.get("reviewQueue") if isinstance(payload, dict) else {},
        "executionQueue": execution_queue,
    }


def _attach_closed_loop_control(
    routes: list[dict[str, Any]],
    *,
    chain_id: int,
) -> dict[str, Any]:
    snapshot = _closed_loop_control_snapshot(chain_id)
    workers_by_group = {
        str(worker.get("group_key") or "").strip().lower(): worker
        for worker in snapshot["workers"]
        if str(worker.get("group_key") or "").strip()
    }
    tasks_by_group = {
        str(task.get("group_key") or "").strip().lower(): task
        for task in snapshot["executionTasks"]
        if str(task.get("group_key") or "").strip()
    }
    for route in routes:
        route_symbols = list(route.get("routeSymbols") or [])
        group_key = worker_group_key_from_symbols(route_symbols)
        loop_key = physical_loop_key_from_route(route)
        direction_key = directed_symbol_key_from_symbols(route_symbols)
        worker = workers_by_group.get(group_key or "", {})
        task = tasks_by_group.get(group_key or "", {})
        worker_state = str(worker.get("state") or "").strip().lower()
        task_state = str(task.get("state") or "").strip().lower()
        route["closedLoopControl"] = {
            "loopKey": loop_key,
            "directionKey": direction_key,
            "groupKey": group_key or None,
            "workerGroupKey": group_key or None,
            "directionStateAvailable": bool(loop_key),
            "review": {
                "state": worker_state or "missing",
                "phase": worker.get("phase"),
                "threadLive": worker.get("thread_live"),
                "reviewWaitReady": bool(worker.get("review_wait_ready")),
                "manualWakeEligible": bool(
                    worker_state == "idle"
                    and worker.get("thread_live") is not False
                    and worker.get("review_wait_ready") is True
                ),
            },
            "broadcast": {
                "state": task_state or "missing",
                "availableAt": task.get("available_at"),
                "manualWakeEligible": task_state == "waiting",
            },
        }
    return snapshot
