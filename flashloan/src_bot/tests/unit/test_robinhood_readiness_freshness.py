from datetime import datetime, timezone

from intent_trade.chains.robinhood.readiness import robinhood_deployment_status


def _market():
    return {
        "market_id": "robinhood-dex-shadow",
        "network": "robinhood",
        "chain_id": 4663,
        "wrapped_native_address": "0x" + "1" * 40,
        "multicall3_address": "0x" + "2" * 40,
        "observation_only": True,
    }


def test_expired_discovery_and_quote_evidence_are_blocked():
    now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    report = robinhood_deployment_status(
        market=_market(),
        rpc_status={"ok": True},
        discovery_report={
            "network": "robinhood",
            "chainId": 4663,
            "factoryCodeVerified": True,
            "generatedAt": "2026-08-18T12:00:00+00:00",
        },
        quote_report={
            "network": "robinhood",
            "chainId": 4663,
            "status": "passed",
            "generatedAt": "2026-08-18T12:00:00+00:00",
        },
        now=now,
        evidence_max_age_seconds=3600,
    )

    stages = {stage["key"]: stage for stage in report["stages"]}
    assert stages["dex_discovery"]["status"] == "not_started"
    assert stages["live_quote"]["status"] == "not_started"
    assert "robinhood_pool_discovery_evidence_expired" in report["blockingReasons"]
    assert "robinhood_live_quote_evidence_expired" in report["blockingReasons"]
    assert report["evidenceFreshness"]["discovery"]["status"] == "expired"


def test_fresh_evidence_is_reported_without_enabling_broadcast():
    now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    common = {
        "network": "robinhood",
        "chainId": 4663,
        "generatedAt": "2026-08-20T11:30:00+00:00",
    }
    report = robinhood_deployment_status(
        market=_market(),
        rpc_status={"ok": True},
        discovery_report={**common, "factoryCodeVerified": True},
        quote_report={**common, "status": "passed"},
        executor_address="0x" + "3" * 40,
        flashloan_provider="0x" + "4" * 40,
        fork_simulation_report={**common, "status": "passed"},
        now=now,
        evidence_max_age_seconds=3600,
    )

    assert report["evidenceFreshness"]["quote"]["status"] == "fresh"
    assert report["executionReady"] is True
    assert report["executionEnabled"] is False
    assert report["broadcastEnabled"] is False
