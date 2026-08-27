from core.evidence_scope import evidence_scope_matches, evidence_scope_mismatch_reasons, infer_evidence_scope


def test_infer_evidence_scope_reads_top_level_fields():
    scope = infer_evidence_scope(
        {
            "network": "Robinhood-Testnet",
            "chain_id": "46630",
            "market_id": "robinhood-testnet-dex-paused",
        }
    )

    assert scope.network == "robinhood-testnet"
    assert scope.chain_id == 46630
    assert scope.market_id == "robinhood-testnet-dex-paused"


def test_infer_evidence_scope_reads_nested_live_onchain_evidence():
    scope = infer_evidence_scope(
        {
            "quote": {
                "market_id": "robinhood-dex-shadow",
                "live_onchain_evidence": {
                    "network": "robinhood",
                    "chainId": 4663,
                },
            }
        }
    )

    assert scope.network == "robinhood"
    assert scope.chain_id == 4663
    assert scope.market_id == "robinhood-dex-shadow"


def test_evidence_scope_rejects_same_network_wrong_chain_id():
    payload = {"network": "robinhood-testnet", "chain_id": 46630}

    assert evidence_scope_matches(payload, networks=["robinhood-testnet"], chain_ids=[46630]) is True
    assert evidence_scope_matches(payload, networks=["robinhood-testnet"], chain_ids=[4663]) is False
    assert evidence_scope_mismatch_reasons(
        payload,
        expected_network="robinhood-testnet",
        expected_chain_id=4663,
    ) == ["chain_id_scope_mismatch"]


def test_evidence_scope_rejects_wrong_market_id():
    payload = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "robinhood-testnet-dex-paused",
    }

    assert evidence_scope_matches(payload, market_ids=["robinhood-testnet-dex-paused"]) is True
    assert evidence_scope_matches(payload, market_ids=["robinhood-dex-shadow"]) is False
    assert evidence_scope_mismatch_reasons(
        payload,
        expected_network="robinhood-testnet",
        expected_chain_id=46630,
        expected_market_id="robinhood-dex-shadow",
    ) == ["market_id_scope_mismatch"]
