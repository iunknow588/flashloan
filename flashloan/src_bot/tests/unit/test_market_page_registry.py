from core.market_config import (
    DEFAULT_AVALANCHE_CHAIN_ID,
    DEFAULT_AVALANCHE_DEX_MARKET_ID,
    DEFAULT_ROBINHOOD_TESTNET_BOT_MARKET_ID,
    DEFAULT_ROBINHOOD_TESTNET_CHAIN_ID,
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
)
from web.market_page_registry import resolve_market_page_variant


def test_bot_page_defaults_to_robinhood_testnet():
    variant = resolve_market_page_variant("/bot")

    assert variant is not None
    assert variant.market_id == DEFAULT_ROBINHOOD_TESTNET_BOT_MARKET_ID
    assert variant.chain_id == DEFAULT_ROBINHOOD_TESTNET_CHAIN_ID


def test_generic_dex_page_defaults_to_avalanche():
    variant = resolve_market_page_variant("/dex")

    assert variant is not None
    assert variant.market_id == DEFAULT_AVALANCHE_DEX_MARKET_ID
    assert variant.chain_id == DEFAULT_AVALANCHE_CHAIN_ID


def test_robinhood_dex_page_keeps_robinhood_shadow_default():
    variant = resolve_market_page_variant("/robinhood-dex")

    assert variant is not None
    assert variant.market_id == DEFAULT_ROBINHOOD_DEX_MARKET_ID
    assert variant.chain_id == DEFAULT_ROBINHOOD_CHAIN_ID
