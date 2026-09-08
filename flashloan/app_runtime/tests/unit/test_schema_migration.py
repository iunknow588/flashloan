import pytest

from core.schema_migration import migrate_record, migration_fixture_v1_to_v2


def test_schema_migration_round_trips_and_preserves_version():
    migration = (migration_fixture_v1_to_v2(),)
    source = {"schema_version": 1, "market": "m", "value": 3}
    upgraded = migrate_record(source, target_version=2, migrations=migration)
    assert upgraded == {"schema_version": 2, "market_id": "m", "value": 3}
    assert migrate_record(upgraded, target_version=1, migrations=migration) == source


def test_schema_migration_rejects_missing_path():
    with pytest.raises(ValueError):
        migrate_record({"schema_version": 3}, target_version=1, migrations=(migration_fixture_v1_to_v2(),))
