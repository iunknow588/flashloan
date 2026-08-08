from db.storage import EXPECTED_SCHEMA_MIGRATION_IDS, record_schema_migrations
from db.storage_schema import ensure_database_schema
import db.storage_schema as storage_schema
from web import control_panel_data


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))


def test_record_schema_migrations_is_idempotent():
    cursor = FakeCursor()

    record_schema_migrations(cursor)

    assert len(cursor.calls) == len(EXPECTED_SCHEMA_MIGRATION_IDS)
    assert "20260803_liquidation_market_namespace" in EXPECTED_SCHEMA_MIGRATION_IDS
    assert "20260804_cow_supported_tokens" in EXPECTED_SCHEMA_MIGRATION_IDS
    assert "20260805_cow_execution_attempts" in EXPECTED_SCHEMA_MIGRATION_IDS
    query, params = cursor.calls[0]
    assert "ON CONFLICT (migration_id) DO NOTHING" in query
    assert params[0] == "20260730_liquidation_runtime_schema"


def test_database_table_counts_includes_liquidation_and_schema_status(monkeypatch):
    captured = {}

    def fake_fetch_one(database_url, query, params=()):
        captured["database_url"] = database_url
        captured["query"] = query
        captured["params"] = params
        migration_count = len(EXPECTED_SCHEMA_MIGRATION_IDS)
        return (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 10, migration_count, "2026-07-30 00:00:00+00", migration_count)

    monkeypatch.setattr(control_panel_data, "fetch_one", fake_fetch_one)

    counts = control_panel_data.database_table_counts("postgresql://example")

    assert counts["liquidation_accounts"] == 7
    assert counts["liquidation_discovery_scans"] == 8
    assert counts["liquidation_account_health_scans"] == 9
    assert counts["liquidation_borrow_health_pool"] == 11
    assert counts["liquidation_high_frequency_pool"] == 12
    assert counts["liquidation_core_opportunity_pool"] == 13
    assert counts["liquidation_borrow_health_scan_batches"] == 14
    assert counts["liquidation_scan_config_library"] == 15
    assert counts["liquidation_failure_samples"] == 10
    assert counts["schema"]["up_to_date"] is True
    assert counts["total"] == 120 + len(EXPECTED_SCHEMA_MIGRATION_IDS)
    assert captured["params"] == (list(EXPECTED_SCHEMA_MIGRATION_IDS),)


class FakeSchemaCursor:
    def __init__(self, lock_acquired):
        self.lock_acquired = lock_acquired
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchone(self):
        return (self.lock_acquired,)


class FakeSchemaConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self.cursor_instance


def fake_schema_connection(cursor):
    def factory(database_url, connect_timeout=8):
        return FakeSchemaConnection(cursor)

    return factory


def test_ensure_database_schema_skips_when_advisory_lock_is_busy(monkeypatch):
    cursor = FakeSchemaCursor(lock_acquired=False)
    monkeypatch.setattr(storage_schema, "require_psycopg", lambda: object())
    monkeypatch.setattr(storage_schema, "db_connection", fake_schema_connection(cursor))

    ensure_database_schema("postgresql://example")

    queries = [query for query, _params in cursor.calls]
    assert len(queries) == 1
    assert "pg_try_advisory_lock" in queries[0]
    assert all("CREATE TABLE" not in query for query in queries)
    assert all("pg_advisory_unlock" not in query for query in queries)


def test_ensure_database_schema_unlocks_after_success(monkeypatch):
    cursor = FakeSchemaCursor(lock_acquired=True)
    monkeypatch.setattr(storage_schema, "require_psycopg", lambda: object())
    monkeypatch.setattr(storage_schema, "db_connection", fake_schema_connection(cursor))

    ensure_database_schema("postgresql://example")

    queries = [query for query, _params in cursor.calls]
    assert "pg_try_advisory_lock" in queries[0]
    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in query for query in queries)
    assert any("ADD COLUMN IF NOT EXISTS market_id" in query for query in queries)
    assert any("ADD COLUMN IF NOT EXISTS chain_id" in query for query in queries)
    assert any("idx_liq_core_mkt" in query for query in queries)
    assert any("CREATE TABLE IF NOT EXISTS cow_supported_tokens" in query for query in queries)
    assert any("idx_cow_supported_tokens_network_symbol" in query for query in queries)
    assert any("CREATE TABLE IF NOT EXISTS cow_execution_attempts" in query for query in queries)
    assert any("idx_cow_execution_attempts_network_time" in query for query in queries)
    assert any("control_mode TEXT" in query for query in queries)
    assert any("route_hop_constraints_enforced BOOLEAN" in query for query in queries)
    assert any("cow_flashloan_intent_json TEXT" in query for query in queries)
    assert any("cow_sdk_result_json TEXT" in query for query in queries)
    assert "pg_advisory_unlock" in queries[-1]
