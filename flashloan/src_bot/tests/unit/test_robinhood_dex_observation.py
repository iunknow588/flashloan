from dex.robinhood_dex_panel import (
    load_latest_robinhood_evidence,
    robinhood_dex_observation_payload,
    robinhood_dex_pool_observation_payload,
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
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": [{"name": "eth_blockNumber", "value": 99}]},
    )

    payload = robinhood_dex_observation_payload(
        database_url=None,
        candidate_cache_path=cache_path,
        evidence_root=cache_path.parent / "missing-evidence",
    )

    assert payload["candidateSource"] == "cache"
    assert payload["chainStatus"]["ok"] is True
    assert payload["summary"]["executionEnabled"] is False
    assert payload["summary"]["deploymentLevel"] == "framework_and_observation_shadow"
    assert payload["deploymentStatus"]["deploymentComplete"] is False
    assert payload["candidates"] == []
    assert payload["observationStatus"] == "awaiting_discovery_evidence"
    assert payload["observationDiagnostics"]["candidateCount"] == 0


def test_robinhood_observation_rejects_wrong_market_scope():
    try:
        robinhood_dex_observation_payload(market_id="bnb-venus-core", chain_id=56, run_rpc_probe=False)
    except ValueError as exc:
        assert "belongs to" in str(exc) or "requires" in str(exc)
    else:
        raise AssertionError("wrong market scope must be rejected")


def test_robinhood_observation_rejects_robinhood_testnet_scope():
    try:
        robinhood_dex_observation_payload(
            market_id="robinhood-testnet-dex-paused",
            chain_id=46630,
            run_rpc_probe=False,
        )
    except ValueError as exc:
        assert "Robinhood DEX observation requires the dex-arbitrage-4663-uniswap-v3 market scope" in str(exc)
    else:
        raise AssertionError("Robinhood testnet must not use the Robinhood mainnet DEX observation handler")


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
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
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
        "dex.robinhood_dex_panel.ROBINHOOD_MARKET_SNAPSHOT_PATH",
        tmp_path / "missing-market-snapshot.json",
    )
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
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
    assert "factory" in payload["observationDiagnostics"]["nextAction"].lower()


def test_observation_payload_suppresses_pnl_without_verified_candidate_pool(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        '{"network":"robinhood","chainId":4663,"blockNumber":123,"bestRoute":{"grossProfit":"-0.2"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("dex.robinhood_dex_panel.ROBINHOOD_MARKET_SNAPSHOT_PATH", snapshot)
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": []},
    )

    payload = robinhood_dex_observation_payload(
        database_url=None,
        candidate_cache_path=tmp_path / "missing-candidates.json",
        evidence_root=tmp_path / "missing-evidence",
    )

    assert payload["observationStatus"] == "awaiting_discovery_evidence"
    assert payload["summary"]["candidateCount"] == 0
    assert payload["summary"]["marketSnapshotAvailable"] is False
    assert payload["marketSnapshot"] is None
    assert payload["observationDiagnostics"]["marketSnapshotSuppressed"] is True
    assert payload["observationDiagnostics"]["bestRouteGrossProfit"] is None


def test_observation_payload_publishes_pnl_only_after_verified_candidate_pool(tmp_path, monkeypatch):
    cache_path = tmp_path / "candidates.json"
    cache_path.write_text(
        '{"candidates":[{"routeKey":"USDG-WETH-USDG","routeSymbols":["USDG","WETH","USDG"],'
        '"poolAddresses":["0xpool1","0xpool2"],"status":"observed",'
        '"evidence":{"pools":[{"eventVerified":true,"poolCodeVerified":true},'
        '{"eventVerified":true,"poolCodeVerified":true}]}}]}',
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        '{"network":"robinhood","chainId":4663,"blockNumber":123,'
        '"schemaVersion":"robinhood-market-snapshot-v1","bestRoute":{"grossProfit":"-0.2"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("dex.robinhood_dex_panel.ROBINHOOD_MARKET_SNAPSHOT_PATH", snapshot)
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": [{"name": "eth_blockNumber", "value": 123}]},
    )

    payload = robinhood_dex_observation_payload(
        database_url=None,
        candidate_cache_path=cache_path,
        evidence_root=tmp_path / "missing-evidence",
    )

    assert payload["observationStatus"] == "candidates_available"
    assert payload["summary"]["candidateCount"] == 1
    assert payload["summary"]["marketSnapshotAvailable"] is False
    assert payload["marketSnapshot"] is None
    assert payload["observationDiagnostics"]["marketSnapshotSuppressed"] is True


def test_pool_observation_payload_exposes_only_pool_surface(tmp_path, monkeypatch):
    evidence_root = tmp_path / "evidence"
    evidence_dir = evidence_root / "run"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "factory_events.json").write_text(
        '{"network":"robinhood","chainId":4663,"factoryCodeVerified":true}',
        encoding="utf-8",
    )
    (evidence_dir / "pool_static_table.json").write_text(
        '{"pools":[{"pool":"0xpool","status":"observable","liquidity":"10",'
        '"evidence":{"factoryQueryVerified":true,"poolCodeVerified":true,'
        '"poolStateVerified":true,"discoveryMethod":"factory_getPool_readonly"}}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": []},
    )

    payload = robinhood_dex_pool_observation_payload(
        database_url=None,
        candidate_cache_path=tmp_path / "missing-candidates.json",
        evidence_root=evidence_root,
    )

    assert payload["summary"]["poolCount"] == 1
    assert len(payload["pools"]) == 1
    assert "candidates" not in payload
    assert "marketSnapshot" not in payload
    assert payload["summary"]["executionEnabled"] is False
