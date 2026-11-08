"""Unit tests for DEX surface payload placeholder masking."""

from __future__ import annotations

from web.data_routes.dex_surface_response import (
    mask_dex_surface_placeholder_symbols,
)


def test_masks_pool_key_token_symbols_with_sibling_address():
    payload = {
        "poolKey": {
            "token0": "0x" + "0" * 40,
            "token0Symbol": "0X33E419",
            "token1": "0x" + "1" * 40,
            "token1Symbol": "0x8f330543",
        }
    }
    masked = mask_dex_surface_placeholder_symbols(payload)
    assert masked["poolKey"]["token0Symbol"] == "UNRESOLVED:0x00000000"
    assert masked["poolKey"]["token1Symbol"] == "UNRESOLVED:0x11111111"


def test_masks_currency_metadata_symbol_with_own_address():
    payload = {
        "currency0Metadata": {
            "address": "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E",
            "symbol": "0x05a31b34",
        }
    }
    masked = mask_dex_surface_placeholder_symbols(payload)
    assert masked["currency0Metadata"]["symbol"] == "UNRESOLVED:0x05a31b34"


def test_masks_fallback_and_raw_symbols_for_residual_addresses():
    payload = {
        "sharedTokens": [
            {
                "address": "0x450d9bc42be9a45269678669a786d2c3c5221bdf",
                "rawSymbol": "0X450D9BC4",
                "nameFallbackSymbol": "0X450D9BC4",
            }
        ]
    }
    masked = mask_dex_surface_placeholder_symbols(payload)
    token = masked["sharedTokens"][0]
    assert token["rawSymbol"] == "UNRESOLVED:0x450d9bc4"
    assert token["nameFallbackSymbol"] == "UNRESOLVED:0x450d9bc4"


def test_keeps_real_symbols_and_unrelated_values():
    payload = {
        "anchorSymbol": "TZ",
        "counterName": "AMZN Token",
        "symbol": "ETH",
        "poolKey": {"token0Symbol": "AMZN", "token1Symbol": "TB"},
        "count": 7,
        "flags": [True, False],
    }
    assert mask_dex_surface_placeholder_symbols(payload) == payload


def test_missing_sibling_address_falls_back_to_unknown_label():
    payload = {"symbol": "0X123456"}
    masked = mask_dex_surface_placeholder_symbols(payload)
    assert masked["symbol"] == "UNRESOLVED:unknown"
