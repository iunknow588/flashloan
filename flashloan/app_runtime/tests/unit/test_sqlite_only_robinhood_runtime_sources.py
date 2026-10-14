"""Guard tests for the SQLite-only Robinhood runtime source contract.

Page reads must resolve official stock anchors from ``chain_tokens`` rows in
the SQLite business store.  JSON stock registries remain build/seed artifacts
and must never be loaded by these runtime paths.
"""

from __future__ import annotations

from typing import Any


def _seed_stock_db(tmp_path, *, include_stock: bool = True) -> str:
    from core.db.storage_business_schema import ensure_business_schema
    from core.db.storage_chain_core import seed_chain_tokens

    db = str(tmp_path / "robinhood.sqlite3")
    ensure_business_schema(db)
    token_rows = [
        {
            "token_address": "0x0000000000000000000000000000000000000000",
            "token_symbol": "ETH",
            "token_decimals": 18,
            "is_native": 1,
        },
        {
            "token_address": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
            "token_symbol": "WETH",
            "token_decimals": 18,
            "is_wrapped_native": 1,
        },
        {
            "token_address": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
            "token_symbol": "USDG",
            "token_decimals": 6,
            "is_stable": 1,
        },
        {
            "token_address": "0xa8cf7c51eee671646ba440ffa180f970902fd2fb",
            "token_symbol": "TZ",
            "token_decimals": 18,
        },
    ]
    if include_stock:
        token_rows.append(
            {
                "token_address": "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9",
                "token_symbol": "AAPL",
                "token_decimals": 18,
                "token_metadata_json": {"category": "robinhood_stock"},
            }
        )
    seed_chain_tokens(
        db,
        network="robinhood",
        chain_id=4663,
        token_rows=token_rows,
    )
    return db


def _fail_json_registry(*args: Any, **kwargs: Any):
    raise AssertionError("JSON stock registry must not be read by runtime paths")


def test_dex_pool_candidates_stock_scope_comes_from_sqlite(tmp_path, monkeypatch):
    import core.db.database_path as database_path
    from dex.intent_trade.chains.robinhood import dex_pool_candidates as candidates

    monkeypatch.setattr(candidates, "_MAINNET_STOCK_TOKEN_BY_ADDRESS", {})
    monkeypatch.setattr(candidates, "_MAINNET_STOCK_TOKENS_LOADED", False)
    monkeypatch.setattr(
        database_path,
        "absolute_database_path",
        lambda database_url=None: _seed_stock_db(tmp_path),
    )
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_common.stock_token_registry.load_stock_token_registry",
        _fail_json_registry,
    )
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_common.stock_token_registry.stock_token_by_address",
        _fail_json_registry,
    )

    stock_by_address = candidates._mainnet_stock_token_by_address()

    assert candidates._MAINNET_STOCK_TOKENS_LOADED is True
    assert "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9" in stock_by_address
    assert stock_by_address["0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"]["symbol"] == "AAPL"
    assert "AAPL" in candidates._mainnet_configured_anchor_symbols()


def test_page_payloads_default_anchor_symbols_come_from_sqlite(tmp_path, monkeypatch):
    from core.db import page_payloads

    db = _seed_stock_db(tmp_path)
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_common.stock_token_registry.load_stock_token_registry",
        _fail_json_registry,
    )
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_common.stock_token_registry.stock_token_by_address",
        _fail_json_registry,
    )

    symbols = page_payloads._default_anchor_symbols(
        4663,
        database_url=db,
    )

    assert "AAPL" in symbols


def test_chain_config_stock_anchor_map_can_be_db_only(tmp_path, monkeypatch):
    from dex.intent_trade.chains.robinhood_common import chain_config

    db = _seed_stock_db(tmp_path)
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_common.stock_token_registry.load_stock_token_registry",
        _fail_json_registry,
    )

    anchors = chain_config.all_anchor_addresses_by_symbol(
        "robinhood",
        database_url=db,
    )

    assert anchors.get("AAPL") == "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"


def test_stock_token_registry_helpers_can_be_db_only(tmp_path, monkeypatch):
    from dex.intent_trade.chains.robinhood_common import stock_token_registry as registry

    db = _seed_stock_db(tmp_path)
    monkeypatch.setattr(registry, "_read_json", _fail_json_registry)

    payload = registry.load_stock_token_registry(
        database_url=db,
        network="robinhood",
        chain_id=4663,
    )
    tokens = payload.get("tokens") or {}
    assert payload.get("source") == "sqlite_db"
    assert tokens["aapl"]["symbol"] == "AAPL"
    assert tokens["aapl"]["address"] == "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"

    by_address = registry.stock_token_by_address(
        database_url=db,
        network="robinhood",
        chain_id=4663,
    )
    assert by_address["0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"]["symbol"] == "AAPL"

    rows = [
        {
            "token_address": "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9",
            "token_symbol": "0XAF3D76F1",
            "token_decimals": None,
        }
    ]
    registry.apply_stock_token_metadata(
        rows,
        database_url=db,
        network="robinhood",
        chain_id=4663,
    )
    assert rows[0]["token_symbol"] == "AAPL"
    assert rows[0]["token_decimals"] == 18
    assert rows[0]["token_metadata"]["category"] == "robinhood_stock"


def test_robinhood_runtime_page_modules_do_not_import_json_stock_registry():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    src_root = root / "src"
    runtime_modules = (
        ("dex/intent_trade/chains/robinhood/flash_loan_pool_views.py", src_root),
        ("dex/intent_trade/chains/robinhood/v4_quote_adapter.py", src_root),
        ("dex/intent_trade/chains/robinhood_testnet/v4_quote_adapter.py", src_root),
        ("dex/intent_trade/chains/robinhood/dex_pool_candidates.py", src_root),
        ("core/db/page_payloads.py", src_root),
        ("market/binance_market/dex_surfaces.py", src_root),
        ("web/flash_loan_pools.py", root / "app_runtime"),
        ("web/control_panel_page_routes.py", root / "app_runtime"),
    )
    forbidden = (
        "stock_token_registry.load_stock_token_registry",
        "stock_token_registry.stock_token_by_address",
    )
    offenders = []
    for relative, module_root in runtime_modules:
        source = (module_root / relative).read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                offenders.append(f"{relative} contains {token}")
    assert offenders == []


def test_mainnet_observation_runtime_path_never_uses_legacy_enumeration():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    src_root = root / "src"
    source = (
        src_root
        / "dex"
        / "intent_trade"
        / "chains"
        / "robinhood"
        / "market_observation.py"
    ).read_text(encoding="utf-8")

    fund_start = source.index("def _mainnet_fund_anchor_observation(")
    fund_end = source.index("def fund_anchor_pair_observation(")
    fund_body = source[fund_start:fund_end]
    assert "_mainnet_observation_sequences(" not in fund_body

    legacy_start = source.index("def _mainnet_observation_sequences(")
    legacy_end = source.index("def _mainnet_bridge_pairs(")
    legacy_body = source[legacy_start:legacy_end]
    assert "return _mainnet_closed_loop_sequences(), []" in legacy_body
    assert "pools_by_edge = _pools_by_edge(pools)" not in legacy_body
