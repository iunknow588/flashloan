from core.db.storage import EXPECTED_SCHEMA_MIGRATION_IDS
from core.db.storage_schema import ensure_database_schema, load_schema_migrations, record_schema_migrations
import core.db.storage_schema as storage_schema
from web import control_panel_data


def test_record_schema_migrations_is_idempotent():
    record_schema_migrations(None)

    assert len(EXPECTED_SCHEMA_MIGRATION_IDS) > 0
    assert "20260803_liquidation_market_namespace" in EXPECTED_SCHEMA_MIGRATION_IDS
    assert "20260804_cow_supported_tokens" in EXPECTED_SCHEMA_MIGRATION_IDS
    assert "20260805_cow_execution_attempts" in EXPECTED_SCHEMA_MIGRATION_IDS
    assert "20260818_chain_market_registry" in EXPECTED_SCHEMA_MIGRATION_IDS


def test_database_table_counts_includes_liquidation_and_schema_status(monkeypatch, tmp_path):
    kv_path = str(tmp_path / "counts.sqlite3")
    monkeypatch.setenv("FLASHLOAN_KV_PATH", kv_path)
    ensure_database_schema(kv_path)

    counts = control_panel_data.database_table_counts(kv_path)

    for name in (
        "observations",
        "binance_price_history",
        "binance_candidate_price_history",
        "binance_pair_price_history",
        "binance_window_extremes",
        "arbitrage_simulations",
        "liquidation_accounts",
        "liquidation_discovery_scans",
        "liquidation_account_health_scans",
        "liquidation_borrow_health_pool",
        "liquidation_high_frequency_pool",
        "liquidation_core_opportunity_pool",
        "liquidation_borrow_health_scan_batches",
        "liquidation_scan_config_library",
        "liquidation_failure_samples",
    ):
        assert counts[name] == 0
    assert counts["schema"]["up_to_date"] is True
    assert counts["total"] == 0


def test_ensure_database_schema_records_local_migrations(monkeypatch, tmp_path):
    kv_path = str(tmp_path / "schema.sqlite3")
    monkeypatch.setenv("FLASHLOAN_KV_PATH", kv_path)

    ensure_database_schema(kv_path)
    migrations = load_schema_migrations(kv_path)

    assert {row["migration_id"] for row in migrations} == set(EXPECTED_SCHEMA_MIGRATION_IDS)
