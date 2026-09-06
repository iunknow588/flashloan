"""Read-only deployment readiness for Robinhood Chain support.

The project has several independently verifiable milestones. A healthy RPC
proves chain connectivity, but it does not prove that a DEX route or an
executor can be used safely. This module keeps those conclusions explicit and
side-effect free so pages, reports, and tests share one status vocabulary.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from core.contracts import ExecutionGate
from core.market_config import DEFAULT_ROBINHOOD_CHAIN_ID, DEFAULT_ROBINHOOD_DEX_MARKET_ID
from core.market_identity import canonical_market_id_from_legacy


def _stage(
    key: str,
    label: str,
    status: str,
    *,
    evidence: list[str] | None = None,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "ready": status == "ready",
        "evidence": list(evidence or []),
        "blockedReasons": list(blocked_reasons or []),
    }


def _has_report_success(report: dict[str, Any] | None, *keys: str) -> bool:
    if not isinstance(report, dict):
        return False
    for key in keys:
        value = report.get(key)
        if isinstance(value, bool) and value:
            return True
        if str(value or "").strip().lower() in {"ok", "passed", "success", "ready", "verified"}:
            return True
    return False


def _report_matches_target(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict):
        return False
    network = str(report.get("network") or "").strip().lower()
    chain_id = report.get("chainId", report.get("chain_id"))
    if network and network != "robinhood":
        return False
    if chain_id not in (None, ""):
        try:
            if int(chain_id) != DEFAULT_ROBINHOOD_CHAIN_ID:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result


def _evidence_timestamp(report: dict[str, Any] | None) -> datetime | None:
    if not isinstance(report, dict):
        return None
    for key in ("generatedAt", "generated_at", "observedAt", "observed_at", "checkedAt", "checked_at"):
        raw = report.get(key)
        if not raw:
            continue
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return None


def _evidence_freshness(
    report: dict[str, Any] | None,
    *,
    now: datetime,
    max_age_seconds: float,
) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "status": "missing",
            "observedAt": None,
            "ageSeconds": None,
            "maxAgeSeconds": max_age_seconds,
        }
    timestamp = _evidence_timestamp(report)
    if timestamp is None:
        has_timestamp = any(
            report.get(key)
            for key in ("generatedAt", "generated_at", "observedAt", "observed_at", "checkedAt", "checked_at")
        )
        return {
            "status": "invalid" if has_timestamp else "unknown",
            "observedAt": None,
            "ageSeconds": None,
            "maxAgeSeconds": max_age_seconds,
        }
    age_seconds = max(0.0, (now - timestamp).total_seconds())
    return {
        "status": "fresh" if age_seconds <= max_age_seconds else "expired",
        "observedAt": timestamp.isoformat(),
        "ageSeconds": round(age_seconds, 3),
        "maxAgeSeconds": max_age_seconds,
    }


def _configured_evidence_max_age_seconds(value: float | None) -> float:
    if value is not None:
        return max(1.0, float(value))
    try:
        configured = float(os.getenv("ROBINHOOD_EVIDENCE_MAX_AGE_SECONDS", "86400"))
    except (TypeError, ValueError):
        configured = 86400.0
    return max(1.0, configured)


def robinhood_deployment_status(
    *,
    market: dict[str, Any] | None = None,
    rpc_status: dict[str, Any] | None = None,
    discovery_report: dict[str, Any] | None = None,
    quote_report: dict[str, Any] | None = None,
    executor_address: str = "",
    flashloan_provider: str = "",
    fork_simulation_report: dict[str, Any] | None = None,
    now: datetime | None = None,
    evidence_max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Return the current Robinhood deployment level without enabling execution.

    The default report describes the repository state when no external
    evidence is supplied. Passing live evidence only upgrades the relevant
    stage; it never turns a shadow market into an executable market by itself.
    """

    profile = market if isinstance(market, dict) else {}
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    max_age_seconds = _configured_evidence_max_age_seconds(evidence_max_age_seconds)
    network = str(profile.get("network") or "robinhood").strip().lower()
    chain_id = int(profile.get("chain_id") or DEFAULT_ROBINHOOD_CHAIN_ID)
    market_id = canonical_market_id_from_legacy(profile.get("market_id")) or str(
        profile.get("market_id") or DEFAULT_ROBINHOOD_DEX_MARKET_ID
    )
    observation_only = bool(profile.get("observation_only", True))
    rpc_ok = bool(isinstance(rpc_status, dict) and rpc_status.get("ok"))

    chain_evidence = [
        "chain_config",
        "direct_network_alias",
        "readonly_rpc_probe" if rpc_ok else "readonly_rpc_probe_not_confirmed",
    ]
    chain_blockers: list[str] = []
    if network != "robinhood" or chain_id != DEFAULT_ROBINHOOD_CHAIN_ID:
        chain_blockers.append("robinhood_market_scope_invalid")
    if not profile.get("wrapped_native_address"):
        chain_blockers.append("robinhood_wrapped_native_unconfigured")
    if not profile.get("multicall3_address"):
        chain_blockers.append("robinhood_multicall_unconfigured")
    if not rpc_ok:
        chain_blockers.append("robinhood_rpc_probe_not_confirmed")

    stages = [
        _stage(
            "chain_integration",
            "链配置与只读连通",
            "ready" if not chain_blockers else "blocked",
            evidence=chain_evidence,
            blocked_reasons=chain_blockers,
        ),
        _stage(
            "observation_shadow",
            "DEX 观察与候选展示",
            "ready" if observation_only and market_id == DEFAULT_ROBINHOOD_DEX_MARKET_ID else "blocked",
            evidence=["shadow_quote_only_intent", "observation_page", "database_or_file_fallback"],
            blocked_reasons=(
                []
                if observation_only and market_id == DEFAULT_ROBINHOOD_DEX_MARKET_ID
                else ["robinhood_observation_market_not_in_shadow_mode"]
            ),
        ),
    ]

    discovery_freshness = _evidence_freshness(
        discovery_report,
        now=current_time,
        max_age_seconds=max_age_seconds,
    )
    discovery_freshness_blocked = discovery_freshness["status"] in {"expired", "invalid"}
    discovery_ready = (
        _report_matches_target(discovery_report)
        and _has_report_success(discovery_report, "factoryCodeVerified", "verified")
        and not discovery_freshness_blocked
    )
    discovery_reasons = [] if discovery_ready else [
        reason
        for reason in [
            "robinhood_dex_factory_source_unverified",
            "robinhood_pool_discovery_evidence_missing",
            (
                "robinhood_pool_discovery_evidence_expired"
                if discovery_freshness["status"] == "expired"
                else "robinhood_pool_discovery_evidence_freshness_invalid"
                if discovery_freshness["status"] == "invalid"
                else None
            ),
        ]
        if reason
    ]
    stages.append(
        _stage(
            "dex_discovery",
            "DEX 地址与池发现",
            "ready" if discovery_ready else "not_started",
            evidence=["factory_source", "pool_created_events", "pool_code_checks"] if discovery_ready else [],
            blocked_reasons=discovery_reasons,
        )
    )

    quote_freshness = _evidence_freshness(
        quote_report,
        now=current_time,
        max_age_seconds=max_age_seconds,
    )
    quote_freshness_blocked = quote_freshness["status"] in {"expired", "invalid"}
    quote_ready = _report_matches_target(quote_report) and _has_report_success(
        quote_report,
        "ok",
        "passed",
        "status",
    ) and not quote_freshness_blocked
    quote_reasons = [
        reason
        for reason in [
            None if quote_ready else "robinhood_live_quote_unverified",
            (
                "robinhood_live_quote_evidence_expired"
                if quote_freshness["status"] == "expired"
                else "robinhood_live_quote_evidence_freshness_invalid"
                if quote_freshness["status"] == "invalid"
                else None
            ),
        ]
        if reason
    ]
    stages.append(
        _stage(
            "live_quote",
            "实时报价与成本模型",
            "ready" if quote_ready else "not_started",
            evidence=["router_or_quoter_staticcall", "gas_and_l1_fee", "net_profit_model"] if quote_ready else [],
            blocked_reasons=quote_reasons,
        )
    )

    fork_freshness = _evidence_freshness(
        fork_simulation_report,
        now=current_time,
        max_age_seconds=max_age_seconds,
    )
    fork_freshness_blocked = fork_freshness["status"] in {"expired", "invalid"}
    execution_reasons: list[str] = []
    if not str(executor_address or "").strip():
        execution_reasons.append("robinhood_executor_unverified")
    if not str(flashloan_provider or "").strip():
        execution_reasons.append("robinhood_flashloan_provider_unverified")
    if not quote_ready:
        execution_reasons.append("robinhood_live_quote_unverified")
    if not _has_report_success(fork_simulation_report, "ok", "passed", "status"):
        execution_reasons.append("robinhood_fork_simulation_unverified")
    if fork_freshness["status"] == "expired":
        execution_reasons.append("robinhood_fork_simulation_evidence_expired")
    elif fork_freshness["status"] == "invalid":
        execution_reasons.append("robinhood_fork_simulation_evidence_freshness_invalid")
    stages.append(
        _stage(
            "execution",
            "执行器与广播准入",
            "ready" if not execution_reasons else "blocked",
            evidence=["executor", "flashloan_provider", "live_quote", "fork_simulation"] if not execution_reasons else [],
            blocked_reasons=execution_reasons,
        )
    )

    stages.append(
        _stage(
            "liquidation",
            "清算协议适配",
            "not_started",
            blocked_reasons=[
                "robinhood_lending_protocol_unverified",
                "robinhood_liquidation_adapter_not_started",
            ],
        )
    )

    observation_ready = next(stage for stage in stages if stage["key"] == "observation_shadow")["ready"]
    execution_ready = next(stage for stage in stages if stage["key"] == "execution")["ready"]
    liquidation_ready = False
    production_ready = bool(execution_ready and liquidation_ready)
    blocking_reasons = _dedupe(
        [
            reason
            for stage in stages
            if stage["key"] in {"dex_discovery", "live_quote", "execution", "liquidation"}
            for reason in stage["blockedReasons"]
        ]
    )
    return {
        "marketId": market_id,
        "network": network,
        "chainId": chain_id,
        "deploymentComplete": False,
        "deploymentLevel": "framework_and_observation_shadow",
        "scope": "observation_shadow_only",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "observationReady": observation_ready,
        "dexExecutionReady": execution_ready,
        "liquidationReady": liquidation_ready,
        "productionReady": production_ready,
        "executionReady": execution_ready,
        "executionGate": ExecutionGate(
            ready=production_ready,
            execution_enabled=False,
            broadcast_enabled=False,
            blocked_reasons=tuple(blocking_reasons),
            evidence_refs=tuple(
                evidence
                for stage in stages
                for evidence in stage.get("evidence", [])
            ),
        ).as_dict(),
        "stages": stages,
        "evidenceFreshness": {
            "maxAgeSeconds": max_age_seconds,
            "evaluatedAt": current_time.isoformat(),
            "discovery": discovery_freshness,
            "quote": quote_freshness,
            "forkSimulation": fork_freshness,
        },
        "blockingReasons": blocking_reasons,
        "nextRequiredEvidence": [
            "verified_router_factory_quoter_addresses",
            "pool_liquidity_and_fee_snapshots",
            "live_quote_and_net_profit_evidence",
            "chain_specific_executor_and_flashloan_evidence",
            "fork_simulation_evidence",
        ],
    }
