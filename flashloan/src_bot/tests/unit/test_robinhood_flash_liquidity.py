from intent_trade.chains.robinhood import flash_liquidity as liquidity


def test_robinhood_flash_liquidity_snapshot_models_morpho_and_uniswap_v2(monkeypatch):
    balances = {
        liquidity.USDG.lower(): 41_660_722_032807,
        liquidity.WETH.lower(): 17_349_175_052_060_704,
    }

    monkeypatch.setattr(
        liquidity,
        "_erc20_balance",
        lambda *, token, **_kwargs: balances[token.lower()],
    )
    monkeypatch.setattr(
        liquidity,
        "_uniswap_v2_pair",
        lambda **_kwargs: "0x8803c117ccae7b5146297876c2a25df135141c4d",
    )
    monkeypatch.setattr(
        liquidity,
        "_pair_token",
        lambda *, selector, **_kwargs: liquidity.WETH if selector == liquidity.TOKEN0_SELECTOR else liquidity.USDG,
    )
    monkeypatch.setattr(
        liquidity,
        "_pair_reserves",
        lambda **_kwargs: (45_365_972_644_790_324_833, 110_985_201_820),
    )

    snapshot = liquidity.load_robinhood_flash_liquidity_snapshot(rpc_url="https://rpc.example")

    assert snapshot["ok"] is True
    assert snapshot["errors"] == []
    assert [source["protocol"] for source in snapshot["sources"]] == [
        "morpho_blue",
        "morpho_blue",
        "uniswap_v2",
    ]
    morpho_usdg = snapshot["sources"][0]
    assert morpho_usdg["source_kind"] == "flash_loan"
    assert morpho_usdg["token_symbol"] == "USDG"
    assert morpho_usdg["available_liquidity"] == 41_660_722.032807
    assert morpho_usdg["fee_bps"] == 0
    assert morpho_usdg["flash_borrowable"] is True
    v2 = snapshot["sources"][2]
    assert v2["source_kind"] == "flash_swap"
    assert v2["token_symbol"] == "WETH:USDG"
    assert v2["reserve0"] == 45.36597264479032
    assert v2["reserve1"] == 110_985.20182
    assert v2["fee_bps"] == 30
    assert v2["flash_borrowable"] is True
