"""RuntimeCore command adapters for Candidate Exchange Manager refreshes."""

from __future__ import annotations

from typing import Any

from flask import has_app_context

from .manager_page_compare_common import _int_or_none, _shadow_error


def candidate_exchange_refresh_manager_payload(
    *,
    chain_id: int | None,
    dry_run: bool,
    request_id: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Rebuild Candidate Exchange Manager state through RuntimeCore."""

    if chain_id is None:
        return _shadow_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "candidate exchange refresh requires a selected chain id",
            data_source="manager",
            upstream_status=400,
        )
    if not str(request_id or "").strip():
        return _shadow_error(
            "MANAGER_REQUEST_ID_REQUIRED",
            "candidate exchange refresh requires a request id",
            data_source="manager",
            upstream_status=400,
        )
    if not str(idempotency_key or "").strip():
        return _shadow_error(
            "MANAGER_IDEMPOTENCY_KEY_REQUIRED",
            "candidate exchange refresh requires an idempotency key",
            data_source="manager",
            upstream_status=400,
        )
    if not has_app_context():
        return _shadow_error(
            "RUNTIME_CORE_UNAVAILABLE",
            "chain runtime is not bound to this request",
            data_source="manager",
            upstream_status=503,
        )
    from flask import g

    try:
        chain_runtime = getattr(g, "chain_runtime", None)
    except RuntimeError:
        chain_runtime = None
    if chain_runtime is None:
        return _shadow_error(
            "RUNTIME_CORE_UNAVAILABLE",
            "chain runtime is not bound to this request",
            data_source="manager",
            upstream_status=503,
        )
    if not hasattr(chain_runtime, "execute_command"):
        return _shadow_error(
            "RUNTIME_CORE_COMMAND_UNAVAILABLE",
            "chain runtime does not expose RuntimeCore commands",
            data_source="manager",
            upstream_status=503,
        )
    try:
        envelope = chain_runtime.execute_command(
            "rebuild_candidate_exchange_pools",
            request_id=str(request_id).strip(),
            idempotency_key=str(idempotency_key).strip(),
            dry_run=bool(dry_run),
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return _shadow_error(
            "RUNTIME_CORE_COMMAND_FAILED",
            f"RuntimeCore rebuild_candidate_exchange_pools command failed: {str(exc)[:500]}",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope, dict):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore rebuild_candidate_exchange_pools command must return an object",
            data_source="manager",
            upstream_status=503,
        )
    if envelope.get("ok") is False:
        return _shadow_error(
            str(envelope.get("code") or "RUNTIME_CORE_COMMAND_FAILED"),
            str(
                envelope.get("detail")
                or "RuntimeCore rebuild_candidate_exchange_pools command failed"
            ),
            data_source="manager",
            upstream_status=(
                envelope.get("upstreamStatus")
                if isinstance(envelope.get("upstreamStatus"), int)
                else 503
            ),
        )
    if envelope.get("command") != "rebuild_candidate_exchange_pools":
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore returned an unexpected candidate exchange command",
            data_source="manager",
            upstream_status=503,
        )
    if envelope.get("dataSource") != "rust_runtime_core":
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore candidate exchange command has an unexpected dataSource",
            data_source="manager",
            upstream_status=503,
        )
    if _int_or_none(envelope.get("chainId")) != int(chain_id):
        return _shadow_error(
            "RUNTIME_CORE_SCOPE_MISMATCH",
            "RuntimeCore candidate exchange command chainId does not match the requested chain",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope.get("dryRun"), bool) or envelope.get("dryRun") != bool(
        dry_run
    ):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore candidate exchange command must preserve the requested dryRun flag",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope.get("idempotentReplay"), bool):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore candidate exchange command must include Boolean idempotentReplay",
            data_source="manager",
            upstream_status=503,
        )
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "runtimeDataSource": envelope.get("dataSource"),
        "managerEnvelopeVersion": envelope.get("version"),
        "dryRun": bool(envelope.get("dryRun")),
        "idempotentReplay": bool(envelope.get("idempotentReplay")),
        "candidateEdgeVersion": _int_or_none(envelope.get("candidateEdgeVersion")),
        "candidateEdgeChecksum": envelope.get("candidateEdgeChecksum"),
        "poolScopeChecksum": envelope.get("poolScopeChecksum"),
        "anchorCount": _int_or_none(envelope.get("anchorCount")) or 0,
        "tokenCount": _int_or_none(envelope.get("tokenCount")) or 0,
        "directionCount": _int_or_none(envelope.get("directionCount")) or 0,
        "poolCount": _int_or_none(envelope.get("poolCount")) or 0,
        "observationVersion": _int_or_none(envelope.get("observationVersion")),
        "materializedChecksum": envelope.get("materializedChecksum"),
        "changedRows": _int_or_none(envelope.get("changedRows")) or 0,
        "consumersNeedRebuild": bool(envelope.get("consumersNeedRebuild")),
        "managerResult": envelope,
    }


__all__ = ["candidate_exchange_refresh_manager_payload"]
