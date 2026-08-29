import json

from core.flash_loan_pools import (
    load_chain_flash_loan_pool_static,
    load_flash_loan_pool_static,
    write_flash_loan_pool_static,
)


def _view(rows, *, protocol="uniswap_v4"):
    return {
        "protocol": protocol,
        "display_name": "Uniswap V4",
        "cache": {"chain_id": 46630, "network": "robinhood-testnet", "source": "test"},
        "summary": {"borrow_asset_count": len(rows)},
        "non_stable_v4_flash_source_rows": rows,
        "borrow_target_universe": None,
    }


def test_flash_loan_pool_store_writes_chain_protocol_static_table(tmp_path):
    stored = write_flash_loan_pool_static(
        _view([{"token_symbol": "ETH", "currency_address": "0x0000000000000000000000000000000000000000"}]),
        chain_id=46630,
        refresh_mode="full",
        root=tmp_path,
    )

    loaded = load_flash_loan_pool_static(chain_id=46630, protocol="uniswap_v4", root=tmp_path)
    chain = load_chain_flash_loan_pool_static(chain_id=46630, root=tmp_path)

    assert stored["exists"] is True
    assert stored["network"] == "robinhood-testnet"
    assert stored["chain_id"] == 46630
    assert stored["protocol"] == "uniswap_v4"
    assert stored["section_counts"]["non_stable_v4_flash_source_rows"] == 1
    assert loaded["sections"]["non_stable_v4_flash_source_rows"][0]["token_symbol"] == "ETH"
    assert chain["protocol_count"] == 1
    assert chain["protocols"][0]["protocol"] == "uniswap_v4"
    assert json.loads((tmp_path / "robinhood-testnet" / "46630" / "uniswap_v4" / "pools.json").read_text(encoding="utf-8"))["schema_version"]


def test_flash_loan_pool_store_incremental_merge_keeps_existing_rows(tmp_path):
    write_flash_loan_pool_static(
        _view([{"token_symbol": "ETH", "currency_address": "0x0000000000000000000000000000000000000000", "available_liquidity": 1}]),
        chain_id=46630,
        refresh_mode="full",
        root=tmp_path,
    )
    write_flash_loan_pool_static(
        _view([{"token_symbol": "WETH", "currency_address": "0x" + "1" * 40, "available_liquidity": 2}]),
        chain_id=46630,
        refresh_mode="incremental",
        root=tmp_path,
    )

    loaded = load_flash_loan_pool_static(chain_id=46630, protocol="uniswap_v4", root=tmp_path)
    rows = loaded["sections"]["non_stable_v4_flash_source_rows"]

    assert loaded["refresh_mode"] == "incremental"
    assert [row["token_symbol"] for row in rows] == ["ETH", "WETH"]
    assert loaded["section_counts"]["non_stable_v4_flash_source_rows"] == 2
