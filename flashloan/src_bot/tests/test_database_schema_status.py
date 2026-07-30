from db.storage import EXPECTED_SCHEMA_MIGRATION_IDS, record_schema_migrations
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
        return (1, 2, 3, 4, 5, 6, 7, 8, 9, migration_count, "2026-07-30 00:00:00+00", migration_count)

    monkeypatch.setattr(control_panel_data, "fetch_one", fake_fetch_one)

    counts = control_panel_data.database_table_counts("postgresql://example")

    assert counts["liquidation_accounts"] == 7
    assert counts["liquidation_discovery_scans"] == 8
    assert counts["liquidation_account_health_scans"] == 9
    assert counts["schema"]["up_to_date"] is True
    assert counts["total"] == 45 + len(EXPECTED_SCHEMA_MIGRATION_IDS)
    assert captured["params"] == (list(EXPECTED_SCHEMA_MIGRATION_IDS),)
