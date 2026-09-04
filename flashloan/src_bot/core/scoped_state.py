"""Scope-aware runtime state boundary shared by cache and daemon code."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.json_io import read_scoped_json, write_scoped_json


def load_runtime_state(path: str | Path, expected_scope: dict[str, Any]) -> dict[str, Any]:
    return read_scoped_json(Path(path), expected_scope)


def save_runtime_state(path: str | Path, payload: dict[str, Any], expected_scope: dict[str, Any]) -> Path:
    return write_scoped_json(Path(path), payload, expected_scope)


__all__ = ["load_runtime_state", "save_runtime_state"]
