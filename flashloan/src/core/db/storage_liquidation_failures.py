from __future__ import annotations

import time
from typing import Any

from core.kv_rows import count, load_rows, upsert_row
from core.local_kv import ensure_store


def _market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    return {
        "market_id": str(market_id or "default"),
        "chain_id": int(chain_id) if chain_id is not None else 0,
        "network": str(market_id or "local"),
        "protocol": "aave",
    }


def record_liquidation_failure_sample(
    database_url: str,
    *,
    account: str | None = None,
    block_number: int | None = None,
    collateral_asset: str | None = None,
    debt_asset: str | None = None,
    failure_type: str,
    failure_reason: str | None = None,
    payload: dict[str, Any] | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> int:
    ensure_store(database_url)
    scope = _market_scope(market_id, chain_id)
    sample_id = count(database_url, "liquidation_failure_samples") + 1
    upsert_row(
        database_url,
        "liquidation_failure_samples",
        str(sample_id),
        {
            "id": sample_id,
            **scope,
            "account": account,
            "block_number": block_number,
            "collateral_asset": collateral_asset,
            "debt_asset": debt_asset,
            "failure_type": failure_type,
            "failure_reason": failure_reason,
            "payload": payload or {},
            "created_at": time.time(),
        },
    )
    return sample_id


def _load_failure_samples(
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    scope = _market_scope(market_id, chain_id)
    rows = load_rows(database_url, "liquidation_failure_samples")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == scope["market_id"]]
    if chain_id is not None:
        rows = [row for row in rows if int(row.get("chain_id") or 0) == scope["chain_id"]]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows


def load_recent_liquidation_failure_samples(
    database_url: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    return _load_failure_samples(database_url, market_id=market_id, chain_id=chain_id)[:max(1, int(limit))]


def load_liquidation_failure_samples_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = _load_failure_samples(database_url, market_id=market_id, chain_id=chain_id)
    return [row for row in rows if str(row.get("account") or "").lower() == str(account).lower()][:max(1, int(limit))]


__all__ = [
    "load_liquidation_failure_samples_for_account",
    "load_recent_liquidation_failure_samples",
    "record_liquidation_failure_sample",
]
