from core.db.storage_cow_execution import append_cow_execution_attempts_jsonl, load_recent_cow_execution_attempts_jsonl
from core.db.cow_execution_review import _build_review_summary, _review_route_identity


def _attempt_with_route_addresses(route_addresses):
    return {
        "observed_at": "2026-08-25T06:29:58+00:00",
        "network": "robinhood",
        "chain_id": 4663,
        "pair": "USDG -> WETH -> VLAD -> WETH -> USDG",
        "pair_rank": 1,
        "priority_reason": "live_quote_verified_preflight_required",
        "route_path": ["USDG", "WETH", "VLAD", "WETH", "USDG"],
        "route_addresses": route_addresses,
        "state": "ready_for_transaction_development",
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "quote": {
            "routeAddresses": route_addresses,
            "live_onchain_evidence": {
                "status": "live_verified",
                "trustedForExecution": False,
                "readyForTransactionPreflight": True,
                "routeSymbols": ["USDG", "WETH", "VLAD", "WETH", "USDG"],
                "routeAddresses": route_addresses,
                "hops": [
                    {"hop": 1, "fromSymbol": "USDG", "toSymbol": "WETH", "tokenIn": route_addresses[0], "tokenOut": route_addresses[1]},
                    {"hop": 2, "fromSymbol": "WETH", "toSymbol": "VLAD", "tokenIn": route_addresses[1], "tokenOut": route_addresses[2]},
                    {"hop": 3, "fromSymbol": "VLAD", "toSymbol": "WETH", "tokenIn": route_addresses[2], "tokenOut": route_addresses[3]},
                    {"hop": 4, "fromSymbol": "WETH", "toSymbol": "USDG", "tokenIn": route_addresses[3], "tokenOut": route_addresses[4]},
                ],
                "validation": {"allHopsQuoted": True, "profitableAfterCosts": True},
                "costModel": {"netProfitBps": 100.0},
                "blockers": [],
            },
        },
    }


def test_review_summary_blocks_same_symbol_token_address_discontinuity(tmp_path):
    usdg = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
    weth = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
    vlad_a = "0x91e2ce85c223CD55b0Cf76Ca668a0e61ed696C6b"
    vlad_b = "0xfDBb27E971E72E38C41b1C772FB409C94dd92789"
    attempt = _attempt_with_route_addresses([usdg, weth, vlad_a, weth, usdg])
    attempt["quote"]["live_onchain_evidence"]["hops"][2]["tokenIn"] = vlad_b

    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, [attempt]) == 1
    row = load_recent_cow_execution_attempts_jsonl(path, limit=1, networks=["robinhood"])[0]
    evidence = row["review_summary"]["onchain_evidence"]

    assert evidence["route_identity_status"] == "token_discontinuity"
    assert evidence["readyForTransactionPreflight"] is False
    assert evidence["tokenContinuity"] is False
    assert evidence["tokenContinuityIssues"][0]["previousTokenOut"] == vlad_a
    assert evidence["tokenContinuityIssues"][0]["currentTokenIn"] == vlad_b
    assert any("token 地址不连续" in blocker for blocker in evidence["blockers"])


def test_market_candidate_jsonl_dedupe_uses_route_addresses(tmp_path):
    usdg = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
    weth = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
    vlad_a = "0x91e2ce85c223CD55b0Cf76Ca668a0e61ed696C6b"
    vlad_b = "0xfDBb27E971E72E38C41b1C772FB409C94dd92789"
    first = _attempt_with_route_addresses([usdg, weth, vlad_a, weth, usdg])
    second = _attempt_with_route_addresses([usdg, weth, vlad_b, weth, usdg])

    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, [first, second]) == 2
    assert append_cow_execution_attempts_jsonl(path, [first]) == 0
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10, networks=["robinhood"])

    assert len(rows) == 2
    assert {row["review_summary"]["onchain_evidence"]["routeAddresses"][2].lower() for row in rows} == {
        vlad_a.lower(),
        vlad_b.lower(),
    }


def test_review_summary_infers_net_positive_for_legacy_evidence(tmp_path):
    usdg = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
    weth = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
    cats = "0x7195e2088f3DFCca56e6A71D129051963Ed9657A"
    attempt = _attempt_with_route_addresses([usdg, weth, cats, weth, usdg])
    evidence = attempt["quote"]["live_onchain_evidence"]
    evidence["readyForTransactionPreflight"] = False
    evidence["validation"]["profitableAfterCosts"] = False
    evidence["costModel"]["netProfitUsd"] = 0.612327
    evidence["costModel"]["minProfitUsd"] = 2.0

    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, [attempt]) == 1
    row = load_recent_cow_execution_attempts_jsonl(path, limit=1, networks=["robinhood"])[0]
    reviewed = row["review_summary"]["onchain_evidence"]

    assert reviewed["netPositiveAfterCosts"] is True
    assert reviewed["meetsMinProfitThreshold"] is False
    assert reviewed["readyForTransactionPreflight"] is False


def test_jsonl_attempt_loading_requires_network_and_chain_scope(tmp_path):
    route_addresses = [
        "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
        "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
        "0x91e2ce85c223CD55b0Cf76Ca668a0e61ed696C6b",
        "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
        "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
    ]
    mainnet = _attempt_with_route_addresses(route_addresses)
    testnet = _attempt_with_route_addresses(route_addresses)
    testnet["network"] = "robinhood-testnet"
    testnet["chain_id"] = 46630
    testnet["market_id"] = "robinhood-testnet-dex-paused"
    testnet["quote"]["market_id"] = "robinhood-testnet-dex-paused"
    testnet["quote"]["live_onchain_evidence"]["network"] = "robinhood-testnet"
    testnet["quote"]["live_onchain_evidence"]["chainId"] = 46630

    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, [mainnet, testnet]) == 2

    rows = load_recent_cow_execution_attempts_jsonl(
        path,
        limit=10,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )
    mismatched = load_recent_cow_execution_attempts_jsonl(
        path,
        limit=10,
        networks=["robinhood"],
        chain_ids=[46630],
    )

    assert len(rows) == 1
    assert rows[0]["network"] == "robinhood-testnet"
    assert rows[0]["chain_id"] == 46630
    assert mismatched == []


def test_jsonl_append_rejects_contradictory_nested_scope(tmp_path):
    attempt = _attempt_with_route_addresses(["0x1", "0x2", "0x3", "0x2", "0x1"])
    attempt["quote"]["live_onchain_evidence"]["network"] = "robinhood-testnet"
    attempt["quote"]["live_onchain_evidence"]["chainId"] = 46630

    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, [attempt]) == 0
    assert load_recent_cow_execution_attempts_jsonl(path, limit=10) == []


def test_review_route_identity_resolves_known_robinhood_symbols():
    row = {
        "route_path": ["WETH", "ETH", "0XC9F9C8", "WETH"],
    }
    quote = {
        "path": row["route_path"],
        "hops": [
            {"hop": 1, "fromSymbol": "WETH", "toSymbol": "ETH"},
            {"hop": 2, "fromSymbol": "ETH", "toSymbol": "0XC9F9C8"},
            {"hop": 3, "fromSymbol": "0XC9F9C8", "toSymbol": "WETH"},
        ],
    }
    identity = _review_route_identity(row, quote, {})

    assert identity["status"] == "ok"
    assert identity["route_addresses"] == [
        "0x7943e237c7F95DA44E0301572D358911207852Fa",
        "0x0000000000000000000000000000000000000000",
        "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
        "0x7943e237c7F95DA44E0301572D358911207852Fa",
    ]


def test_review_summary_prefers_quote_business_status_over_identity_warning():
    row = {
        "route_path": ["WETH", "ETH", "0XC9F9C8", "WETH"],
    }
    quote = {
        "path": row["route_path"],
        "quote_status": "not_enough_liquidity",
        "execution_trust": "not_enough_liquidity",
        "hops": [
            {"hop": 1, "fromSymbol": "WETH", "toSymbol": "ETH"},
            {"hop": 2, "fromSymbol": "ETH", "toSymbol": "0XC9F9C8"},
            {"hop": 3, "fromSymbol": "0XC9F9C8", "toSymbol": "WETH"},
        ],
        "live_onchain_evidence": {},
    }
    row["quote"] = quote
    evidence = _build_review_summary(row)["onchain_evidence"]

    assert evidence["status"] == "not_enough_liquidity"
    assert evidence["route_identity_status"] == "ok"
    assert any("LP" in blocker for blocker in evidence["blockers"])
    assert not any("地址身份" in blocker for blocker in evidence["blockers"])


def test_route_test_recorder_persists_quote_evidence_and_addresses(tmp_path, monkeypatch):
    from dex.runtime.dex_route_test_recorder import record_dex_route_test_attempt

    log_path = tmp_path / "attempts.jsonl"
    monkeypatch.setattr(
        "dex.runtime.dex_route_test_recorder.cow_execution_attempt_log_path",
        lambda **kwargs: log_path,
    )
    route_symbols = ["WETH", "ETH", "0XC9F9C8", "WETH"]
    route_addresses = [
        "0x7943e237c7F95DA44E0301572D358911207852Fa",
        "0x0000000000000000000000000000000000000000",
        "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
        "0x7943e237c7F95DA44E0301572D358911207852Fa",
    ]
    payload = {
        "ok": True,
        "quoteStatus": "not_enough_liquidity",
        "executionTrust": "not_enough_liquidity",
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "steps": [
            {
                "hop": 1,
                "fromSymbol": "WETH",
                "toSymbol": "ETH",
                "tokenIn": route_addresses[0],
                "tokenOut": route_addresses[1],
            }
        ],
        "analysis": {},
    }
    recorded = record_dex_route_test_attempt(
        payload,
        chain_id=46630,
        route_symbols=route_symbols,
        amount=1000.0,
        amount_source="requested",
        mode="estimate",
        broadcast=False,
        fallback_networks=["robinhood-testnet"],
    )

    assert recorded["recorded"] == 1
    rows = load_recent_cow_execution_attempts_jsonl(
        log_path,
        limit=1,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )
    assert len(rows) == 1
    evidence = rows[0]["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "not_enough_liquidity"
    assert evidence["routeAddresses"] == route_addresses
    summary_evidence = rows[0]["review_summary"]["onchain_evidence"]
    assert summary_evidence["status"] == "not_enough_liquidity"
    assert summary_evidence["route_identity_status"] == "ok"
