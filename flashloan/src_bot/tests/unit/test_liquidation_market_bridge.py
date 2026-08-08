from datetime import datetime, timedelta, timezone
import json

from runtime.liquidation_market_bridge import (
    asset_variants_for_market_symbols,
    binance_symbols_for_liquidation_assets,
    liquidation_asset_ids_from_pool_rows,
    price_snapshot_from_extremes,
)


def test_price_snapshot_from_extremes_uses_fresh_market_rows():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)

    snapshot = price_snapshot_from_extremes(
        {
            "observed_at": now.isoformat(),
            "basket": [{"symbol": "AVAXUSDT", "current_price": 6.5}],
            "top": [{"symbol": "BTCUSDT", "end_price": 63000}],
        },
        now=now + timedelta(seconds=10),
    )

    assert snapshot == {"AVAXUSDT": 6.5, "BTCUSDT": 63000.0}


def test_price_snapshot_from_extremes_rejects_stale_market_rows():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)

    snapshot = price_snapshot_from_extremes(
        {"observed_at": (now - timedelta(seconds=180)).isoformat(), "basket": [{"symbol": "AVAXUSDT", "current_price": 6.5}]},
        max_age_seconds=120,
        now=now,
    )

    assert snapshot == {}


def test_asset_variants_for_market_symbols_uses_reserve_cache(tmp_path):
    cache = tmp_path / "aave_reserve_assets.json"
    cache.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "token_symbol": "WAVAX",
                        "binance_symbol": "AVAXUSDT",
                        "token_address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    variants = asset_variants_for_market_symbols(["AVAXUSDT"], reserve_cache_path=cache)

    assert "AVAXUSDT" in variants
    assert "AVAX" in variants
    assert "WAVAX" in variants
    assert "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7" in variants


def test_liquidation_asset_ids_from_pool_rows_extracts_debt_and_collateral_assets():
    rows = [
        {
            "best_debt_asset": "USDC",
            "best_collateral_asset": "WAVAX",
            "report": {
                "positions": [
                    {"symbol": "SAVAXUSDT", "token_symbol": "SAVAX", "collateral_value_base": 100},
                    {"symbol": "LINKUSDT", "token_symbol": "LINK", "debt_value_base": 0},
                ]
            },
        }
    ]

    assets = liquidation_asset_ids_from_pool_rows(rows)

    assert assets == ["USDC", "WAVAX", "SAVAXUSDT"]


def test_binance_symbols_for_liquidation_assets_prefers_reserve_mapping():
    symbols = binance_symbols_for_liquidation_assets(
        ["WAVAX", "0x2b2C81e08f1Af8835a78Bb2A90AE924ACE0eA4bE", "USDC"],
        [
            {"token_symbol": "WAVAX", "binance_symbol": "AVAXUSDT", "token_address": "0xavax"},
            {
                "token_symbol": "SAVAX",
                "binance_symbol": "SAVAXUSDT",
                "token_address": "0x2b2C81e08f1Af8835a78Bb2A90AE924ACE0eA4bE",
            },
            {"token_symbol": "USDC", "binance_symbol": "USDCUSDT", "token_address": "0xusdc"},
        ],
    )

    assert symbols == ["AVAXUSDT", "SAVAXUSDT", "USDCUSDT"]
