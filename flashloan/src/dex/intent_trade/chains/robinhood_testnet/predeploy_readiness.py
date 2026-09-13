from __future__ import annotations

from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.robinhood.chain_test_audit import audit_robinhood_chain_test_plan
from dex.intent_trade.chains.robinhood_testnet.normal_flash_probe import resolve_robinhood_testnet_normal_flash_probe
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK


PASSING_FORK_STATUSES = {
    "passed",
    "pass",
    "success",
    "fork_staticcall_passed",
    "fork_transaction_simulation_passed",
    "staticcall_passed",
}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return str(value or "").strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = _text(value)
        if item and item not in result:
            result.append(item)
    return result


def _bool(value: object) -> bool:
    return value is True


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _evidence_path_is_testnet_scoped(value: object, expected_suffix: str = "") -> bool:
    path = _text(value).replace("\\", "/").lower()
    suffix = expected_suffix.lower()
    if not path or "/robinhood-testnet/" not in f"/{path}":
        return False
    if not suffix:
        return True
    if path.endswith(suffix):
        return True
    evidence_dir = suffix.strip("/").rsplit("/", 1)[0]
    return bool(evidence_dir and f"/robinhood-testnet/{evidence_dir}/" in f"/{path}/")


def _has_passing_fork_evidence(plan: dict[str, Any]) -> bool:
    evidence = _as_dict(plan.get("forkEvidence") or plan.get("simulationEvidence"))
    status = _text(evidence.get("status")).lower()
    return evidence.get("passed") is True or status in PASSING_FORK_STATUSES


def _chain_test_blockers(plan: dict[str, Any]) -> list[str]:
    evidence = _as_dict(plan.get("evidence"))
    decision = _as_dict(plan.get("decision"))
    preflight = _as_dict(plan.get("preflightSpec"))
    blockers: list[str] = []

    if not _bool(evidence.get("routeClosed")):
        blockers.append("route_not_closed")
    if not _bool(evidence.get("tokenContinuity")):
        blockers.append("token_continuity_failed")
    if not _bool(evidence.get("allHopsQuoted")):
        blockers.append("not_all_hops_quoted")
    if not _bool(evidence.get("netPositiveAfterCosts")):
        blockers.append("net_profit_not_positive_after_costs")
    if not _bool(evidence.get("meetsMinProfitThreshold")):
        blockers.append("min_profit_threshold_not_met")
    if not _bool(decision.get("readyForTransactionPreflight")):
        blockers.append("chain_test_plan_not_ready_for_transaction_preflight")
    if not _bool(decision.get("canRunForkPreflight")):
        blockers.append("fork_staticcall_gate_not_ready")
    if preflight.get("status") != "ready_for_fork_staticcall":
        blockers.append("preflight_status_not_ready_for_fork_staticcall")

    return blockers


def _plan_scope(plan: dict[str, Any]) -> dict[str, Any]:
    chain_test_scope = _as_dict(plan.get("chainTestScope"))
    chain_id = plan.get("chainId", chain_test_scope.get("chainId"))
    try:
        parsed_chain_id = int(chain_id)
    except (TypeError, ValueError):
        parsed_chain_id = None
    return {
        "network": _text(plan.get("network") or chain_test_scope.get("network")).lower(),
        "chainId": parsed_chain_id,
    }


def _is_testnet_plan_scope(plan: dict[str, Any]) -> bool:
    scope = _plan_scope(plan)
    return scope["network"] == ROBINHOOD_TESTNET_NETWORK and scope["chainId"] == ROBINHOOD_TESTNET_CHAIN_ID


def summarize_robinhood_predeploy_readiness(
    *,
    chain_metadata: dict[str, Any],
    contracts_metadata: dict[str, Any],
    chain_test_plan: dict[str, Any],
    chain_test_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize whether the repo is ready to move past local/read-only work.

    This gate intentionally does not authorize deployment. It separates a
    syntactically valid chain-test package from an opportunity that is actually
    ready for fork/static-call and later deployment discussion.
    """

    audit = chain_test_audit or audit_robinhood_chain_test_plan(chain_test_plan)
    stage = _as_dict(chain_metadata.get("stageFreezePredeploy"))
    policy = _as_dict(chain_metadata.get("onchainTestPolicy"))
    testnet_v4_pool = _as_dict(chain_metadata.get("testnetV4PoolEvidence"))
    lending_liquidity = _as_dict(chain_metadata.get("lendingLiquidity"))
    plan_decision = _as_dict(chain_test_plan.get("decision"))
    plan_evidence = _as_dict(chain_test_plan.get("evidence"))
    plan_preflight = _as_dict(chain_test_plan.get("preflightSpec"))
    plan_route = _as_dict(chain_test_plan.get("route"))
    plan_scope = _plan_scope(chain_test_plan)
    plan_scope_ok = _is_testnet_plan_scope(chain_test_plan)
    normal_probe = resolve_robinhood_testnet_normal_flash_probe(
        chain_metadata=chain_metadata,
        contracts_metadata=contracts_metadata,
        chain_test_plan=chain_test_plan,
    )
    audit_ok = _bool(audit.get("ok"))
    fork_ready = audit_ok and plan_scope_ok and not _chain_test_blockers(chain_test_plan)
    fork_passed = _has_passing_fork_evidence(chain_test_plan)
    stage_ready = _bool(stage.get("readyForStageFreeze") or chain_metadata.get("readyForStageFreeze"))
    stage_approved = _bool(stage.get("stageFreezeApproved") or chain_metadata.get("stageFreezeApproved"))
    chain_test_plan_evidence_scoped = _evidence_path_is_testnet_scoped(
        chain_metadata.get("lastChainTestPlanEvidence"),
        "/chain_test/latest/plan.json",
    )
    predeploy_readiness_evidence_scoped = _evidence_path_is_testnet_scoped(
        chain_metadata.get("lastPredeployReadinessEvidence"),
        "/predeploy_readiness/latest.json",
    )
    lending_liquidity_evidence_scoped = _evidence_path_is_testnet_scoped(
        chain_metadata.get("lastLendingLiquidityEvidence"),
        "/lending_liquidity/latest.json",
    )
    candidate_pool_supply_evidence_unset = not _text(chain_metadata.get("lastCandidatePoolSupplyEvidence"))
    metadata_evidence_scope_ok = (
        chain_test_plan_evidence_scoped
        and predeploy_readiness_evidence_scoped
        and lending_liquidity_evidence_scoped
        and candidate_pool_supply_evidence_unset
        and (
            lending_liquidity.get("supportsAaveOrMorphoFlashLoans") is not True
            or lending_liquidity.get("hasVerifiedBorrowableLiquidity") is True
        )
    )
    broadcast_disabled = (
        chain_metadata.get("broadcastEnabled") is False
        and contracts_metadata.get("broadcastEnabled") is False
        and plan_decision.get("canBroadcast") is False
        and plan_preflight.get("broadcastEnabled") is False
    )
    normal_probe_inputs_ready = bool(normal_probe.get("measurementReady"))
    normal_probe_address_ready = bool(_as_dict(normal_probe.get("probe")).get("address"))

    blockers: list[str] = []
    if chain_metadata.get("chainId") != ROBINHOOD_TESTNET_CHAIN_ID:
        blockers.append("testnet_chain_metadata_not_46630")
    if chain_metadata.get("chainKey") != f"chain:{ROBINHOOD_TESTNET_CHAIN_ID}":
        blockers.append("testnet_chain_key_not_chain_46630")
    if chain_metadata.get("defaultDexChainSelector") is not False:
        blockers.append("robinhood_testnet_must_not_be_default_dex_selector")
    if not chain_test_plan_evidence_scoped:
        blockers.append("chain_test_plan_evidence_not_robinhood_testnet_scoped")
    if not predeploy_readiness_evidence_scoped:
        blockers.append("predeploy_readiness_evidence_not_robinhood_testnet_scoped")
    if not lending_liquidity_evidence_scoped:
        blockers.append("lending_liquidity_evidence_not_robinhood_testnet_scoped")
    if not candidate_pool_supply_evidence_unset:
        blockers.append("candidate_pool_supply_evidence_must_be_null_for_testnet")
    if (
        lending_liquidity.get("supportsAaveOrMorphoFlashLoans") is True
        and lending_liquidity.get("hasVerifiedBorrowableLiquidity") is not True
    ):
        blockers.append("aave_or_morpho_flashloan_claim_without_verified_testnet_liquidity")
    if testnet_v4_pool.get("supportsV4FlashAccountingTests") is not True:
        blockers.append("testnet_v4_usable_pool_not_verified")
    if _int(testnet_v4_pool.get("recentNonzeroLiquiditySwapPoolCount")) <= 0:
        blockers.append("testnet_usable_pool_missing")
    if testnet_v4_pool.get("executionReady") is not True:
        blockers.append("testnet_v4_execution_not_ready")
    if not normal_probe_inputs_ready:
        blockers.append("normal_testnet_probe_inputs_not_resolved")
    if normal_probe_inputs_ready and not normal_probe_address_ready:
        blockers.append("normal_testnet_probe_address_missing")
    if not broadcast_disabled:
        blockers.append("broadcast_gate_not_closed")
    if policy.get("mode") != "normal_only":
        blockers.append("onchain_test_policy_not_normal_only")
    if policy.get("abnormalOnchainTestsEnabled") is not False:
        blockers.append("abnormal_onchain_tests_not_deferred")
    if not stage_ready:
        blockers.append("stage_predeploy_not_ready")
    if not audit_ok:
        blockers.append("chain_test_audit_failed")
    if not plan_scope_ok:
        blockers.append("chain_test_plan_scope_not_robinhood_testnet")
    blockers.extend(_chain_test_blockers(chain_test_plan))
    if not fork_passed:
        blockers.append("fork_staticcall_not_passed")
    if contracts_metadata.get("readyForLatestPausedExecutor") is not True:
        blockers.append("latest_executor_gate_not_ready")
    if contracts_metadata.get("matchesLocalArtifact") is not True:
        blockers.append("current_executor_mismatch_not_ready")
    if not stage_approved:
        blockers.append("stage_freeze_not_approved")

    ready_for_fork_staticcall = fork_ready
    ready_for_deployment_discussion = bool(stage_ready and fork_ready and fork_passed and metadata_evidence_scope_ok)
    ready_for_paused_redeploy = bool(ready_for_deployment_discussion and stage_approved)

    return {
        "schemaVersion": "robinhood-predeploy-readiness-v1",
        "chainId": chain_metadata.get("chainId"),
        "chainKey": chain_metadata.get("chainKey"),
        "marketId": chain_metadata.get("marketId"),
        "route": {
            "network": plan_scope["network"],
            "chainId": plan_scope["chainId"],
            "pathText": plan_route.get("pathText"),
            "closed": plan_route.get("closed"),
            "tokenContinuous": plan_route.get("tokenContinuous"),
            "hopCount": plan_route.get("hopCount"),
        },
        "opportunity": {
            "status": plan_evidence.get("status"),
            "triggerBlockNumber": plan_evidence.get("triggerBlockNumber"),
            "blockNumber": plan_evidence.get("blockNumber"),
            "netPositiveAfterCosts": plan_evidence.get("netPositiveAfterCosts"),
            "meetsMinProfitThreshold": plan_evidence.get("meetsMinProfitThreshold"),
            "readyForTransactionPreflight": plan_decision.get("readyForTransactionPreflight"),
            "canRunForkPreflight": plan_decision.get("canRunForkPreflight"),
            "trustedForExecution": plan_decision.get("trustedForExecution"),
            "canBroadcast": plan_decision.get("canBroadcast"),
        },
        "gates": {
            "auditOk": audit_ok,
            "broadcastDisabled": broadcast_disabled,
            "normalOnlyOnchainTesting": policy.get("mode") == "normal_only"
            and policy.get("abnormalOnchainTestsEnabled") is False,
            "chainTestPlanScopeOk": plan_scope_ok,
            "chainTestPlanEvidenceScoped": chain_test_plan_evidence_scoped,
            "predeployReadinessEvidenceScoped": predeploy_readiness_evidence_scoped,
            "lendingLiquidityEvidenceScoped": lending_liquidity_evidence_scoped,
            "candidatePoolSupplyEvidenceUnset": candidate_pool_supply_evidence_unset,
            "metadataEvidenceScopeOk": metadata_evidence_scope_ok,
            "aaveOrMorphoFlashLoansNotClaimedWithoutLiquidity": lending_liquidity.get(
                "supportsAaveOrMorphoFlashLoans"
            )
            is not True
            or lending_liquidity.get("hasVerifiedBorrowableLiquidity") is True,
            "testnetV4UsablePoolVerified": testnet_v4_pool.get("supportsV4FlashAccountingTests") is True
            and _int(testnet_v4_pool.get("recentNonzeroLiquiditySwapPoolCount")) > 0,
            "testnetV4ExecutionReady": testnet_v4_pool.get("executionReady") is True,
            "normalTestnetProbeInputsReady": normal_probe_inputs_ready,
            "normalTestnetProbeAddressReady": normal_probe_address_ready,
            "stagePredeployReady": stage_ready,
            "stageFreezeApproved": stage_approved,
            "readyForForkStaticcall": ready_for_fork_staticcall,
            "forkStaticcallPassed": fork_passed,
            "readyForDeploymentDiscussion": ready_for_deployment_discussion,
            "readyForPausedRedeploy": ready_for_paused_redeploy,
        },
        "audit": {
            "ok": audit_ok,
            "errorCount": audit.get("errorCount", 0),
            "warningCount": audit.get("warningCount", 0),
            "issues": _as_list(audit.get("issues")),
        },
        "evidence": {
            "chainTestPlan": chain_metadata.get("lastChainTestPlanEvidence"),
            "predeployReadiness": chain_metadata.get("lastPredeployReadinessEvidence"),
            "lendingLiquidity": chain_metadata.get("lastLendingLiquidityEvidence"),
            "candidatePoolSupply": chain_metadata.get("lastCandidatePoolSupplyEvidence"),
            "stageFreezePredeploy": chain_metadata.get("lastStageFreezePredeployEvidence"),
            "pausedRedeployPlan": chain_metadata.get("lastPausedRedeployPlanEvidence"),
            "normalTestnetProbe": chain_metadata.get("lastNormalTestnetProbeEvidence"),
            "readback": chain_metadata.get("lastReadbackEvidence"),
            "resolvedNormalTestnetProbe": normal_probe,
        },
        "nextRequiredActions": [
            action
            for action, needed in [
                ("capture_fresh_closed_opportunity_meeting_min_profit", not ready_for_fork_staticcall),
                ("run_fork_staticcall_or_transaction_simulation", ready_for_fork_staticcall and not fork_passed),
                ("deploy_or_register_normal_testnet_probe_helper", normal_probe_inputs_ready and not normal_probe_address_ready),
                ("freeze_stage_before_paused_redeploy", ready_for_deployment_discussion and not stage_approved),
                ("keep_testnet_deployment_deferred", not ready_for_paused_redeploy),
            ]
            if needed
        ],
        "readyForForkStaticcall": ready_for_fork_staticcall,
        "readyForDeploymentDiscussion": ready_for_deployment_discussion,
        "readyForPausedRedeploy": ready_for_paused_redeploy,
        "broadcastAttempted": False,
        "blockingReasons": _dedupe(blockers),
    }


def predeploy_readiness_flow(
    *,
    request: ChainFlowRequest | None = None,
    chain_metadata: dict[str, Any],
    contracts_metadata: dict[str, Any],
    chain_test_plan: dict[str, Any],
    chain_test_audit: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    return summarize_robinhood_predeploy_readiness(
        chain_metadata=chain_metadata,
        contracts_metadata=contracts_metadata,
        chain_test_plan=chain_test_plan,
        chain_test_audit=chain_test_audit,
    )


__all__ = ["predeploy_readiness_flow", "summarize_robinhood_predeploy_readiness"]
