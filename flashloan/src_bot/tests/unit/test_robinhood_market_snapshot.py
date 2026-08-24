from core.robinhood_market_snapshot import model_costs


def test_model_costs_keeps_net_profit_unverified_when_inputs_are_incomplete():
    result = model_costs(
        gross_profit_usdg_raw=100,
        amount_in_usdg_raw=1000,
        gas_price_wei=20_000_000,
        gas_units=100_000,
        weth_usdg_raw_per_weth=2_400_000_000,
    )

    assert result["flashloanFeeUsdgRaw"] is None
    assert result["gasCostUsdgRaw"] == 4800
    assert result["netProfitUsdgRaw"] is None
    assert result["netProfitModelStatus"] == "incomplete"


def test_model_costs_subtracts_flashloan_gas_and_buffer_when_all_inputs_are_verified():
    result = model_costs(
        gross_profit_usdg_raw=1000,
        amount_in_usdg_raw=100_000,
        gas_price_wei=20_000_000,
        gas_units=100_000,
        weth_usdg_raw_per_weth=2_400_000_000,
        flashloan_fee_bps=5,
        mev_buffer_usdg_raw=10,
    )

    assert result["flashloanFeeUsdgRaw"] == 50
    assert result["gasCostUsdgRaw"] == 4800
    assert result["netProfitUsdgRaw"] == -3860
    assert result["netProfitModelStatus"] == "complete"
