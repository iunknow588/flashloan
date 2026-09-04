"""Small JSON helpers that are safe to import outside the web layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core.contracts import validate_scoped_payload


def read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_scoped_json(path: Path, expected_scope: dict) -> dict:
    """Read JSON and reject missing or mismatched persisted scope."""
    payload = read_json(path)
    if payload is None:
        raise ValueError(f"scoped JSON is missing or invalid: {path}")
    validate_scoped_payload(payload, expected_scope)
    return payload


def write_scoped_json(path: Path, payload: dict, expected_scope: dict) -> Path:
    """Validate scope before writing a JSON state payload."""
    validate_scoped_payload(payload, expected_scope)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def read_scoped_jsonl(path: Path, expected_scope: dict) -> list[dict]:
    """Read JSONL records and reject the first malformed or wrong-scope row."""
    records: list[dict] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"scoped JSONL is missing: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid scoped JSONL at line {line_number}") from exc
        try:
            validate_scoped_payload(record, expected_scope)
        except ValueError as exc:
            raise ValueError(f"invalid scoped JSONL at line {line_number}: {exc}") from exc
        records.append(record)
    return records


def append_scoped_jsonl(path: Path, record: dict, expected_scope: dict) -> Path:
    """Validate and append one scoped JSONL record."""
    validate_scoped_payload(record, expected_scope)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
    return path
