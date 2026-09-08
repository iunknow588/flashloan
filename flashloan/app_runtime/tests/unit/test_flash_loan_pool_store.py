import json

import pytest

from core.flash_loan_pools import (
    load_chain_flash_loan_pool_static,
    load_flash_loan_pool_static,
    write_flash_loan_pool_static,
)
from core.flash_loan_pool_store import pool_evidence_from_view
from core.pool_evidence_contract import POOL_EVIDENCE_FIELDS, validate_pool_evidence_contract


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
    assert stored["pool_evidence_schema_version"] == "flash-loan-pool-evidence-v1"
    assert stored["pool_evidence"]["executability"]["allowed"] is False
    assert "execution_enabled_false_or_missing" in stored["pool_evidence"]["executability"]["blocked_reasons"]
    assert stored["pool_evidence"]["profitability"]["status"] == "requires_live_quoter"
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


def test_pool_evidence_fails_closed_without_liquidity_market_targets_or_execution(tmp_path):
    evidence = pool_evidence_from_view(_view([]))

    assert evidence["liquidity"]["allowed"] is False
    assert evidence["marketability"]["allowed"] is False
    assert evidence["profitability"]["allowed"] is False
    assert evidence["executability"]["allowed"] is False


def test_pool_evidence_marks_executable_only_when_capability_explicitly_enabled():
    view = _view([
        {
            "token_symbol": "USDC",
            "currency_address": "0x0000000000000000000000000000000000000000",
            "available_liquidity": 1000,
            "depth_score_usd": 2000,
        }
    ])
    view["summary"]["borrow_target_count"] = 1
    view["cache"]["capabilities"] = {
        "concrete_pool_count": 1,
        "execution_enabled": True,
    }

    evidence = pool_evidence_from_view(view)

    assert evidence["liquidity"]["allowed"] is True
    assert evidence["marketability"]["allowed"] is True
    assert evidence["executability"]["allowed"] is True


def test_flash_loan_pool_store_rejects_wrong_scope_on_write_and_load(tmp_path):
    scope = {
        "market_id": "dex-arbitrage-56-uniswap-v4",
        "chain_id": 56,
        "network": "bnb",
        "protocol": "uniswap_v4",
    }
    view = _view(
        [{"token_symbol": "USDC", "currency_address": "0x0000000000000000000000000000000000000000"}],
        protocol="uniswap_v4",
    )

    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        write_flash_loan_pool_static(
            view,
            chain_id=46630,
            expected_scope=scope,
            root=tmp_path,
        )

    stored = write_flash_loan_pool_static(
        view,
        chain_id=46630,
        expected_scope={
            "market_id": "dex-arbitrage-46630-uniswap-v4",
            "chain_id": 46630,
            "network": "robinhood-testnet",
            "protocol": "uniswap_v4",
        },
        root=tmp_path,
        refresh_mode="full",
    )
    assert stored["pool_evidence"]["liquidity"]["allowed"] is False

    with pytest.raises(ValueError, match="scope mismatch"):
        load_flash_loan_pool_static(
            chain_id=46630,
            protocol="uniswap_v4",
            root=tmp_path,
            expected_scope=scope,
        )


def test_flash_loan_pool_store_evidence_contract_is_stable_and_covers_all_fields(tmp_path):
    view = _view(
        [{"token_symbol": "USDC", "currency_address": "0x0000000000000000000000000000000000000000", "available_liquidity": 1000}]
    )
    view["summary"]["borrow_target_count"] = 1
    view["cache"]["capabilities"] = {"execution_enabled": False}

    stored = write_flash_loan_pool_static(
        view,
        chain_id=46630,
        root=tmp_path,
        refresh_mode="full",
    )
    evidence = stored["pool_evidence"]

    assert set(evidence) == set(POOL_EVIDENCE_FIELDS)
    assert validate_pool_evidence_contract(evidence) == (True, ())
    assert stored["pool_evidence_schema_version"] == "flash-loan-pool-evidence-v1"
    assert stored["pool_evidence_observed_at"] == stored["generated_at"]
