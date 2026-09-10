from dex.error_stack import route_error_stack, stack_from_dex_payload
from core.error_registry import ErrorCode
from runtime.dex_quote_evidence_helpers import _dex_error_stack_from_hop


def test_route_error_stack_returns_single_ok_for_live_verified():
    rows = route_error_stack(status="live_verified", message="OK")
    assert rows == [{"module": "DEX", "code": 0, "message": "OK"}]


def test_route_error_stack_uses_pool_not_initialized_code():
    rows = route_error_stack(
        status="pool_not_initialized",
        message="V4 pool not initialized",
    )
    assert rows[0]["module"] == "DEX"
    assert rows[0]["code"] == int(ErrorCode.DEX_POOL_NOT_INITIALIZED)
    assert rows[0]["message"] == "V4 pool not initialized"


def test_route_error_stack_reuses_child_error_stack():
    child = [{"module": "DEX", "code": int(ErrorCode.DEX_LIVE_QUOTE_FAILED), "message": "hop 1 A->B: bad"}]
    rows = route_error_stack(
        status="live_quote_failed",
        evidence={"errorStack": child},
    )
    assert rows == child


def test_route_error_stack_maps_candidate_pair_missing_to_route_invalid():
    rows = route_error_stack(
        status="candidate_pair_not_found",
        message="candidate_pair_not_found",
    )
    assert rows[0]["code"] == int(ErrorCode.DEX_ROUTE_INVALID)


def test_route_error_stack_builds_hop_focus_message():
    rows = route_error_stack(
        status="live_quote_failed",
        evidence={
            "hops": [
                {"hop": 2, "fromSymbol": "ETH", "toSymbol": "USDC", "liveQuoteError": "PoolNotInitialized()"}
            ]
        },
    )
    assert rows[0]["code"] == int(ErrorCode.DEX_LIVE_QUOTE_FAILED)
    assert "hop 2 ETH->USDC: PoolNotInitialized()" in rows[0]["message"]


def test_stack_from_dex_payload_normalizes_route_status():
    rows = stack_from_dex_payload(
        {
            "quoteStatus": "pool_not_initialized",
            "blockers": ["V4 pool is not initialized or cannot be quoted"],
            "errorStack": [{"module": "DEX", "code": int(ErrorCode.DEX_POOL_NOT_INITIALIZED), "message": "V4 pool missing"}],
        }
    )
    assert rows[0]["code"] == int(ErrorCode.DEX_POOL_NOT_INITIALIZED)
    assert rows[0]["message"] == "V4 pool missing"


def test_stack_from_dex_payload_returns_ok_when_no_status_error():
    rows = stack_from_dex_payload({"quoteStatus": "live_verified"})
    assert rows == [{"module": "DEX", "code": 0, "message": "OK"}]


def test_route_error_stack_prefers_hop_child_over_generic_evidence_stack():
    rows = route_error_stack(
        status="requires_live_quoter",
        evidence={
            "errorStack": [
                {"module": "DEX", "code": int(ErrorCode.DEX_GATE_NOT_READY), "message": "requires_live_quoter"}
            ],
            "hops": [
                {
                    "hop": 2,
                    "fromSymbol": "0XA",
                    "toSymbol": "0XB",
                    "status": "live_quote_failed",
                    "liveQuoteError": "robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)",
                    "errorStack": [
                        {
                            "module": "DEX",
                            "code": int(ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY),
                            "message": "hop 2 0XA->0XB: NotEnoughLiquidity(bytes32)",
                        }
                    ],
                }
            ],
        },
    )
    assert rows[0]["code"] == int(ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY)
    assert "NotEnoughLiquidity" in rows[0]["message"]


def test_hop_error_stack_maps_not_enough_liquidity():
    rows = _dex_error_stack_from_hop(
        {
            "hop": 2,
            "fromSymbol": "0XA",
            "toSymbol": "0XB",
            "status": "live_quote_failed",
            "liveQuoteError": "robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)",
        }
    )
    assert rows[0]["code"] == int(ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY)
    assert "NotEnoughLiquidity" in rows[0]["message"]
