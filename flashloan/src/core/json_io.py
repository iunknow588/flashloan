"""Small JSON helpers that are safe to import outside the web layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src_bot.core.json_io import append_scoped_jsonl, read_scoped_json, read_scoped_jsonl, write_scoped_json


def read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


__all__ = ["append_scoped_jsonl", "read_json", "read_scoped_json", "read_scoped_jsonl", "write_scoped_json"]
