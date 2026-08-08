from web3 import Web3

from tools import collect_liquidation_history as history


def _topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address.removeprefix("0x").lower()


def _word(value: int) -> str:
    return int(value).to_bytes(32, "big").hex()


def _address_word(address: str) -> str:
    return "0" * 24 + address.removeprefix("0x").lower()


def _log(block_number=10, user="0x0000000000000000000000000000000000000003", tx="0xabc"):
    collateral = "0x0000000000000000000000000000000000000001"
    debt = "0x0000000000000000000000000000000000000002"
    liquidator = "0x0000000000000000000000000000000000000004"
    return {
        "address": "0x00000000000000000000000000000000000000aa",
        "blockNumber": block_number,
        "transactionHash": tx,
        "logIndex": 0,
        "topics": [
            history.LIQUIDATION_CALL_TOPIC,
            _topic_address(collateral),
            _topic_address(debt),
            _topic_address(user),
        ],
        "data": "0x" + _word(1000) + _word(2000) + _address_word(liquidator) + _word(0),
    }


def test_decode_liquidation_call_log_parses_topics_and_data():
    event = history.decode_liquidation_call_log(_log())

    assert event["collateral_asset"] == Web3.to_checksum_address("0x0000000000000000000000000000000000000001")
    assert event["debt_asset"] == Web3.to_checksum_address("0x0000000000000000000000000000000000000002")
    assert event["user"] == Web3.to_checksum_address("0x0000000000000000000000000000000000000003")
    assert event["debt_to_cover"] == 1000
    assert event["liquidated_collateral_amount"] == 2000
    assert event["liquidator"] == Web3.to_checksum_address("0x0000000000000000000000000000000000000004")
    assert event["receive_a_token"] is False


def test_summarize_liquidation_events_counts_competition_and_gas():
    events = [
        {
            **history.decode_liquidation_call_log(_log(block_number=10, tx="0x1")),
            "gas_used": 100,
            "effective_gas_price": 25,
        },
        {
            **history.decode_liquidation_call_log(
                _log(block_number=12, user="0x0000000000000000000000000000000000000003", tx="0x2")
            ),
            "liquidator": Web3.to_checksum_address("0x0000000000000000000000000000000000000005"),
            "gas_used": 200,
            "effective_gas_price": 50,
        },
    ]

    summary = history.summarize_liquidation_events(events, days=2, competition_window_blocks=20)

    assert summary["event_count"] == 2
    assert summary["unique_user_count"] == 1
    assert summary["unique_liquidator_count"] == 2
    assert summary["daily_average"] == 1
    assert summary["competition"]["avg_competitors"] == 1.5
    assert summary["gas"]["p90_gas_used"] == 190
    assert summary["profit_usd"]["status"] == "missing_price_inputs"
    assert summary["slippage_bps"]["status"] == "missing_quote_inputs"


def test_summarize_liquidation_events_uses_enriched_profit_and_slippage_samples():
    events = [
        {
            **history.decode_liquidation_call_log(_log(block_number=10, tx="0x1")),
            "estimated_net_profit_usd": 10.0,
            "estimated_slippage_bps": 20.0,
        },
        {
            **history.decode_liquidation_call_log(_log(block_number=11, tx="0x2")),
            "estimated_net_profit_usd": 30.0,
            "estimated_slippage_bps": 60.0,
        },
    ]

    summary = history.summarize_liquidation_events(events, days=1)

    assert summary["profit_usd"]["status"] == "calculated"
    assert summary["profit_usd"]["average"] == 20.0
    assert summary["profit_usd"]["p50"] == 20.0
    assert summary["slippage_bps"]["status"] == "calculated"
    assert summary["slippage_bps"]["p90"] == 56.0


def test_build_markdown_summary_includes_data_quality_status():
    report = {
        "config": {"days": 30},
        "summary": {
            "event_count": 2,
            "daily_average": 0.07,
            "profit_usd": {"status": "missing_price_inputs", "average": 0, "p50": 0, "p90": 0},
            "slippage_bps": {"status": "missing_quote_inputs", "p50": 0, "p90": 0},
            "competition": {"avg_competitors": 1.5},
            "gas": {"p50_gas_used": 100, "p90_gas_used": 190},
        },
    }

    text = history.build_markdown_summary([report])

    assert "| 30 天 | 2 | 0.07 |" in text
    assert "missing_price_inputs / missing_quote_inputs" in text


def test_collect_liquidation_events_chunks_logs_and_receipts():
    calls = []

    class FakeEth:
        block_number = 5

        @staticmethod
        def get_logs(params):
            calls.append((params["fromBlock"], params["toBlock"], params["topics"]))
            if params["fromBlock"] <= 4 <= params["toBlock"]:
                return [_log(block_number=4, tx="0xreceipt")]
            return []

        @staticmethod
        def get_transaction_receipt(tx_hash):
            assert tx_hash == "0xreceipt"
            return {"gasUsed": 321, "effectiveGasPrice": 42}

    class FakeWeb3:
        eth = FakeEth()

    config = history.LiquidationHistoryConfig(
        rpc_url="https://rpc.example",
        pool_address="0x00000000000000000000000000000000000000aa",
        days=1,
        chunk_size=2,
        include_receipts=True,
    )

    events = history.collect_liquidation_events(FakeWeb3(), config)

    assert calls[-1] == (4, 5, [history.LIQUIDATION_CALL_TOPIC])
    assert events[0]["gas_used"] == 321
    assert events[0]["effective_gas_price"] == 42


def test_collect_liquidation_events_redacts_receipt_errors(monkeypatch):
    rpc_url = "https://rpc.example/path?token=abc123"
    private_key = "0x" + "c" * 64
    monkeypatch.setenv("AVALANCHE_RPC_URL", rpc_url)

    class FakeEth:
        block_number = 5

        @staticmethod
        def get_logs(params):
            return [_log(block_number=4, tx="0xreceipt")]

        @staticmethod
        def get_transaction_receipt(tx_hash):
            raise RuntimeError(f"receipt failed: {rpc_url} private_key={private_key}")

    class FakeWeb3:
        eth = FakeEth()

    config = history.LiquidationHistoryConfig(
        rpc_url=rpc_url,
        pool_address="0x00000000000000000000000000000000000000aa",
        days=1,
        chunk_size=2,
        include_receipts=True,
    )

    events = history.collect_liquidation_events(FakeWeb3(), config)
    error = events[0]["receipt_error"]

    assert rpc_url not in error
    assert private_key not in error
    assert "abc123" not in error
    assert "[REDACTED]" in error
