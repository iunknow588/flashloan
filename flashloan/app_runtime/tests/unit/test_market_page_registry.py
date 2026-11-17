from core.market_page_registry import resolve_market_page_variant


CANONICAL_ROBINHOOD_BOT = "liquidation-4663-aave-v3"
CANONICAL_ROBINHOOD_DEX = "dex-arbitrage-4663-uniswap-v4"
LEGACY_ROBINHOOD_DEX = "robinhood-dex-shadow"


def test_bot_page_defaults_to_the_registry_liquidation_scope():
    variant = resolve_market_page_variant("/bot")

    assert variant is not None
    assert variant.market_id == CANONICAL_ROBINHOOD_BOT
    assert variant.chain_id == 4663


def test_generic_dex_page_defaults_to_registry_dex_scope():
    variant = resolve_market_page_variant("/dex")

    assert variant is not None
    assert variant.market_id == CANONICAL_ROBINHOOD_DEX
    assert variant.chain_id == 4663


def test_robinhood_dex_page_keeps_robinhood_shadow_default():
    variant = resolve_market_page_variant("/robinhood-dex")

    assert variant is not None
    assert variant.market_id == CANONICAL_ROBINHOOD_DEX
    assert variant.selection.legacy_market_id == LEGACY_ROBINHOOD_DEX
    assert variant.chain_id == 4663
