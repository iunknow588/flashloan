"""Shared RuntimeCore adapter and comparison helpers."""

from __future__ import annotations

import sys
from typing import Any

from flask import has_app_context
from core.manager_api_client import ManagerApiClient, ManagerApiClientError, manager_ui_data_source

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


def _compat_runtime_core_manager_overview_envelope(**kwargs: Any) -> dict[str, Any]:
    facade = sys.modules.get("web.data_routes.manager_page_compare")
    resolver = (
        getattr(facade, "runtime_core_manager_overview_envelope", None)
        if facade is not None
        else None
    )
    if callable(resolver):
        return resolver(**kwargs)
    from web.data_routes.manager_overview_payload import (
        runtime_core_manager_overview_envelope,
    )

    return runtime_core_manager_overview_envelope(**kwargs)


def _manager_read_envelope(
    path: str,
    *,
    query: dict[str, object],
    request_id: str | None,
) -> dict[str, Any]:
    """Read a page view through RuntimeCore, with a non-request test adapter."""

    view = RUNTIME_CORE_MANAGER_VIEWS.get(str(path or "").strip())
    if view is None:
        return _compat_manager_client().from_env().get_json(
            path,
            query=query,
            request_id=request_id,
        )
    if not has_app_context():
        # Direct unit calls have no Flask runtime boundary. Production page
        # routes always execute inside an app context and therefore fail closed
        # when their RuntimeCore binding is missing.
        return _compat_manager_client().from_env().get_json(
            path,
            query=query,
            request_id=request_id,
        )
    chain_id = _int_or_none(query.get("chainId"))
    if chain_id is None or chain_id <= 0:
        raise ManagerApiClientError(
            "MANAGER_CHAIN_ID_REQUIRED",
            "RuntimeCore manager view requires a positive chainId",
            status=400,
        )
    limit = _int_or_none(query.get("limit")) or 200
    options = {
        str(key): value
        for key, value in query.items()
        if key not in {"chainId", "limit"} and value is not None
    }
    envelope = runtime_core_manager_view_envelope(
        view=view,
        limit=limit,
        options=options,
        request_id=request_id,
    )
    if envelope.get("ok") is False:
        raise ManagerApiClientError(
            str(envelope.get("code") or "RUNTIME_CORE_VIEW_READ_FAILED"),
            str(envelope.get("detail") or "RuntimeCore manager view failed"),
            status=_int_or_none(envelope.get("upstreamStatus")) or 503,
            response_payload=envelope,
        )
    if _int_or_none(envelope.get("chainId")) != int(chain_id):
        raise ManagerApiClientError(
            "RUNTIME_CORE_SCOPE_MISMATCH",
            "RuntimeCore manager view chainId does not match the requested chain",
            status=503,
        )
    return envelope


def manager_compare_requested(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _shadow_gate(feature_name: str) -> dict[str, Any] | None:
    try:
        data_source = manager_ui_data_source()
    except ManagerApiClientError as exc:
        return _shadow_error(
            exc.code,
            exc.message,
            data_source="legacy",
        )
    if data_source != "manager":
        return {
            "ok": False,
            "source": "manager",
            "enabled": False,
            "legacyUnaffected": True,
            "code": "MANAGER_UI_DATA_SOURCE_LEGACY",
            "detail": f"{feature_name} shadow comparison is disabled while MANAGER_UI_DATA_SOURCE=legacy",
        }
    return None


def runtime_core_manager_view_envelope(
    *,
    view: str,
    limit: int,
    options: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read one page projection from the request-bound Rust RuntimeCore."""

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
        if options:
            envelope = chain_runtime.manager_view(
                str(view or "").strip(),
                int(limit),
                options=dict(options),
            )
        else:
            envelope = chain_runtime.manager_view(str(view or "").strip(), int(limit))
    except (TypeError, ValueError, RuntimeError) as exc:
        return _manager_read_error(
            "RUNTIME_CORE_VIEW_READ_FAILED",
            f"RuntimeCore {view} view failed: {str(exc)[:500]}",
            upstream_status=503,
        )
    if not isinstance(envelope, dict):
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            f"RuntimeCore {view} view must return an object",
            upstream_status=503,
        )
    if envelope.get("ok") is False:
        return envelope
    if envelope.get("dataSource") != "rust_runtime_core":
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            f"RuntimeCore {view} view has an unexpected dataSource",
            upstream_status=503,
        )
    if envelope.get("view") != str(view or "").strip():
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            f"RuntimeCore returned an unexpected view for {view}",
            upstream_status=503,
        )
    if not isinstance(envelope.get("data"), dict):
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            f"RuntimeCore {view} view must contain an object data field",
            upstream_status=503,
        )
    runtime_core = (
        envelope.get("runtimeCore")
        if isinstance(envelope.get("runtimeCore"), dict)
        else {}
    )
    if _int_or_none(runtime_core.get("chainId")) is None:
        return _manager_read_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            f"RuntimeCore {view} view must include runtimeCore.chainId",
            upstream_status=503,
        )
    return envelope


def _manager_snapshot(
    path: str,
    *,
    query: dict[str, object],
    request_id: str | None,
    invalid_detail: str,
) -> dict[str, Any]:
    try:
        envelope = _manager_read_envelope(
            path,
            query=query,
            request_id=request_id,
        )
    except ManagerApiClientError as exc:
        return {
            "ok": False,
            "source": "manager",
            "code": exc.code,
            "detail": exc.message,
            "upstreamStatus": exc.status,
        }
    data = envelope.get("data")
    if not isinstance(data, dict):
        return {
            "ok": False,
            "source": "manager",
            "code": "MANAGER_API_INVALID_RESPONSE",
            "detail": invalid_detail,
        }
    return {
        "ok": True,
        "managerEnvelopeVersion": envelope.get("version"),
        "data": data,
    }


def _comparison_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "available": False,
            "managerRowCount": None,
            "legacyRowCount": None,
            "managerOnlyCount": None,
            "legacyOnlyCount": None,
            "fieldMismatchCount": None,
            "totalDifferenceCount": None,
        }
    manager_only = _int_or_none(value.get("managerOnlyCount"))
    legacy_only = _int_or_none(value.get("legacyOnlyCount"))
    field_mismatch = _int_or_none(value.get("fieldMismatchCount"))
    total = None
    if None not in (manager_only, legacy_only, field_mismatch):
        total = manager_only + legacy_only + field_mismatch
    return {
        "available": True,
        "managerRowCount": _int_or_none(value.get("managerRowCount")),
        "legacyRowCount": _int_or_none(value.get("legacyRowCount")),
        "managerOnlyCount": manager_only,
        "legacyOnlyCount": legacy_only,
        "fieldMismatchCount": field_mismatch,
        "totalDifferenceCount": total,
    }


def _shadow_error(
    code: str,
    message: str,
    *,
    data_source: str,
    upstream_status: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "manager",
        "enabled": True,
        "legacyUnaffected": True,
        "code": code,
        "detail": message,
        "observedDataSource": data_source,
    }
    if upstream_status is not None:
        payload["upstreamStatus"] = upstream_status
    return payload


def _manager_read_error(
    code: str,
    message: str,
    *,
    upstream_status: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "source": "manager",
        "dataSource": "manager",
        "code": code,
        "detail": message,
    }
    if upstream_status is not None:
        payload["upstreamStatus"] = upstream_status
    return payload


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mapping_value(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_length(value: object) -> int | None:
    return len(value) if isinstance(value, list) else None


def _delta(legacy: int | None, manager: int | None) -> int | None:
    if legacy is None or manager is None:
        return None
    return manager - legacy
