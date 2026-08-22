from web.control_panel_robinhood_dex import (
    load_latest_robinhood_evidence,
    robinhood_dex_observation_payload,
    robinhood_dex_readiness_payload,
)


def test_robinhood_observation_payload_uses_cache_without_database(tmp_path, monkeypatch):
    cache_path = tmp_path / "robinhood_candidates.json"
    cache_path.write_text(
        """
        {
          "candidates": [{
            "routeKey": "weth-usdc-token-weth",
            "routeSymbols": ["WETH", "USDC", "TOKEN", "WETH"],
            "poolAddresses": ["0x1", "0x2", "0x3"],
            "status": "observed",
            "observedBlock": 42,
            "evidence": {"eventVerified": true, "poolCodeVerified": true}
          }]
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "web.control_panel_robinhood_dex.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": [{"name": "eth_blockNumber", "value": 99}]},
    )

    payload = robinhood_dex_observation_payload(
        database_url=None,
        candidate_cache_path=cache_path,
    )

    assert payload["candidateSource"] == "cache"
    assert payload["chainStatus"]["ok"] is True
    assert payload["summary"]["executionEnabled"] is False
    assert payload["summary"]["deploymentLevel"] == "framework_and_observation_shadow"
    assert payload["deploymentStatus"]["deploymentComplete"] is False
    assert payload["candidates"][0]["executionEnabled"] is False
    assert "robinhood_live_quote_unverified" in payload["candidates"][0]["blockedReasons"]
    assert payload["observationStatus"] == "candidates_available"
    assert payload["observationDiagnostics"]["candidateCount"] == 1


def test_robinhood_observation_rejects_wrong_market_scope():
    try:
        robinhood_dex_observation_payload(market_id="bnb-venus-core", chain_id=56, run_rpc_probe=False)
    except ValueError as exc:
        assert "belongs to" in str(exc) or "requires" in str(exc)
    else:
        raise AssertionError("wrong market scope must be rejected")


def test_latest_evidence_loader_rejects_wrong_chain_and_reads_valid_package(tmp_path):
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "factory_events.json").write_text(
        '{"network": "avalanche", "chainId": 43114, "factoryCodeVerified": true}',
        encoding="utf-8",
    )
    valid = tmp_path / "valid"
    valid.mkdir()
    (valid / "factory_events.json").write_text(
        '{"network": "robinhood", "chainId": 4663, "factoryCodeVerified": true}',
        encoding="utf-8",
    )
    (valid / "quote_smoke_report.json").write_text(
        '{"status": "not_started"}',
        encoding="utf-8",
    )

    evidence = load_latest_robinhood_evidence(tmp_path)

    assert evidence["source"] == "evidence_package"
    assert evidence["directory"] == str(valid)
    assert evidence["discoveryReport"]["chainId"] == 4663


def test_readiness_payload_keeps_execution_disabled_with_verified_discovery(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "factory_events.json").write_text(
        '{"network": "robinhood", "chainId": 4663, "factoryCodeVerified": true}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "web.control_panel_robinhood_dex.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": []},
    )

    payload = robinhood_dex_readiness_payload(evidence_root=tmp_path)

    discovery_stage = next(stage for stage in payload["deploymentStatus"]["stages"] if stage["key"] == "dex_discovery")
    assert discovery_stage["status"] == "ready"
    assert payload["deploymentStatus"]["deploymentComplete"] is False
    assert payload["executionEnabled"] is False
    assert payload["broadcastEnabled"] is False


def test_observation_payload_explains_missing_candidates_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "web.control_panel_robinhood_dex.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": []},
    )

    payload = robinhood_dex_observation_payload(
        database_url=None,
        candidate_cache_path=tmp_path / "missing-candidates.json",
        evidence_root=tmp_path / "missing-evidence",
    )

    assert payload["observationStatus"] == "awaiting_discovery_evidence"
    assert payload["summary"]["candidateCount"] == 0
    assert payload["observationDiagnostics"]["rpcHealthy"] is True
    assert payload["observationDiagnostics"]["evidenceAvailable"] is False
    assert payload["observationDiagnostics"]["candidateCache"]["status"] == "missing"
    assert "discovery evidence" in payload["observationDiagnostics"]["nextAction"]
