from __future__ import annotations

import json
from typing import Any

from db.storage_common import db_connection


def ensure_control_panel_parameters_table(database_url: str, *, connect_timeout: int = 8) -> None:
    with db_connection(database_url, connect_timeout=connect_timeout) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS control_panel_parameters (
                    namespace TEXT NOT NULL,
                    parameter_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (namespace, parameter_key)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_control_panel_parameters_namespace "
                "ON control_panel_parameters(namespace, updated_at DESC)"
            )


def _encode_parameter_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _decode_parameter_value(value_json: object) -> Any:
    try:
        return json.loads(str(value_json))
    except (TypeError, json.JSONDecodeError):
        return None


def load_control_panel_parameter_map(database_url: str, namespace: str, *, connect_timeout: int = 8) -> dict[str, Any]:
    ensure_control_panel_parameters_table(database_url, connect_timeout=connect_timeout)
    with db_connection(database_url, connect_timeout=connect_timeout) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT parameter_key, value_json
                FROM control_panel_parameters
                WHERE namespace = %s
                ORDER BY parameter_key
                """,
                (str(namespace),),
            )
            rows = cursor.fetchall()
    return {str(key): _decode_parameter_value(value_json) for key, value_json in rows}


def save_control_panel_parameter_map(
    database_url: str,
    namespace: str,
    values: dict[str, Any],
    *,
    connect_timeout: int = 8,
) -> dict[str, Any]:
    ensure_control_panel_parameters_table(database_url, connect_timeout=connect_timeout)
    rows = [
        (str(namespace), str(key), _encode_parameter_value(value))
        for key, value in values.items()
    ]
    with db_connection(database_url, connect_timeout=connect_timeout) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO control_panel_parameters (namespace, parameter_key, value_json, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (namespace, parameter_key)
                DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = NOW()
                """,
                rows,
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
