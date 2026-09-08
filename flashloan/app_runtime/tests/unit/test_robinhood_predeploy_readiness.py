import json
from copy import deepcopy

from dex.intent_trade.chains.robinhood.chain_test_pack import build_robinhood_chain_test_plan
from dex.intent_trade.chains.robinhood_testnet.predeploy_readiness import summarize_robinhood_predeploy_readiness
from tests.unit.test_robinhood_chain_test_pack import _sample_attempt
from dex.tools.check_robinhood_predeploy_readiness import main as predeploy_readiness_main
import dex.tools.check_robinhood_predeploy_readiness as predeploy_checker


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _chain_metadata():
    return {
        "chainId": 46630,
        "chainKey": "chain:46630",
        "marketId": "robinhood-testnet-dex-paused",
        "defaultDexChainSelector": False,
        "broadcastEnabled": False,
        "readyForStageFreeze": True,
        "stageFreezeApproved": False,
        "lastChainTestPlanEvidence": "flashloan/app_runtime/runtime/evidence/robinhood-testnet/chain_test/latest/plan.json",
        "lastPredeployReadinessEvidence": "flashloan/app_runtime/runtime/evidence/robinhood-testnet/predeploy_readiness/latest.json",
        "lastLendingLiquidityEvidence": "flashloan/app_runtime/runtime/evidence/robinhood-testnet/lending_liquidity/latest.json",
        "lastCandidatePoolSupplyEvidence": None,
        "lastStageFreezePredeployEvidence": "stage/report.json",
        "lastPausedRedeployPlanEvidence": "plan/report.json",
        "lastNormalTestnetProbeEvidence": "probe/report.json",
        "lastReadbackEvidence": "readback/report.json",
        "onchainTestPolicy": {
            "mode": "normal_only",
            "abnormalOnchainTestsEnabled": False,
        },
        "testnetV4PoolEvidence": {
            "supportsV4FlashAccountingTests": True,
            "recentNonzeroLiquiditySwapPoolCount": 1,
            "executionReady": True,
        },
        "lendingLiquidity": {
            "hasVerifiedBorrowableLiquidity": False,
            "supportsAaveOrMorphoFlashLoans": False,
        },
        "stageFreezePredeploy": {
            "readyForStageFreeze": True,
            "stageFreezeApproved": False,
        },
    }


def _contracts_metadata():
    return {
        "broadcastEnabled": False,
        "readyForLatestPausedExecutor": False,
        "matchesLocalArtifact": False,
    }


def _as_testnet_plan(plan):
    plan = deepcopy(plan)
    plan["network"] = "robinhood-testnet"
    plan["chainId"] = 46630
    plan["marketId"] = "robinhood-testnet-dex-paused"
    plan["chainTestScope"] = {
        **plan.get("chainTestScope", {}),
        "network": "robinhood-testnet",
        "chainId": 46630,
    }
    return plan


def test_predeploy_readiness_blocks_valid_audit_when_opportunity_is_below_min_profit():
    attempt = _sample_attempt()
    evidence = attempt["quote"]["live_onchain_evidence"]
    evidence["readyForTransactionPreflight"] = False
    evidence["validation"]["meetsMinProfitThreshold"] = False
    evidence["costModel"]["netProfitUsd"] = 0.612327
    evidence["costModel"]["minProfitUsd"] = 2.0
    plan = _as_testnet_plan(build_robinhood_chain_test_plan(attempt))

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=_chain_metadata(),
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["gates"]["auditOk"] is True
    assert report["readyForForkStaticcall"] is False
    assert report["readyForDeploymentDiscussion"] is False
    assert report["readyForPausedRedeploy"] is False
    assert "min_profit_threshold_not_met" in report["blockingReasons"]
    assert "chain_test_plan_not_ready_for_transaction_preflight" in report["blockingReasons"]
    assert "fork_staticcall_not_passed" in report["blockingReasons"]


def test_predeploy_readiness_blocks_when_testnet_pool_evidence_is_missing():
    chain_metadata = _chain_metadata()
    chain_metadata["testnetV4PoolEvidence"] = {
        "supportsV4FlashAccountingTests": False,
        "recentNonzeroLiquiditySwapPoolCount": 0,
        "executionReady": False,
    }
    plan = _as_testnet_plan(build_robinhood_chain_test_plan(_sample_attempt()))
    plan["forkEvidence"] = {
        "status": "fork_staticcall_passed",
        "passed": True,
    }

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=chain_metadata,
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["gates"]["testnetV4UsablePoolVerified"] is False
    assert report["gates"]["testnetV4ExecutionReady"] is False
    assert "testnet_v4_usable_pool_not_verified" in report["blockingReasons"]
    assert "testnet_usable_pool_missing" in report["blockingReasons"]
    assert "testnet_v4_execution_not_ready" in report["blockingReasons"]


def test_predeploy_readiness_blocks_mainnet_plan_for_testnet_deploy():
    plan = build_robinhood_chain_test_plan(_sample_attempt())
    plan = deepcopy(plan)
    plan["forkEvidence"] = {
        "status": "fork_staticcall_passed",
        "passed": True,
    }

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=_chain_metadata(),
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["route"]["network"] == "robinhood"
    assert report["route"]["chainId"] == 4663
    assert report["gates"]["chainTestPlanScopeOk"] is False
    assert report["readyForDeploymentDiscussion"] is False
    assert "chain_test_plan_scope_not_robinhood_testnet" in report["blockingReasons"]


def test_predeploy_readiness_blocks_mainnet_evidence_paths_for_testnet_metadata():
    chain_metadata = _chain_metadata()
    chain_metadata["lastChainTestPlanEvidence"] = "flashloan/app_runtime/runtime/evidence/robinhood/chain_test/latest/plan.json"
    chain_metadata["lastPredeployReadinessEvidence"] = (
        "flashloan/app_runtime/runtime/evidence/robinhood/predeploy_readiness/latest.json"
    )
    chain_metadata["lastLendingLiquidityEvidence"] = (
        "flashloan/app_runtime/runtime/evidence/robinhood/lending_liquidity/latest.json"
    )
    chain_metadata["lastCandidatePoolSupplyEvidence"] = (
        "flashloan/app_runtime/runtime/evidence/robinhood/candidate_pool_supply/latest/report.json"
    )
    plan = _as_testnet_plan(build_robinhood_chain_test_plan(_sample_attempt()))
    plan["forkEvidence"] = {
        "status": "fork_staticcall_passed",
        "passed": True,
    }

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=chain_metadata,
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["gates"]["chainTestPlanEvidenceScoped"] is False
    assert report["gates"]["predeployReadinessEvidenceScoped"] is False
    assert report["gates"]["lendingLiquidityEvidenceScoped"] is False
    assert report["gates"]["candidatePoolSupplyEvidenceUnset"] is False
    assert report["readyForDeploymentDiscussion"] is False
    assert "chain_test_plan_evidence_not_robinhood_testnet_scoped" in report["blockingReasons"]
    assert "predeploy_readiness_evidence_not_robinhood_testnet_scoped" in report["blockingReasons"]
    assert "lending_liquidity_evidence_not_robinhood_testnet_scoped" in report["blockingReasons"]
    assert "candidate_pool_supply_evidence_must_be_null_for_testnet" in report["blockingReasons"]


def test_predeploy_readiness_accepts_testnet_scoped_timestamp_report_paths():
    chain_metadata = _chain_metadata()
    chain_metadata["lastPredeployReadinessEvidence"] = (
        "flashloan/app_runtime/runtime/evidence/robinhood-testnet/predeploy_readiness/20260827T014839Z/report.json"
    )
    plan = _as_testnet_plan(build_robinhood_chain_test_plan(_sample_attempt()))

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=chain_metadata,
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["gates"]["predeployReadinessEvidenceScoped"] is True
    assert "predeploy_readiness_evidence_not_robinhood_testnet_scoped" not in report["blockingReasons"]


def test_predeploy_readiness_blocks_lending_flashloan_claim_without_verified_liquidity():
    chain_metadata = _chain_metadata()
    chain_metadata["lendingLiquidity"] = {
        "hasVerifiedBorrowableLiquidity": False,
        "supportsAaveOrMorphoFlashLoans": True,
    }
    plan = _as_testnet_plan(build_robinhood_chain_test_plan(_sample_attempt()))
    plan["forkEvidence"] = {
        "status": "fork_staticcall_passed",
        "passed": True,
    }

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=chain_metadata,
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["gates"]["aaveOrMorphoFlashLoansNotClaimedWithoutLiquidity"] is False
    assert report["gates"]["metadataEvidenceScopeOk"] is False
    assert report["readyForDeploymentDiscussion"] is False
    assert "aave_or_morpho_flashloan_claim_without_verified_testnet_liquidity" in report["blockingReasons"]


def test_predeploy_readiness_chain_test_plan_build_prefers_trigger_block_attempt(monkeypatch, tmp_path):
    chain_json = tmp_path / "contract" / "robinhood" / "testnet" / "chain.json"
    plan_path = tmp_path / "runtime" / "evidence" / "robinhood-testnet" / "chain_test" / "latest" / "plan.json"
    _write_json(chain_json, _chain_metadata())
    picked = {}

    def fake_loader(*args, **kwargs):
        lower = deepcopy(_sample_attempt())
        lower["quote"]["live_onchain_evidence"]["triggerBlockNumber"] = 0
        lower["trigger_block_number"] = 0
        higher = deepcopy(_sample_attempt())
        higher["quote"]["live_onchain_evidence"]["triggerBlockNumber"] = 107210981
        higher["trigger_block_number"] = 107210981
        return [lower, higher]

    def fake_package_flow(*, attempt, **kwargs):
        picked["trigger_block_number"] = attempt.get("trigger_block_number")
        picked["triggerBlockNumber"] = attempt.get("quote", {}).get("live_onchain_evidence", {}).get("triggerBlockNumber")
        return {"plan": {"sentinel": True}}

    monkeypatch.setattr(predeploy_checker, "load_recent_cow_execution_attempts_jsonl", fake_loader)
    monkeypatch.setattr(predeploy_checker, "chain_test_package_flow", fake_package_flow)

    plan = predeploy_checker._build_chain_test_plan_from_local_attempts(plan_path, chain_json)

    assert plan == {"sentinel": True}
    assert picked["trigger_block_number"] == 107210981
    assert picked["triggerBlockNumber"] == 107210981


def test_predeploy_readiness_cli_writes_testnet_scoped_closed_report_when_plan_is_missing(tmp_path, monkeypatch):
    chain_path = tmp_path / "contract" / "robinhood" / "testnet" / "chain.json"
    contracts_path = tmp_path / "contract" / "robinhood" / "testnet" / "contracts.json"
    missing_plan_path = tmp_path / "runtime" / "evidence" / "robinhood-testnet" / "chain_test" / "latest" / "plan.json"
    output_dir = tmp_path / "runtime" / "evidence" / "robinhood-testnet" / "predeploy_readiness"
    _write_json(chain_path, _chain_metadata())
    _write_json(contracts_path, _contracts_metadata())
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_robinhood_predeploy_readiness.py",
            "--chain-json",
            str(chain_path),
            "--contracts-json",
            str(contracts_path),
            "--plan-json",
            str(missing_plan_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    exit_code = predeploy_readiness_main()
    latest = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert latest["chainId"] == 46630
    assert latest["route"]["network"] == "robinhood-testnet"
    assert latest.get("chainTestPlanMissing") is not True
    assert missing_plan_path.exists()
    assert "/robinhood-testnet/" in latest["evidence"]["chainTestPlan"].replace("\\", "/")
    assert not any("chain_test_plan_missing" in reason for reason in latest["blockingReasons"])
    assert "/robinhood-testnet/" in latest["evidence"]["predeployReadiness"].replace("\\", "/")
    assert "/robinhood/" not in latest["evidence"]["predeployReadiness"].replace("\\", "/")
    assert latest["readyForPausedRedeploy"] is False


def test_predeploy_readiness_allows_deployment_discussion_only_after_fork_passes():
    plan = _as_testnet_plan(build_robinhood_chain_test_plan(_sample_attempt()))
    plan["forkEvidence"] = {
        "status": "fork_staticcall_passed",
        "passed": True,
    }

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=_chain_metadata(),
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["readyForForkStaticcall"] is True
    assert report["readyForDeploymentDiscussion"] is True
    assert report["readyForPausedRedeploy"] is False
    assert "stage_freeze_not_approved" in report["blockingReasons"]
    assert "fork_staticcall_not_passed" not in report["blockingReasons"]


def test_predeploy_readiness_allows_paused_redeploy_only_after_stage_freeze_approval():
    chain_metadata = _chain_metadata()
    chain_metadata["stageFreezeApproved"] = True
    chain_metadata["stageFreezePredeploy"]["stageFreezeApproved"] = True
    plan = _as_testnet_plan(build_robinhood_chain_test_plan(_sample_attempt()))
    plan["forkEvidence"] = {
        "status": "fork_staticcall_passed",
        "passed": True,
    }

    report = summarize_robinhood_predeploy_readiness(
        chain_metadata=chain_metadata,
        contracts_metadata=_contracts_metadata(),
        chain_test_plan=plan,
    )

    assert report["readyForForkStaticcall"] is True
    assert report["readyForDeploymentDiscussion"] is True
    assert report["readyForPausedRedeploy"] is True
    assert "stage_freeze_not_approved" not in report["blockingReasons"]
