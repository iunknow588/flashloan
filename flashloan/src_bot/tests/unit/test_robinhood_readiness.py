from dex.intent_trade.chains.robinhood.readiness import robinhood_deployment_status


def _market(**overrides):
    payload = {
        "market_id": "robinhood-dex-shadow",
        "network": "robinhood",
        "chain_id": 4663,
        "wrapped_native_address": "0x" + "1" * 40,
        "multicall3_address": "0x" + "2" * 40,
        "observation_only": True,
    }
    payload.update(overrides)
    return payload


def test_default_status_is_observation_shadow_and_not_deployed():
    report = robinhood_deployment_status(
        market=_market(),
        rpc_status={"ok": True},
    )

    assert report["deploymentComplete"] is False
    assert report["deploymentLevel"] == "framework_and_observation_shadow"
    assert report["executionEnabled"] is False
    assert report["broadcastEnabled"] is False
    assert report["observationReady"] is True
    assert report["dexExecutionReady"] is False
    assert report["liquidationReady"] is False
    assert report["productionReady"] is False
    assert report["executionReady"] is False
    assert "robinhood_executor_unverified" in report["blockingReasons"]
    assert "robinhood_lending_protocol_unverified" in report["blockingReasons"]


def test_verified_evidence_does_not_enable_execution_without_all_gates():
    report = robinhood_deployment_status(
        market=_market(),
        rpc_status={"ok": True},
        discovery_report={"factoryCodeVerified": True},
        quote_report={"status": "passed"},
        executor_address="0x" + "3" * 40,
        flashloan_provider="0x" + "4" * 40,
        fork_simulation_report={"status": "passed"},
    )

    assert report["executionReady"] is True
    assert report["executionEnabled"] is False
    assert report["broadcastEnabled"] is False
    assert all(stage["status"] != "not_started" for stage in report["stages"] if stage["key"] != "liquidation")


def test_wrong_scope_is_blocked():
    report = robinhood_deployment_status(
        market=_market(network="bnb", chain_id=56),
        rpc_status={"ok": True},
    )

    chain_stage = next(stage for stage in report["stages"] if stage["key"] == "chain_integration")
    assert chain_stage["status"] == "blocked"
    assert "robinhood_market_scope_invalid" in chain_stage["blockedReasons"]


def test_evidence_from_another_chain_cannot_upgrade_readiness():
    report = robinhood_deployment_status(
        market=_market(),
        rpc_status={"ok": True},
        discovery_report={
            "network": "avalanche",
            "chainId": 43114,
            "factoryCodeVerified": True,
        },
        quote_report={"network": "avalanche", "chainId": 43114, "status": "passed"},
    )

    stages = {stage["key"]: stage for stage in report["stages"]}
    assert stages["dex_discovery"]["status"] == "not_started"
    assert stages["live_quote"]["status"] == "not_started"
    assert report["dexExecutionReady"] is False
