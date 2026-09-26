from __future__ import annotations


def _quote_payload_for_robinhood_testnet(*, symbol_pair: str = "TZ -> TA -> TB -> TZ") -> dict:
    return {
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630},
        "summary": {
            "routeCount": 1,
            "completeStaticQuoteCount": 1,
            "executableCandidateCount": 0,
            "profitableRouteCount": 1,
            "liveQuoterRequiredCount": 1,
            "bestNetProfitUsd": 0.5,
        },
        "routes": [
            {
                "symbolPair": symbol_pair,
                "routeSymbols": symbol_pair.split(" -> "),
                "routeAddresses": [
                    "0x" + "1" * 40,
                    "0x" + "2" * 40,
                    "0x" + "3" * 40,
                    "0x" + "1" * 40,
                ],
                "quoteStatus": "requires_live_quoter",
                "quoteInputs": {
                    "candidatePools": [],
                    "quoteRequests": [{"venue": "uniswap_v4", "status": "sdk_adapter_required"}],
                },
                "quoteAnalysis": {
                    "complete": True,
                    "requiresLiveQuoter": True,
                    "referenceInputSymbol": "TZ",
                    "referenceInputAmount": 1.0,
                    "referenceNetProfitUsd": 0.5,
                    "steps": [
                        {"hop": 1, "fromSymbol": "TZ", "toSymbol": "TA"},
                        {"hop": 2, "fromSymbol": "TA", "toSymbol": "TB"},
                        {"hop": 3, "fromSymbol": "TB", "toSymbol": "TZ"},
                    ],
                },
            }
        ],
    }


def _profitable_live_evidence(route) -> dict:
    return {
        "status": "live_verified",
        "priceEvidenceMode": "live_verified",
        "livePriceVerified": True,
        "trustedForExecution": False,
        "readyForTransactionPreflight": True,
        "profitable": True,
        "blockNumber": 123456,
        "routeSymbols": route.get("routeSymbols") or [],
        "routeAddresses": route.get("routeAddresses") or [],
        "hops": [{"hop": 1, "status": "live_verified"}],
        "costModel": {
            "selectedBorrowInputAmount": 1.5,
            "selectedBorrowInputAmountRaw": 1500000000000000000,
            "borrowInputAmount": 1.5,
            "netProfitUsd": 0.5,
            "netProfitAmount": 0.5,
            "netProfitBps": 33.0,
            "profitBasis": "borrow_token",
        },
        "validation": {
            "allHopsQuoted": True,
            "routeClosed": True,
            "profitableAfterCosts": True,
            "finalOutputSymbol": "TZ",
        },
        "blockers": [],
        "transactionDevelopment": {"status": "ready_for_transaction_preflight_development", "executionEnabled": False},
    }


def _scanner(tmp_path, *, evidence=None, callback=None, enabled=None, cooldown=600.0):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    def onchain_evidence_provider(route, **kwargs):
        return evidence(route) if evidence is not None else _profitable_live_evidence(route)

    return DexOpportunityScanner(
        quote_payload_provider=lambda *, chain_id=None, protocols=None: _quote_payload_for_robinhood_testnet(),
        onchain_evidence_provider=onchain_evidence_provider,
        trigger_context_provider=lambda **kwargs: {"source": "test", "triggerBlockNumber": 123456},
        auto_execute_callback=callback,
        auto_execute_enabled=enabled,
        auto_execute_cooldown_seconds=cooldown,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        record_opportunities=False,
        max_onchain_evidence_routes=1,
    )


def test_auto_execute_disabled_by_default_keeps_callback_unused(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append((route_symbols, amount, broadcast, chain_id))
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == []
    assert result["scanner"]["auto_execute_enabled"] is False
    assert result["scanner"]["auto_execute_callback_configured"] is True
    assert "autoExecuteEnabled" not in result["snapshot"]["recording"]
    assert scanner._auto_execute_count == 0
    assert scanner._last_auto_execute_at is None


def test_auto_execute_calls_callback_only_for_live_verified_profitable_route(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append({"route_symbols": route_symbols, "amount": amount, "broadcast": broadcast, "chain_id": chain_id})
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback, enabled=True)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == [
        {
            "route_symbols": ["TZ", "TA", "TB", "TZ"],
            "amount": 1.5,
            "broadcast": True,
            "chain_id": 46630,
        }
    ]
    recording = result["snapshot"]["recording"]
    assert recording["autoExecuteEnabled"] is True
    assert recording["autoExecuted"] == 1
    assert recording["autoExecuteFailed"] == 0
    assert scanner._auto_execute_count == 1
    assert scanner._last_auto_execute_at is not None


def test_auto_execute_skips_without_live_evidence(tmp_path):
    calls = []

    def callback(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "txHash": "0xtx"}

    def no_evidence(route):
        return {
            "status": "live_quote_failed",
            "trustedForExecution": False,
            "readyForTransactionPreflight": False,
            "blockNumber": 123,
            "hops": [],
            "costModel": {},
            "blockers": ["quote failed"],
        }

    scanner = _scanner(tmp_path, evidence=no_evidence, callback=callback, enabled=True)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert calls == []
    recording = result["snapshot"]["recording"]
    assert recording["autoExecuteSkipped"] == 1
    assert recording["autoExecuted"] == 0
    snapshot_route = result["snapshot"]["payload"]["routes"][0]
    assert snapshot_route["quoteAnalysis"]["live_onchain_evidence"]["status"] == "live_quote_failed"


def test_auto_execute_respects_cooldown_keyed_to_route_amount_and_block(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append({"route_symbols": route_symbols, "amount": amount, "broadcast": broadcast, "chain_id": chain_id})
        return {"ok": True, "txHash": "0xtx", "routeSymbols": route_symbols}

    scanner = _scanner(tmp_path, callback=callback, enabled=True, cooldown=3600.0)

    first = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])
    second = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert len(calls) == 1
    assert first["snapshot"]["recording"]["autoExecuted"] == 1
    assert second["snapshot"]["recording"]["autoExecuted"] == 0
    assert second["snapshot"]["recording"]["autoExecuteSkipped"] == 1


def test_auto_execute_records_failure_and_attempt_markers(tmp_path):
    calls = []

    def callback(*, route_symbols, amount, broadcast, chain_id):
        calls.append((route_symbols, amount))
        return {"ok": False, "error": "official_arb_broadcast_rpc_unavailable", "txHash": None}

    scanner = _scanner(tmp_path, callback=callback, enabled=True)
    result = scanner.scan_now(chain_id=46630, protocols=["uniswap_v4"])

    assert len(calls) == 1
    recording = result["snapshot"]["recording"]
    assert recording["autoExecuted"] == 0
    assert recording["autoExecuteFailed"] == 1
    assert recording["autoExecuteErrors"] == ["official_arb_broadcast_rpc_unavailable"]
    snapshot_route = result["snapshot"]["payload"]["routes"][0]
    assert snapshot_route["quoteAnalysis"]["live_onchain_evidence"]["status"] == "live_verified"
