"""File-oriented history migration and rollback helpers.

The helpers never mutate source files without first creating a timestamped
backup and leaving the migrated result at an explicit output path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.schema_migration import SchemaMigration, migrate_record


def history_backup_path(path: str | Path) -> Path:
    target = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return target.with_name(f"{target.stem}.{stamp}.bak{target.suffix}")


def read_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    target = Path(path)
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"non-object JSONL at line {line_number}")
        records.append(record)
    return records


def write_jsonl_records(path: str | Path, records: Iterable[Mapping[str, Any]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(dict(record), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
    return target


def migrate_jsonl_file(
    path: str | Path,
    *,
    migrations: tuple[SchemaMigration, ...],
    target_version: int,
    output_path: str | Path | None = None,
    backup: bool = True,
) -> Path:
    """Migrate every JSONL record to ``target_version``.

    Returns the output path. If ``backup`` is true and ``output_path`` aliases
    the source, the original source is first copied to a timestamped backup.
    """
    source = Path(path)
    records = read_jsonl_records(source)
    migrated = [migrate_record(record, target_version=target_version, migrations=migrations) for record in records]
    output = Path(output_path) if output_path is not None else source
    if backup and output.resolve() == source.resolve():
        backup_path = history_backup_path(source)
        backup_path.write_bytes(source.read_bytes())
    return write_jsonl_records(output, migrated)


def migrate_json_file(
    path: str | Path,
    *,
    migrations: tuple[SchemaMigration, ...],
    target_version: int,
    output_path: str | Path | None = None,
    backup: bool = True,
) -> Path:
    """Migrate one JSON object to ``target_version`` and persist the result."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"non-object JSON at {source}")
    migrated = migrate_record(payload, target_version=target_version, migrations=migrations)
    output = Path(output_path) if output_path is not None else source
    if backup and output.resolve() == source.resolve():
        backup_path = history_backup_path(source)
        backup_path.write_bytes(source.read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(migrated, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return output


__all__ = [
    "history_backup_path",
    "migrate_json_file",
    "migrate_jsonl_file",
    "read_jsonl_records",
    "write_jsonl_records",
]
