from execution.venus_abis import COMPTROLLER_ABI, VENUS_CORE_COMPTROLLER_ADDRESS, VENUS_EVENT_SPECS, VTOKEN_ABI


def _names(abi):
    return {item.get("name") for item in abi if item.get("type") == "function"}


def test_venus_core_addresses_and_abi_include_required_read_methods():
    assert VENUS_CORE_COMPTROLLER_ADDRESS == "0xfD36E2c2a6789Db23113685031d7F16329158384"
    assert {"getAllMarkets", "markets", "getAccountLiquidity", "liquidateCalculateSeizeTokens"} <= _names(COMPTROLLER_ABI)
    assert {"symbol", "underlying", "borrowBalanceStored", "balanceOf"} <= _names(VTOKEN_ABI)


def test_venus_event_specs_cover_borrower_registry_events():
    assert {"Borrow", "RepayBorrow", "Mint", "Redeem", "LiquidateBorrow"} <= set(VENUS_EVENT_SPECS)
    assert VENUS_EVENT_SPECS["Borrow"]["topic"].startswith("0x")
    assert VENUS_EVENT_SPECS["RepayBorrow"]["account_index"] == 1
