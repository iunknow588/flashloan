from web.liquidation_execution_service import prepare_execution_payload, summarize_execution_result


def test_prepare_execution_payload_keeps_controls_and_summarizes_submission():
    payload = {"request": {"user": "0x1"}, "execution_controls": {"execution_enabled": True}}

    prepared = prepare_execution_payload(payload, controls={"slippage_bps": 50})
    summarized = summarize_execution_result({**prepared, "tx_hash": "0xabc"}, {"status": 1})

    assert prepared["execution_controls"]["slippage_bps"] == 50
    assert summarized["execution_summary"]["status"] == "submitted"
    assert summarized["receipt"]["status"] == 1
