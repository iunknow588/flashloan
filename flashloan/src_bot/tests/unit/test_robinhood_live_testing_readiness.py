import json
from copy import deepcopy

from intent_trade.chains.robinhood.chain_test_pack import build_robinhood_chain_test_plan
import intent_trade.chains.robinhood_testnet.live_testing_readiness as live_readiness
from intent_trade.chains.robinhood_testnet.live_testing_readiness import (
    summarize_robinhood_testnet_live_testing_readiness,
)
from tests.unit.test_robinhood_chain_test_pack import _sample_attempt
from tools.check_robinhood_live_testing_readiness import main as live_testing_readiness_main


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _base_chain_metadata():
    return {
        "chainId": 46630,
        "chainKey": "chain:46630",
        "marketId": "robinhood-testnet-dex-paused",
        "broadcastEnabled": True,
        "defaultDexChainSelector": False,
        "lastChainTestPlanEvidence": "flashloan/src_bot/runtime/evidence/robinhood-testnet/chain_test/latest/plan.json",
        "lastPredeployReadinessEvidence": "flashloan/src_bot/runtime/evidence/robinhood-testnet/predeploy_readiness/latest.json",
        "lastLendingLiquidityEvidence": "flashloan/src_bot/runtime/evidence/robinhood-testnet/lending_liquidity/latest.json",
        "lastCandidatePoolSupplyEvidence": None,
        "lastPausedRedeployPlanEvidence": "plan/report.json",
        "lastNormalTestnetProbeEvidence": "probe/report.json",
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
            "stageFreezeApproved": True,
        },
        "stageFreezeApproved": True,
        "readyForStageFreeze": True,
    }


def _base_contracts_metadata():
    return {
        "broadcastEnabled": True,
        "readyForLatestPausedExecutor": True,
        "matchesLocalArtifact": True,
    }


def _live_ready_plan():
    plan = deepcopy(build_robinhood_chain_test_plan(_sample_attempt()))
    plan["network"] = "robinhood-testnet"
    plan["chainId"] = 46630
    plan["marketId"] = "robinhood-testnet-dex-paused"
    plan["chainTestScope"] = {
        **plan.get("chainTestScope", {}),
        "network": "robinhood-testnet",
        "chainId": 46630,
    }
    plan["forkEvidence"] = {
        "status": "fork_staticcall_passed",
        "passed": True,
    }
    return plan


def test_live_testing_readiness_blocks_when_broadcast_is_closed():
    report = summarize_robinhood_testnet_live_testing_readiness(
        chain_metadata={**_base_chain_metadata(), "broadcastEnabled": False},
        contracts_metadata={**_base_contracts_metadata(), "broadcastEnabled": False},
        chain_test_plan=_live_ready_plan(),
    )

    assert report["readyForLiveTest"] is False
    assert "broadcast_gate_closed" in report["blockingReasons"]


def test_live_testing_readiness_requires_probe_broadcast_gate():
    report = summarize_robinhood_testnet_live_testing_readiness(
        chain_metadata={**_base_chain_metadata(), "broadcastEnabled": False},
        contracts_metadata={**_base_contracts_metadata(), "broadcastEnabled": False},
        chain_test_plan=_live_ready_plan(),
    )

    assert report["readyForLiveTest"] is False
    assert "broadcast_gate_closed" in report["blockingReasons"]


def test_live_testing_readiness_requires_normal_probe_readiness():
    def fake_probe(*args, **kwargs):
        return {
            "readyForNormalBroadcast": False,
            "measurementReady": False,
            "probe": {"address": ""},
        }

    original = live_readiness.resolve_robinhood_testnet_normal_flash_probe
    live_readiness.resolve_robinhood_testnet_normal_flash_probe = fake_probe
    try:
        report = summarize_robinhood_testnet_live_testing_readiness(
            chain_metadata=_base_chain_metadata(),
            contracts_metadata=_base_contracts_metadata(),
            chain_test_plan=_live_ready_plan(),
        )
    finally:
        live_readiness.resolve_robinhood_testnet_normal_flash_probe = original

    assert report["readyForLiveTest"] is False
    assert "normal_testnet_probe_not_ready" in report["blockingReasons"]
    assert "normal_testnet_probe_measurement_not_ready" in report["blockingReasons"]


def test_live_testing_readiness_does_not_suggest_broadcast_before_chain_test_ready():
    plan = _live_ready_plan()
    plan["evidence"]["allHopsQuoted"] = False
    plan["decision"]["readyForTransactionPreflight"] = False
    plan["decision"]["canRunForkPreflight"] = False
    plan["preflightSpec"]["status"] = "not_ready"

    report = summarize_robinhood_testnet_live_testing_readiness(
        chain_metadata={**_base_chain_metadata(), "broadcastEnabled": False},
        contracts_metadata={**_base_contracts_metadata(), "broadcastEnabled": False},
        chain_test_plan=plan,
    )

    assert report["chainTestReady"] is False
    assert "broadcast_gate_closed" in report["blockingReasons"]
    assert "finish_chain_test_readiness" in report["nextRequiredActions"]
    assert "open_broadcast_gate" not in report["nextRequiredActions"]


def test_live_testing_readiness_allows_live_test_only_when_every_gate_is_open():
    report = summarize_robinhood_testnet_live_testing_readiness(
        chain_metadata=_base_chain_metadata(),
        contracts_metadata=_base_contracts_metadata(),
        chain_test_plan=_live_ready_plan(),
    )

    assert report["chainTestReady"] is True
    assert report["gates"]["normalTestnetProbeReady"] is True
    assert report["gates"]["normalTestnetProbeMeasurementReady"] is True
    assert report["readyForLiveTest"] is True
    assert report["blockingReasons"] == []


def test_live_testing_readiness_rejects_ready_plan_when_new_snapshot_has_no_live_profit():
    plan = _live_ready_plan()
    plan["generatedAt"] = "2026-08-31T08:08:32.710541+00:00"
    snapshot = {
        "updated_at": "2026-09-03T00:16:06.002936+00:00",
        "snapshots": {
            "46630": {
                "updated_at": "2026-09-03T00:16:06.002936+00:00",
                "summary": {
                    "live_profitable_route_count": 0,
                    "live_verified_route_count": 3,
                },
            }
        },
    }

    report = summarize_robinhood_testnet_live_testing_readiness(
        chain_metadata=_base_chain_metadata(),
        contracts_metadata=_base_contracts_metadata(),
        chain_test_plan=plan,
        current_opportunity_snapshot=snapshot,
    )

    assert report["readyForLiveTest"] is False
    assert report["chainTestReady"] is False
    assert report["gates"]["opportunitySnapshotNewerThanPlan"] is True
    assert report["gates"]["currentSnapshotHasNoLiveProfitableRoute"] is True
    assert "chain_test_plan_stale_against_current_opportunity_snapshot" in report["blockingReasons"]
    assert "current_opportunity_snapshot_has_no_live_profitable_route" in report["blockingReasons"]


def test_live_testing_readiness_cli_writes_report(tmp_path, monkeypatch):
    chain_path = tmp_path / "contract" / "robinhood" / "testnet" / "chain.json"
    contracts_path = tmp_path / "contract" / "robinhood" / "testnet" / "contracts.json"
    plan_path = tmp_path / "runtime" / "evidence" / "robinhood-testnet" / "chain_test" / "latest" / "plan.json"
    output_dir = tmp_path / "runtime" / "evidence" / "robinhood-testnet" / "live_testing_readiness"
    _write_json(chain_path, _base_chain_metadata())
    _write_json(contracts_path, _base_contracts_metadata())
    _write_json(plan_path, _live_ready_plan())
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_robinhood_live_testing_readiness.py",
            "--chain-json",
            str(chain_path),
            "--contracts-json",
            str(contracts_path),
            "--plan-json",
            str(plan_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    exit_code = live_testing_readiness_main()
    latest = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert latest["readyForLiveTest"] is True
    assert latest["broadcastEnabled"] is True
    assert latest["gates"]["normalTestnetProbeReady"] is True
