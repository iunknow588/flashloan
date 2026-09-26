from __future__ import annotations

import time
from typing import Any

from core.kv_rows import collection_key, load_rows, upsert_row
from core.local_kv import delete, ensure_store


def ensure_cow_supported_tokens_table(database_url: str) -> None:
    ensure_store(database_url)


def replace_cow_supported_tokens(database_url: str, *, network: str, chain_id: int, tokens: list[dict[str, Any]]) -> int:
    ensure_cow_supported_tokens_table(database_url)
    for existing in load_rows(database_url, "cow_supported_tokens"):
        if str(existing.get("network")) == str(network):
            delete(database_url, "cow_supported_tokens", existing.get("_key") or "")
    for token in tokens:
        key = collection_key(network, str(token["address"]).lower())
        upsert_row(
            database_url,
            "cow_supported_tokens",
            key,
            {
                "_key": key,
                "network": str(network),
                "chain_id": int(chain_id),
                "symbol": str(token["symbol"]).upper(),
                "address": str(token["address"]).lower(),
                "decimals": int(token["decimals"]),
                "source": str(token.get("source") or ""),
                "token_json": token,
                "refreshed_at": time.time(),
            },
        )
    return len(tokens)


def load_cow_supported_tokens(database_url: str, *, network: str) -> list[dict[str, Any]]:
    ensure_cow_supported_tokens_table(database_url)
    tokens = []
    for row in load_rows(database_url, "cow_supported_tokens"):
        if str(row.get("network")) != str(network):
            continue
        payload = dict(row.get("token_json") or {})
        payload.update(
            {
                "symbol": str(row.get("symbol") or "").upper(),
                "address": str(row.get("address") or "").lower(),
                "decimals": int(row.get("decimals") or 0),
                "source": str(row.get("source") or ""),
                "refreshed_at": row.get("refreshed_at"),
            }
        )
        tokens.append(payload)
    return tokens


__all__ = [
    "ensure_cow_supported_tokens_table",
    "load_cow_supported_tokens",
    "replace_cow_supported_tokens",
]
