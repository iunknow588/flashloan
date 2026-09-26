from core.db.storage_liquidation_pool import (
    _core_opportunity_viable,
    load_liquidation_borrow_health_pool,
    load_liquidation_core_opportunity_pool,
    sync_liquidation_borrow_health_pool,
)


def _row(net_profit, *, debt=1000, health_factor=0.98, account="0x0000000000000000000000000000000000000001"):
    return {
        "account": account,
        "health_factor": health_factor,
        "total_debt_base": debt,
        "recommended_candidate": {
            "debt_symbol": "USDC",
            "collateral_symbol": "WAVAX",
            "estimated_profit": {
                "operator_net_profit_usd": net_profit,
                "net_profit_base": net_profit,
            },
        },
    }


def test_core_opportunity_viable_requires_profit_above_minimum():
    assert _core_opportunity_viable(_row(1.01), min_operator_net_profit_usd=1.0) is True
    assert _core_opportunity_viable(_row(1.0), min_operator_net_profit_usd=1.0) is True
    assert _core_opportunity_viable(_row(0.99), min_operator_net_profit_usd=1.0) is True
    assert _core_opportunity_viable(_row(0.0), min_operator_net_profit_usd=1.0) is False


def test_core_opportunity_requires_positive_debt():
    assert _core_opportunity_viable(_row(10, debt=0), min_operator_net_profit_usd=1.0) is False


def test_sync_core_pool_stores_rows_locally(tmp_path):
    import core.db.storage_liquidation_pool as pool_storage

    kv_path = str(tmp_path / "pool.sqlite3")
    result = sync_liquidation_borrow_health_pool(
        kv_path,
        [
            _row(0.75),
            _row(1.25, health_factor=0.97, account="0x0000000000000000000000000000000000000002"),
        ],
        min_operator_net_profit_usd=1.0,
    )

    assert result["core_count"] == 2
    rows = load_liquidation_core_opportunity_pool(kv_path, limit=10)
    assert {row["account"] for row in rows} == {
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    }
    assert pool_storage.load_liquidation_pool_counts(kv_path)["core_opportunity_count"] == 2


def test_load_borrow_health_pool_returns_stored_rows(tmp_path):
    from core.kv_rows import upsert_row

    kv_path = str(tmp_path / "borrow.sqlite3")
    upsert_row(
        kv_path,
        "liquidation_borrow_health_pool",
        "default|0|0xabc",
        {
            "account": "0xabc",
            "health_factor": 1.2,
            "active": True,
            "report_json": {"nested": {"value": 1}},
        },
    )

    rows = load_liquidation_borrow_health_pool(kv_path, limit=20)

    assert rows[0]["account"] == "0xabc"
    assert rows[0]["report_json"]["nested"]["value"] == 1
