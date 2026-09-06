import json

from core.history_migration import migrate_json_file, migrate_jsonl_file, read_jsonl_records
from core.schema_migration import migration_fixture_v1_to_v2


MIGRATIONS = (migration_fixture_v1_to_v2(),)


def test_migrate_jsonl_file_migrates_and_keeps_backup_when_inplace(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text(json.dumps({"schema_version": 1, "market": "m"}) + "\n", encoding="utf-8")

    output = migrate_jsonl_file(path, migrations=MIGRATIONS, target_version=2)

    assert output == path
    assert read_jsonl_records(path) == [{"schema_version": 2, "market_id": "m"}]
    backups = list(tmp_path.glob("*.bak.jsonl"))
    assert len(backups) == 1


def test_migrate_json_file_round_trips_to_output_path_without_touching_source(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema_version": 1, "market": "old"}), encoding="utf-8")
    output = tmp_path / "migrated.json"

    migrate_json_file(path, migrations=MIGRATIONS, target_version=2, output_path=output, backup=False)

    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": 1, "market": "old"}
    assert json.loads(output.read_text(encoding="utf-8")) == {"schema_version": 2, "market_id": "old"}
