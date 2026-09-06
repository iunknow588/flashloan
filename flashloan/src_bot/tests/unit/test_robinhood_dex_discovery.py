from dex.intent_trade.chains.robinhood.dex_discovery import (
    POOL_CREATED_TOPIC0,
    decode_uniswap_v3_pool_created,
    scan_uniswap_v3_pool_created,
)


def _word(value: int) -> str:
    return f"{value:064x}"


def test_decode_uniswap_v3_pool_created_event():
    token0 = "11" * 20
    token1 = "22" * 20
    pool = "33" * 20
    log = {
        "address": "0x" + "44" * 20,
        "topics": [
            POOL_CREATED_TOPIC0,
            "0x" + "0" * 24 + token0,
            "0x" + "0" * 24 + token1,
            hex(500),
        ],
        "data": "0x" + _word(60) + _word(int(pool, 16)),
        "blockNumber": hex(123),
        "logIndex": hex(2),
    }

    decoded = decode_uniswap_v3_pool_created(log)

    assert decoded["token0"] == "0x" + token0
    assert decoded["token1"] == "0x" + token1
    assert decoded["pool"] == "0x" + pool
    assert decoded["fee"] == 500
    assert decoded["tickSpacing"] == 60


def test_pool_discovery_is_readonly_and_verifies_factory_and_pool_code():
    calls = []

    def transport(_url, payload, _timeout):
        calls.append(payload)
        if payload["method"] == "eth_getCode":
            return {"result": "0x6001"}
        if payload["method"] == "eth_getLogs":
            return {"result": []}
        raise AssertionError(payload["method"])

    report = scan_uniswap_v3_pool_created(
        "https://rpc.example",
        factory_address="0x" + "44" * 20,
        source_url="https://docs.example/factory",
        from_block=10,
        to_block=12,
        chunk_size=2,
        transport=transport,
    )

    assert report["factoryCodeVerified"] is True
    assert report["poolCount"] == 0
    assert report["executionEnabled"] is False
    assert [item["method"] for item in calls] == ["eth_getCode", "eth_getLogs", "eth_getLogs"]
