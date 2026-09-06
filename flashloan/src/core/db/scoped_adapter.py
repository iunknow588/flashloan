"""Small DB-boundary helpers for validating scoped rows before use."""

from __future__ import annotations

from typing import Any, Mapping

from core.contracts import validate_scoped_payload


def validate_scoped_row(row: Mapping[str, Any], expected_scope: dict[str, Any]) -> dict[str, Any]:
    """Validate a DB row or decoded JSON column at the adapter boundary."""
    payload = dict(row)
    validate_scoped_payload(payload, expected_scope)
    return payload


def validate_scoped_rows(rows: list[Mapping[str, Any]], expected_scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate every DB row before exposing a result set to domain code."""
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        try:
            validated.append(validate_scoped_row(row, expected_scope))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid scoped DB row at index {index}: {exc}") from exc
    return validated


__all__ = ["validate_scoped_row", "validate_scoped_rows"]
