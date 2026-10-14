"""Candidate-scope coverage for official Robinhood stock tokens."""

from __future__ import annotations

from dex.intent_trade.chains.robinhood.dex_pool_candidates import (
    _known_v4_token_metadata,
    _mainnet_pair_in_candidate_scope,
    _mainnet_stock_token_addresses,
    _v4_symbol,
)


AAPL = "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"
TSLA = "0x322f0929c4625ed5bad873c95208d54e1c003b2d"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"


def test_stock_token_universe_is_loaded():
    addresses = _mainnet_stock_token_addresses()
    assert "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9" in addresses


def test_stock_token_metadata_resolves_official_symbol_and_decimals():
    assert _known_v4_token_metadata(AAPL) == ("AAPL", 18)
    assert _known_v4_token_metadata(TSLA) == ("TSLA", 18)


def test_anchor_stock_pair_is_in_candidate_scope():
    assert _mainnet_pair_in_candidate_scope(AAPL, USDG)
    assert _mainnet_pair_in_candidate_scope(USDG, AAPL)
    # Official stock tokens are first-class anchors, so stock-stock pairs are
    # also part of the candidate scope.
    assert _mainnet_pair_in_candidate_scope(AAPL, TSLA)
    assert _mainnet_pair_in_candidate_scope(TSLA, AAPL)


def test_v4_symbol_prefers_stock_registry_over_placeholder():
    row = {
        "token0": AAPL,
        "token1": USDG,
        "token0Symbol": "0XAF3D76F1",
        "token1Symbol": "USDG",
    }
    assert _v4_symbol(row, 0) == "AAPL"
    assert _v4_symbol(row, 1) == "USDG"
