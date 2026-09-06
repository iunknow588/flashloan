"""Robinhood Testnet live-testing readiness checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dex.intent_trade.chains.robinhood.chain_test_audit import audit_robinhood_chain_test_plan
from dex.intent_trade.chains.robinhood_testnet.normal_flash_probe import resolve_robinhood_testnet_normal_flash_probe
from dex.intent_trade.chains.robinhood_testnet.predeploy_readiness import (
    _as_dict,
    _as_list,
    _bool,
    _chain_test_blockers,
    _dedupe,
    _evidence_path_is_testnet_scoped,
    _int,
    _is_testnet_plan_scope,
    _plan_scope,
    _text,
)


def summarize_robinhood_testnet_live_testing_readiness(
    *,
    chain_metadata: dict[str, Any],
    contracts_metadata: dict[str, Any],
    chain_test_plan: dict[str, Any],
    chain_test_audit: dict[str, Any] | None = None,
    current_opportunity_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit = chain_test_audit or audit_robinhood_chain_test_plan(chain_test_plan)
    stage = _as_dict(chain_metadata.get("stageFreezePredeploy"))
    policy = _as_dict(chain_metadata.get("onchainTestPolicy"))
    live_testing = _as_dict(chain_metadata.get("liveTesting"))
    testnet_v4_pool = _as_dict(chain_metadata.get("testnetV4PoolEvidence"))
    lending_liquidity = _as_dict(chain_metadata.get("lendingLiquidity"))
    plan_evidence = _as_dict(chain_test_plan.get("evidence"))
    plan_scope = _plan_scope(chain_test_plan)
    plan_scope_ok = _is_testnet_plan_scope(chain_test_plan)
    normal_probe = resolve_robinhood_testnet_normal_flash_probe(
        chain_metadata=chain_metadata,
        contracts_metadata=contracts_metadata,
        chain_test_plan=chain_test_plan,
    )
    chain_broadcast_enabled = _bool(chain_metadata.get("broadcastEnabled"))
    contract_broadcast_enabled = _bool(contracts_metadata.get("broadcastEnabled"))
    normal_probe_ready = _bool(normal_probe.get("readyForNormalBroadcast"))
    normal_probe_measurement_ready = _bool(normal_probe.get("measurementReady"))
    audit_ok = _bool(audit.get("ok"))
    stage_approved = _bool(stage.get("stageFreezeApproved") or chain_metadata.get("stageFreezeApproved"))
    execution_first = _bool(live_testing.get("executionFirst"))
    snapshot_chain = _as_dict(
        _as_dict(current_opportunity_snapshot).get("snapshots", {}).get(str(chain_metadata.get("chainId")))
        if isinstance(_as_dict(current_opportunity_snapshot).get("snapshots"), dict)
        else {}
    )
    snapshot_updated_at = _text(snapshot_chain.get("updated_at") or _as_dict(current_opportunity_snapshot).get("updated_at"))
    plan_generated_at = _text(chain_test_plan.get("generatedAt"))

    def _parse_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    snapshot_time = _parse_timestamp(snapshot_updated_at)
    plan_time = _parse_timestamp(plan_generated_at)
    snapshot_is_newer_than_plan = bool(snapshot_time and plan_time and snapshot_time > plan_time)
    snapshot_summary = _as_dict(snapshot_chain.get("summary"))
    current_live_profitable_route_count = _int(snapshot_summary.get("live_profitable_route_count"))
    if current_live_profitable_route_count == 0:
        current_live_profitable_route_count = _int(snapshot_summary.get("liveProfitableRouteCount"))
    current_snapshot_has_no_live_profitable_route = bool(
        snapshot_chain and current_live_profitable_route_count == 0
    )
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
    candidate_pool_supply_evidence_unset = not str(chain_metadata.get("lastCandidatePoolSupplyEvidence") or "").strip()
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
    broadcast_enabled = _bool(live_testing.get("normalProbeBroadcastEnabled")) or (
        chain_broadcast_enabled and contract_broadcast_enabled
    )
    chain_test_ready = bool(
        audit_ok
        and plan_scope_ok
        and metadata_evidence_scope_ok
        and testnet_v4_pool.get("supportsV4FlashAccountingTests") is True
        and _int(testnet_v4_pool.get("recentNonzeroLiquiditySwapPoolCount")) > 0
        and testnet_v4_pool.get("executionReady") is True
        and normal_probe_ready
        and normal_probe_measurement_ready
        and (execution_first or not _chain_test_blockers(chain_test_plan))
        and not snapshot_is_newer_than_plan
    )

    blockers: list[str] = []
    if chain_metadata.get("chainId") != 46630:
        blockers.append("testnet_chain_metadata_not_46630")
    if chain_metadata.get("chainKey") != "chain:46630":
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
    if not chain_test_ready:
        blockers.append("chain_test_readiness_not_ready_for_live_testing")
    if snapshot_is_newer_than_plan:
        blockers.append("chain_test_plan_stale_against_current_opportunity_snapshot")
    if snapshot_is_newer_than_plan and current_snapshot_has_no_live_profitable_route:
        blockers.append("current_opportunity_snapshot_has_no_live_profitable_route")
    if not broadcast_enabled:
        blockers.append("broadcast_gate_closed")
    if not normal_probe_ready:
        blockers.append("normal_testnet_probe_not_ready")
    if not normal_probe_measurement_ready:
        blockers.append("normal_testnet_probe_measurement_not_ready")
    if policy.get("mode") != "normal_only":
        blockers.append("onchain_test_policy_not_normal_only")
    if policy.get("abnormalOnchainTestsEnabled") is not False:
        blockers.append("abnormal_onchain_tests_not_deferred")
    if stage_approved is not True:
        blockers.append("stage_freeze_not_approved")
    if contracts_metadata.get("chainId") not in (None, 46630):
        blockers.append("contracts_chain_id_not_robinhood_testnet")
    if not audit_ok and not _as_list(audit.get("issues")):
        blockers.append("chain_test_audit_failed")
    if not plan_scope_ok:
        blockers.append("chain_test_plan_scope_not_robinhood_testnet")
    if not execution_first:
        blockers.extend(_chain_test_blockers(chain_test_plan))

    ready_for_live_test = not blockers
    return {
        "schemaVersion": "robinhood-testnet-live-testing-readiness-v1",
        "chainId": chain_metadata.get("chainId"),
        "chainKey": chain_metadata.get("chainKey"),
        "marketId": chain_metadata.get("marketId"),
        "readyForLiveTest": ready_for_live_test,
        "broadcastEnabled": broadcast_enabled,
        "chainTestReady": chain_test_ready,
        "normalTestnetProbe": normal_probe,
        "gates": {
            "chainTestReady": chain_test_ready,
            "chainBroadcastEnabled": chain_broadcast_enabled,
            "contractBroadcastEnabled": contract_broadcast_enabled,
            "broadcastEnabled": broadcast_enabled,
            "normalTestnetProbeReady": normal_probe_ready,
            "normalTestnetProbeMeasurementReady": normal_probe_measurement_ready,
            "stageFreezeApproved": stage_approved,
            "chainTestPlanEvidenceScoped": chain_test_plan_evidence_scoped,
            "predeployReadinessEvidenceScoped": predeploy_readiness_evidence_scoped,
            "lendingLiquidityEvidenceScoped": lending_liquidity_evidence_scoped,
            "candidatePoolSupplyEvidenceUnset": candidate_pool_supply_evidence_unset,
            "opportunitySnapshotNewerThanPlan": snapshot_is_newer_than_plan,
            "currentSnapshotHasNoLiveProfitableRoute": current_snapshot_has_no_live_profitable_route,
        },
        "evidence": {
            "chainTestPlan": chain_metadata.get("lastChainTestPlanEvidence"),
            "predeployReadiness": chain_metadata.get("lastPredeployReadinessEvidence"),
            "lendingLiquidity": chain_metadata.get("lastLendingLiquidityEvidence"),
            "candidatePoolSupply": chain_metadata.get("lastCandidatePoolSupplyEvidence"),
            "normalTestnetProbe": chain_metadata.get("lastNormalTestnetProbeEvidence"),
            "pausedRedeployPlan": chain_metadata.get("lastPausedRedeployPlanEvidence"),
            "currentOpportunitySnapshotUpdatedAt": snapshot_updated_at or None,
            "chainTestPlanGeneratedAt": plan_generated_at or None,
            "currentLiveProfitableRouteCount": current_live_profitable_route_count if snapshot_chain else None,
        },
        "blockingReasons": _dedupe(blockers),
        "nextRequiredActions": [
            action
            for action, needed in [
                ("open_broadcast_gate", "broadcast_gate_closed" in blockers and chain_test_ready and normal_probe_ready),
                ("prepare_normal_testnet_probe", "normal_testnet_probe_not_ready" in blockers),
                (
                    "verify_normal_testnet_probe_measurement",
                    "normal_testnet_probe_measurement_not_ready" in blockers,
                ),
                ("finish_chain_test_readiness", "chain_test_readiness_not_ready_for_live_testing" in blockers),
                ("run_small_notional_testnet_transaction", ready_for_live_test),
            ]
            if needed
        ],
    }


__all__ = ["summarize_robinhood_testnet_live_testing_readiness"]
