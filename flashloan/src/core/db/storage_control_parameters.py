from __future__ import annotations

import json
from typing import Any

from core.kv_rows import collection_key, delete_key, get_row, load_rows, upsert_row
from core.local_kv import ensure_store


def ensure_control_panel_parameters_table(database_url: str, *, connect_timeout: int = 8) -> None:
    ensure_store(database_url)


def _encode_parameter_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _decode_parameter_value(value_json: str) -> Any:
    try:
        return json.loads(value_json)
    except (TypeError, json.JSONDecodeError):
        return None


def load_control_panel_parameter_map(database_url: str, namespace: str, *, connect_timeout: int = 8) -> dict[str, Any]:
    ensure_control_panel_parameters_table(database_url, connect_timeout=connect_timeout)
    rows = load_rows(database_url, "parameters", prefix=f"{namespace}|")
    result: dict[str, Any] = {}
    for row in rows:
        key = str(row.get("parameter_key") or "")
        if not key:
            continue
        result[key] = _decode_parameter_value(str(row.get("value_json") or "null"))
    return result


def save_control_panel_parameter_map(
    database_url: str,
    namespace: str,
    values: dict[str, Any],
    *,
    connect_timeout: int = 8,
) -> dict[str, Any]:
    ensure_control_panel_parameters_table(database_url, connect_timeout=connect_timeout)
    for key, value in values.items():
        upsert_row(
            database_url,
            "parameters",
            collection_key(namespace, key),
            {
                "namespace": str(namespace),
                "parameter_key": str(key),
                "value_json": _encode_parameter_value(value),
            },
        )
    return dict(values)


def load_control_panel_parameter(
    database_url: str,
    namespace: str,
    key: str,
    default: Any = None,
    *,
    connect_timeout: int = 8,
) -> Any:
    values = load_control_panel_parameter_map(database_url, namespace, connect_timeout=connect_timeout)
    return values.get(str(key), default)


def save_control_panel_parameter(
    database_url: str,
    namespace: str,
    key: str,
    value: Any,
    *,
    connect_timeout: int = 8,
) -> Any:
    save_control_panel_parameter_map(database_url, namespace, {str(key): value}, connect_timeout=connect_timeout)
    return value


def delete_control_panel_parameter(
    database_url: str,
    namespace: str,
    key: str,
    *,
    connect_timeout: int = 8,
) -> bool:
    ensure_control_panel_parameters_table(database_url, connect_timeout=connect_timeout)
    return delete_key(database_url, "parameters", collection_key(namespace, key))


__all__ = [
    "delete_control_panel_parameter",
    "ensure_control_panel_parameters_table",
    "load_control_panel_parameter",
    "load_control_panel_parameter_map",
    "save_control_panel_parameter",
    "save_control_panel_parameter_map",
]
