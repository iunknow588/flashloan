from web3 import Web3

from runtime.oracle_price_events import (
    ANSWER_UPDATED_TOPIC,
    OracleFeed,
    OraclePriceEventPoller,
    parse_answer_updated_logs,
)


def _topic(value: int) -> str:
    return "0x" + int(value).to_bytes(32, "big").hex()


def test_parse_answer_updated_logs_maps_aggregator_to_asset():
    aggregator = Web3.to_checksum_address("0x00000000000000000000000000000000000000AA")
    logs = [
        {
            "address": aggregator,
            "topics": [ANSWER_UPDATED_TOPIC, _topic(2_000_000_000), _topic(7), _topic(123456)],
            "blockNumber": 42,
            "transactionHash": "0xabc",
        }
    ]

    events = parse_answer_updated_logs(
        logs,
        [OracleFeed(asset="WAVAX", aggregator=aggregator, decimals=8)],
    )

    assert events == [
        {
            "asset": "WAVAX",
            "aggregator": aggregator,
            "answer": 2_000_000_000,
            "price": 20.0,
            "updated_at": 123456,
            "block_number": 42,
            "transaction_hash": "0xabc",
            "source": "chainlink_answer_updated",
        }
    ]


def test_oracle_price_event_poller_reads_chunks_and_advances_cursor():
    aggregator = Web3.to_checksum_address("0x00000000000000000000000000000000000000AA")
    calls = []

    class FakeEth:
        block_number = 5

        @staticmethod
        def get_logs(params):
            calls.append((params["fromBlock"], params["toBlock"], params["address"], params["topics"]))
            if params["fromBlock"] == 2:
                return [
                    {
                        "address": aggregator,
                        "topics": [ANSWER_UPDATED_TOPIC, _topic(1_950_000_000), _topic(8), _topic(200)],
                        "blockNumber": 2,
                        "transactionHash": "0xdef",
                    }
                ]
            return []

    class FakeWeb3:
        eth = FakeEth()

    poller = OraclePriceEventPoller(
        FakeWeb3(),
        [{"asset": "WAVAX", "aggregator": aggregator, "decimals": 8}],
        start_block=2,
        chunk_size=2,
    )

    events = poller.poll(to_block=5)
    again = poller.poll(to_block=5)

    assert [event["asset"] for event in events] == ["WAVAX"]
    assert events[0]["price"] == 19.5
    assert calls == [
        (2, 3, [aggregator], [ANSWER_UPDATED_TOPIC]),
        (4, 5, [aggregator], [ANSWER_UPDATED_TOPIC]),
    ]
    assert again == []
