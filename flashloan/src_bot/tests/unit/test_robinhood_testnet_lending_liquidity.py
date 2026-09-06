from dex.intent_trade.chains.robinhood_testnet.lending_liquidity import (
    MAINNET_MORPHO_BLUE_NOT_TESTNET,
    PERMIT2,
    POOL_MANAGER,
    POSITION_MANAGER,
    TESTNET_MULTICALL3,
    TESTNET_WETH,
    UNIVERSAL_ROUTER,
    summarize_robinhood_testnet_lending_liquidity,
)

TESTNET_SUPPORT_CONTRACTS = {
    POOL_MANAGER,
    POSITION_MANAGER,
    PERMIT2,
    UNIVERSAL_ROUTER,
    TESTNET_WETH,
    TESTNET_MULTICALL3,
}


def test_robinhood_testnet_lending_liquidity_reports_no_configured_lending_pool(monkeypatch):
    for name in (
        "ROBINHOOD_TESTNET_AAVE_POOL",
        "ROBINHOOD_TESTNET_AAVE_V3_POOL",
        "ROBINHOOD_TESTNET_AAVE_POOL_ADDRESS",
        "ROBINHOOD_TESTNET_MORPHO_BLUE",
        "ROBINHOOD_TESTNET_MORPHO_BLUE_ADDRESS",
        "ROBINHOOD_TESTNET_MORPHO",
        "ROBINHOOD_TESTNET_MORPHO_ADDRESS",
        "ROBINHOOD_TESTNET_BORROW_TOKEN_ADDRESSES",
        "ROBINHOOD_TESTNET_TOKEN_ADDRESSES",
        "ROBINHOOD_TESTNET_USDC_ADDRESS",
        "ROBINHOOD_TESTNET_WETH_ADDRESS",
    ):
        monkeypatch.delenv(name, raising=False)

    def transport(_url, payload, _timeout):
        method = payload["method"]
        if method == "eth_chainId":
            return {"result": hex(46630)}
        if method == "eth_getCode":
            address = payload["params"][0]
            return {"result": "0x6001" if address in TESTNET_SUPPORT_CONTRACTS else "0x"}
        if method == "eth_getBalance":
            return {"result": hex(123)}
        raise AssertionError(method)

    report = summarize_robinhood_testnet_lending_liquidity(transport=transport)

    assert report["rpcOk"] is True
    assert report["hasVerifiedBorrowableLiquidity"] is False
    assert report["supportsAaveOrMorphoFlashLoans"] is False
    assert report["supportsV4FlashAccountingTests"] is True
    assert report["testnetContracts"]["v4PoolManager"]["hasCode"] is True
    assert report["testnetContracts"]["positionManager"]["hasCode"] is True
    assert report["testnetContracts"]["permit2"]["hasCode"] is True
    assert report["testnetContracts"]["universalRouter"]["hasCode"] is True
    assert report["testnetContracts"]["wrappedNative"]["hasCode"] is True
    assert report["testnetContracts"]["multicall3"]["hasCode"] is True
    assert report["mainnetMorphoBlueAddressNegativeControl"]["hasCode"] is False
    assert "no_aave_or_morpho_lending_liquidity_verified_on_testnet" in report["blockingReasons"]


def test_robinhood_testnet_lending_liquidity_requires_nonzero_token_balance(monkeypatch):
    aave_pool = "0x00000000000000000000000000000000000000a1"
    token = "0x00000000000000000000000000000000000000b2"
    monkeypatch.setenv("ROBINHOOD_TESTNET_AAVE_POOL", aave_pool)
    monkeypatch.setenv("ROBINHOOD_TESTNET_BORROW_TOKEN_ADDRESSES", token)
    monkeypatch.delenv("ROBINHOOD_TESTNET_MORPHO_BLUE", raising=False)

    def transport(_url, payload, _timeout):
        method = payload["method"]
        if method == "eth_chainId":
            return {"result": hex(46630)}
        if method == "eth_getCode":
            address = payload["params"][0]
            if address in TESTNET_SUPPORT_CONTRACTS | {aave_pool}:
                return {"result": "0x6001"}
            if address == MAINNET_MORPHO_BLUE_NOT_TESTNET:
                return {"result": "0x"}
            raise AssertionError(address)
        if method == "eth_getBalance":
            return {"result": hex(123)}
        if method == "eth_call":
            return {"result": hex(456)}
        raise AssertionError(method)

    report = summarize_robinhood_testnet_lending_liquidity(transport=transport)

    assert report["hasVerifiedBorrowableLiquidity"] is True
    assert report["supportsAaveOrMorphoFlashLoans"] is True
    assert report["protocols"]["aave_v3"]["codePresentCount"] == 1
    assert report["protocols"]["aave_v3"]["verifiedBorrowableAssetCount"] == 1
    assert "no_aave_or_morpho_lending_liquidity_verified_on_testnet" not in report["blockingReasons"]
