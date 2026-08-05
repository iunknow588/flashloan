from __future__ import annotations

import json
from typing import Any

from db.storage_common import db_connection


def ensure_cow_supported_tokens_table(database_url: str) -> None:
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cow_supported_tokens (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    address TEXT NOT NULL,
                    decimals INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    token_json TEXT NOT NULL,
                    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (network, address)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cow_supported_tokens_network_symbol "
                "ON cow_supported_tokens(network, symbol)"
            )


def replace_cow_supported_tokens(database_url: str, *, network: str, chain_id: int, tokens: list[dict[str, Any]]) -> int:
    ensure_cow_supported_tokens_table(database_url)
    values = [
        (
            network,
            int(chain_id),
            str(token["symbol"]).upper(),
            str(token["address"]).lower(),
            int(token["decimals"]),
            str(token.get("source") or ""),
            json.dumps(token, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
        for token in tokens
    ]
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM cow_supported_tokens WHERE network = %s", (network,))
            if values:
                cursor.executemany(
                    """
                    INSERT INTO cow_supported_tokens (
                        network, chain_id, symbol, address, decimals, source, token_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    values,
                )
    return len(values)


def load_cow_supported_tokens(database_url: str, *, network: str) -> list[dict[str, Any]]:
    ensure_cow_supported_tokens_table(database_url)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT symbol, address, decimals, source, token_json, refreshed_at
                FROM cow_supported_tokens
                WHERE network = %s
                ORDER BY symbol, address
                """,
                (network,),
            )
            rows = cursor.fetchall()
    tokens = []
    for symbol, address, decimals, source, token_json, refreshed_at in rows:
        try:
            payload = json.loads(token_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        payload.update(
            {
                "symbol": str(symbol).upper(),
                "address": str(address).lower(),
                "decimals": int(decimals),
                "source": str(source or ""),
                "refreshed_at": refreshed_at.isoformat() if hasattr(refreshed_at, "isoformat") else str(refreshed_at),
            }
        )
        tokens.append(payload)
    return tokens
