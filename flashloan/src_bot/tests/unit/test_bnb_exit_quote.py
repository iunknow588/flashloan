from execution.bnb_exit_quote import build_bnb_exit_paths, quote_bnb_exit_paths, quote_pancake_v3_path
from intent_trade.chains.bnb import config as bnb


class FakeCall:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class FakeRouterFunctions:
    def getAmountsOut(self, amount_in, path):
        return FakeCall([amount_in, amount_in + len(path)])


class FakeRouter:
    functions = FakeRouterFunctions()


class FakeQuoterFunctions:
    def quoteExactInputSingle(self, params):
        return FakeCall((params["amountIn"] + 100, 1, 2, 3))


class FakeQuoter:
    functions = FakeQuoterFunctions()


class FakeEth:
    block_number = 456

    def contract(self, address, abi):
        if any(item.get("name") == "quoteExactInputSingle" for item in abi):
            return FakeQuoter()
        return FakeRouter()


class FakeW3:
    eth = FakeEth()


def test_build_bnb_exit_paths_includes_direct_and_core_intermediates():
    paths = build_bnb_exit_paths(bnb.TOKENS["BTCB"].address, bnb.TOKENS["USDT"].address)
    symbols_by_address = {token.address.lower(): symbol for symbol, token in bnb.TOKENS.items()}
    rendered = [[symbols_by_address.get(item.lower(), item) for item in path] for path in paths]

    assert ["BTCB", "USDT"] in rendered
    assert ["BTCB", "WBNB", "USDT"] in rendered
    assert ["BTCB", "USDC", "USDT"] in rendered


def test_quote_bnb_exit_paths_selects_best_pancake_v2_quote():
    report = quote_bnb_exit_paths(
        FakeW3(),
        collateral_asset=bnb.TOKENS["BTCB"].address,
        debt_asset=bnb.TOKENS["USDT"].address,
        amount_in_raw=100,
    )

    assert report["quote_block"] == 456
    assert report["best_quote"]["ok"] is True
    assert report["best_quote"]["amount_out_raw"] > 100
    assert report["quote_at"].endswith("Z")
    assert any(row["dex"] == "pancakeswap_v3" for row in report["quotes"])


def test_quote_pancake_v3_path_has_unified_fee_fields():
    quote = quote_pancake_v3_path(
        FakeW3(),
        "0x0000000000000000000000000000000000000099",
        100,
        [bnb.TOKENS["BTCB"].address, bnb.TOKENS["USDT"].address],
        fee_tier=500,
    )

    assert quote["dex"] == "pancakeswap_v3"
    assert quote["fee_tier"] == 500
    assert quote["fee_bps"] == 5.0
    assert quote["reverse_quote_ok"] is True
