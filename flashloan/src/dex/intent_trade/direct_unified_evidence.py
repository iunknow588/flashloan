"""Evidence and circuit-breaker helpers for the unified direct execution path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def direct_circuit_status(
    *,
    network: str,
    route_key: Any = "",
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    if not impl._direct_circuit_enabled():
        return {"enabled": False, "paused": False, "level": "green"}
    state_path = path or impl._direct_circuit_breaker_path()
    payload = impl._read_json_file(state_path)
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    key = impl._direct_circuit_group_key(network, route_key)
    group = groups.get(key) if isinstance(groups.get(key), dict) else {}
    paused_until = impl._parse_utc_datetime(group.get("pausedUntil"))
    current = now or impl._utc_now()
    paused = bool(paused_until and paused_until > current)
    return {
        "enabled": True,
        "paused": paused,
        "level": str(group.get("level") or "green"),
        "reason": str(group.get("reason") or ""),
        "groupKey": key,
        "pausedUntil": paused_until.isoformat() if paused_until else "",
        "state": group,
        "file": str(state_path),
    }


def direct_pre_pause_status(path: Path | None = None) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    state_path = path or impl._direct_pre_pause_path()
    payload = impl._read_json_file(state_path)
    paused = bool(payload.get("prePause"))
    detected_at = str(payload.get("pauseDetectedAt") or payload.get("updatedAt") or "")
    signer_blocked_at = str(payload.get("signerBlockedAt") or payload.get("updatedAt") or "")
    propagation_ms = payload.get("pausePropagationMs")
    return {
        "prePause": paused,
        "reason": str(payload.get("reason") or ""),
        "updatedAt": str(payload.get("updatedAt") or ""),
        "setBy": str(payload.get("setBy") or ""),
        "pauseTriggerSource": str(payload.get("pauseTriggerSource") or ""),
        "pauseDetectedAt": detected_at,
        "signerBlockedAt": signer_blocked_at,
        "pausePropagationMs": str(propagation_ms if propagation_ms is not None else ""),
        "pauseTxSentAt": str(payload.get("pauseTxSentAt") or ""),
        "pauseTxConfirmedAt": str(payload.get("pauseTxConfirmedAt") or ""),
        "blockedCandidateCount": str(payload.get("blockedCandidateCount") or 0),
        "file": str(state_path),
    }


def set_direct_pre_pause(
    paused: bool,
    *,
    reason: str,
    set_by: str = "operator",
    pause_trigger_source: str = "manual",
    pause_detected_at: Any = None,
    pause_tx_sent_at: Any = None,
    pause_tx_confirmed_at: Any = None,
    blocked_candidate_count: Any = 0,
    path: Path | None = None,
) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    state_path = path or impl._direct_pre_pause_path()
    current = impl._utc_now()
    detected_at = impl._parse_utc_datetime(pause_detected_at) or current
    propagation_ms = max(0, int((current - detected_at).total_seconds() * 1000)) if paused else ""
    payload = {
        "schemaVersion": 1,
        "prePause": bool(paused),
        "reason": str(reason or ("manual_resume" if not paused else "operator_pre_pause")),
        "setBy": str(set_by or "operator"),
        "pauseTriggerSource": str(pause_trigger_source or ("manual" if paused else "")),
        "updatedAt": current.isoformat(),
        "pauseDetectedAt": detected_at.isoformat() if paused else "",
        "signerBlockedAt": current.isoformat() if paused else "",
        "pausePropagationMs": str(propagation_ms),
        "pauseTxSentAt": str(pause_tx_sent_at or ""),
        "pauseTxConfirmedAt": str(pause_tx_confirmed_at or ""),
        "blockedCandidateCount": str(blocked_candidate_count or 0),
    }
    impl._write_json_file(state_path, payload)
    return direct_pre_pause_status(state_path)


def _record_direct_circuit_result(
    result: dict[str, Any],
    *,
    network: str,
    route_key: Any = "",
    now: datetime | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    if not impl._direct_circuit_enabled():
        return {"enabled": False, "recorded": False}
    state_path = path or impl._direct_circuit_breaker_path()
    payload = impl._read_json_file(state_path)
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    key = impl._direct_circuit_group_key(network, route_key)
    group = groups.get(key) if isinstance(groups.get(key), dict) else {}
    thresholds = impl._direct_circuit_thresholds()
    current = now or impl._utc_now()
    ok = bool(result.get("ok"))
    status = str(result.get("status") or "")

    if ok:
        group = {
            "level": "green",
            "reason": "last_result_success",
            "offchainFailures": 0,
            "onchainFailures": 0,
            "updatedAt": current.isoformat(),
            "lastSuccessAt": current.isoformat(),
        }
    else:
        bucket = impl._direct_failure_bucket(status, bool(result.get("submitted")))
        offchain_failures = impl._positive_int_value(group.get("offchainFailures"))
        onchain_failures = impl._positive_int_value(group.get("onchainFailures"))
        if bucket == "onchain":
            onchain_failures += 1
        elif bucket == "offchain":
            offchain_failures += 1

        loss_usdc = impl._direct_failure_loss_usdc(result)
        level = "green"
        reason = status or "direct_onchain_failure"
        paused_until = ""
        if loss_usdc >= thresholds["redLossUsdc"] > 0:
            level = "red"
            reason = "red_large_loss"
            paused_until = (current + timedelta(seconds=thresholds["cooldownSeconds"])).isoformat()
        elif onchain_failures >= thresholds["onchain"]:
            level = "orange"
            reason = "orange_onchain_failure_threshold"
            paused_until = (current + timedelta(seconds=thresholds["cooldownSeconds"])).isoformat()
        elif offchain_failures >= thresholds["offchain"]:
            level = "yellow"
            reason = "yellow_offchain_failure_threshold"
            paused_until = (current + timedelta(seconds=thresholds["cooldownSeconds"])).isoformat()
        group = {
            **group,
            "level": level,
            "reason": reason,
            "lastStatus": status,
            "lastFailureBucket": bucket,
            "lastFailureAt": current.isoformat(),
            "updatedAt": current.isoformat(),
            "offchainFailures": offchain_failures,
            "onchainFailures": onchain_failures,
            "lastEstimatedLossUsdc": str(loss_usdc),
        }
        if paused_until:
            group["pausedUntil"] = paused_until

    groups[key] = group
    payload = {"schemaVersion": 1, "updatedAt": current.isoformat(), "groups": groups}
    impl._write_json_file(state_path, payload)
    return {"enabled": True, "recorded": True, "groupKey": key, "state": group, "file": str(state_path)}


def _with_direct_circuit_record(
    result: dict[str, Any],
    *,
    network: str,
    route_key: Any = "",
) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    circuit = _record_direct_circuit_result(result, network=network, route_key=route_key)
    if circuit.get("recorded"):
        result["circuitBreaker"] = circuit
        if (circuit.get("state") or {}).get("level") in {"yellow", "orange", "red"}:
            result["prePause"] = set_direct_pre_pause(
                True,
                reason=f"circuit_breaker_{(circuit.get('state') or {}).get('level')}",
                set_by="direct_circuit_breaker",
                pause_trigger_source="circuit_breaker",
            )
    return result
