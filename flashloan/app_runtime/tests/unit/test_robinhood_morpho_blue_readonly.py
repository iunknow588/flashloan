from eth_utils import function_signature_to_4byte_selector

from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.morpho_blue_readonly import (
    EVENT_TOPIC_BY_SIGNATURE,
    MORPHO_BLUE_CORE,
    read_morpho_blue_market,
    read_morpho_blue_position,
    scan_recent_morpho_blue_events,
    summarize_robinhood_morpho_blue_readonly,
)


def _selector(signature: str) -> str:
    return "0x" + function_signature_to_4byte_selector(signature).hex()


def _code_with_selectors(*signatures: str) -> str:
    body = "00" * 64
    for signature in signatures:
        body += "63" + _selector(signature).removeprefix("0x")
    return "0x" + body


def _word(value: int) -> str:
    return f"{value:064x}"


def _fake_readonly_transport(code: str, market_data: str | None = None, position_data: str | None = None):
    market_selector = _selector("market(bytes32)")
    position_selector = _selector("position(bytes32,address)")

    def transport(url: str, payload: dict, timeout: float) -> dict:
        method = payload["method"]
        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": hex(robinhood.CHAIN_ID)}
        if method == "eth_blockNumber":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x1234"}
        if method == "eth_getCode":
            return {"jsonrpc": "2.0", "id": 1, "result": code}
        if method == "eth_getLogs":
            return {"jsonrpc": "2.0", "id": 1, "result": []}
        if method == "eth_call":
            data = payload["params"][0]["data"]
            if data.startswith(market_selector) and market_data is not None:
                return {"jsonrpc": "2.0", "id": 1, "result": market_data}
            if data.startswith(position_selector) and position_data is not None:
                return {"jsonrpc": "2.0", "id": 1, "result": position_data}
            return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "unexpected call"}}
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "unsupported"}}

    return transport


def test_morpho_blue_readonly_records_code_and_required_selectors():
    code = _code_with_selectors(
        "market(bytes32)",
        "position(bytes32,address)",
        "flashLoan(address,uint256,bytes)",
        "owner()",
    )
    report = summarize_robinhood_morpho_blue_readonly(
        rpc_url="https://rpc.example.invalid",
        transport=_fake_readonly_transport(code),
    )

    assert report["rpcOk"] is True
    assert report["code"]["hasCode"] is True
    assert report["requiredReadonlySelectorProbesPresent"] is True
    assert report["flashLoanSelectorPresent"] is True
    assert report["broadcastEnabled"] is False
    assert "robinhood_morpho_blue_core_source_unverified" in report["blockingReasons"]


def test_morpho_blue_readonly_blocks_wrong_chain():
    def wrong_chain_transport(url: str, payload: dict, timeout: float) -> dict:
        if payload["method"] == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": hex(robinhood.TESTNET_CHAIN_ID)}
        return {"jsonrpc": "2.0", "id": 1, "result": "0x"}

    report = summarize_robinhood_morpho_blue_readonly(
        rpc_url="https://rpc.example.invalid",
        transport=wrong_chain_transport,
    )

    assert report["rpcOk"] is False
    assert "robinhood_morpho_blue_wrong_chain" in report["blockingReasons"]


def test_morpho_blue_readonly_blocks_missing_code():
    report = summarize_robinhood_morpho_blue_readonly(
        rpc_url="https://rpc.example.invalid",
        transport=_fake_readonly_transport("0x"),
    )

    assert report["rpcOk"] is False
    assert "robinhood_morpho_blue_core_code_missing" in report["blockingReasons"]
    assert "robinhood_morpho_blue_account_pool_empty" in report["blockingReasons"]


def test_read_morpho_blue_market_decodes_aggregates():
    market_data = "0x" + _word(1000) + _word(500) + _word(200) + _word(50) + _word(1234) + _word(1)
    result = read_morpho_blue_market(
        "https://rpc.example.invalid",
        "0x" + "11" * 32,
        transport=_fake_readonly_transport("0x", market_data=market_data),
    )

    assert result["ok"] is True
    assert result["totalSupplyAssets"] == "1000"
    assert result["totalBorrowAssets"] == "200"
    assert result["hasBorrowLiquidity"] is True


def test_read_morpho_blue_position_decodes_shares():
    position_data = "0x" + _word(7) + _word(3)
    result = read_morpho_blue_position(
        "https://rpc.example.invalid",
        "0x" + "11" * 32,
        "0x" + "22" * 20,
        transport=_fake_readonly_transport("0x", position_data=position_data),
    )

    assert result["ok"] is True
    assert result["supplyShares"] == "7"
    assert result["borrowShares"] == "3"
    assert result["hasBorrow"] is True
    assert result["healthFactorUsable"] is False


def test_morpho_blue_readonly_reads_supplied_markets_and_accounts():
    code = _code_with_selectors("market(bytes32)", "position(bytes32,address)")
    market_data = "0x" + _word(1000) + _word(500) + _word(200) + _word(50) + _word(1234) + _word(1)
    position_data = "0x" + _word(0) + _word(9)
    report = summarize_robinhood_morpho_blue_readonly(
        rpc_url="https://rpc.example.invalid",
        transport=_fake_readonly_transport(code, market_data=market_data, position_data=position_data),
        market_ids=["0x" + "11" * 32],
        accounts=["0x" + "22" * 20],
    )

    assert report["marketReadCount"] == 1
    assert report["positionReadCount"] == 1
    assert report["positions"][0]["borrowShares"] == "9"
    assert "robinhood_morpho_blue_account_pool_empty" not in report["blockingReasons"]


def test_scan_recent_morpho_blue_events_counts_known_signatures():
    supply_topic = EVENT_TOPIC_BY_SIGNATURE["Supply(bytes32,address,address,uint256,uint256)"]
    flash_topic = EVENT_TOPIC_BY_SIGNATURE["FlashLoan(address,address,uint256)"]
    logs = [
        {
            "blockNumber": "0x1",
            "transactionHash": "0x" + "aa" * 32,
            "logIndex": "0x0",
            "topics": [supply_topic, "0x" + "11" * 32],
            "data": "0x" + "00" * 64,
        },
        {
            "blockNumber": "0x2",
            "transactionHash": "0x" + "bb" * 32,
            "logIndex": "0x1",
            "topics": [flash_topic, "0x" + "22" * 20, "0x" + "33" * 20],
            "data": "0x" + "00" * 32,
        },
    ]

    def transport(url: str, payload: dict, timeout: float) -> dict:
        if payload["method"] == "eth_blockNumber":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x10"}
        if payload["method"] == "eth_getLogs":
            return {"jsonrpc": "2.0", "id": 1, "result": logs}
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "unsupported"}}

    result = scan_recent_morpho_blue_events(
        "https://rpc.example.invalid",
        lookback_blocks=100,
        transport=transport,
    )

    assert result["ok"] is True
    assert result["logCount"] == 2
    assert result["observedActivity"] is True
    assert result["eventCountsBySignature"]["Supply(bytes32,address,address,uint256,uint256)"] == 1
    assert result["eventCountsBySignature"]["FlashLoan(address,address,uint256)"] == 1
