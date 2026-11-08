"""SQLite persistence for PoolManager flash-accounting source balances.

The raw V4 pool tables record trading-pool liquidity only. V4 flash loans are
repayed from the PoolManager's currency balances, so those balances are stored
separately in the existing ``chain_business_initialization`` domain table.
Storage is written directly from in-memory RPC evidence; JSON evidence files
are never read or written by this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.db.storage_chain_core import load_initialization, upsert_initialization


POOL_MANAGER_ACCOUNTING_DOMAIN = "v4_pool_manager_accounting_snapshot"
POOL_MANAGER_ACCOUNTING_PROTOCOL = "uniswap_v4"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    import json

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def save_v4_pool_manager_accounting(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = POOL_MANAGER_ACCOUNTING_PROTOCOL,
    pool_manager: str = "",
    block_number: int | None = None,
    generated_at: str | None = None,
    scan_scope: str = "",
    source_label: str = "",
    native_balance_wei: str | int | None = None,
    sources: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Upsert one in-memory PoolManager accounting snapshot into SQLite."""
    source_rows = [dict(item) for item in sources or [] if isinstance(item, dict)]
    completeness = {
        "schemaVersion": "robinhood-testnet-v4-pool-manager-accounting-v1",
        "poolManager": str(pool_manager or "").strip(),
        "balanceOwner": "poolManager",
        "balanceSemantics": (
            "flash_loan_repayment_source_balances"
            " (PoolManager token/native balances, not trading-pool liquidity)"
        ),
        "balanceReadMode": "rpc_snapshot_at_block",
        "blockNumber": int(block_number) if block_number is not None else None,
        "generatedAt": generated_at or _stamp(),
        "scanScope": str(scan_scope or "").strip(),
        "sourceLabel": str(source_label or "").strip(),
        "nativePoolManagerBalanceWei": str(native_balance_wei or ""),
        "sourceCount": len(source_rows),
        "sources": source_rows,
        "metadata": metadata or {},
        "errors": list(errors or []),
    }
    upsert_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=POOL_MANAGER_ACCOUNTING_DOMAIN,
        status="complete" if not errors else "partial",
        completeness=completeness,
    )
    return {"ok": True, "domain": POOL_MANAGER_ACCOUNTING_DOMAIN, "sourceCount": len(source_rows)}


def load_v4_pool_manager_accounting(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = POOL_MANAGER_ACCOUNTING_PROTOCOL,
) -> dict[str, Any]:
    """Load the most recent persisted accounting snapshot for one chain scope."""
    row = load_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=POOL_MANAGER_ACCOUNTING_DOMAIN,
    )
    if not isinstance(row, dict):
        return {
            "ok": False,
            "status": "accounting_snapshot_missing",
            "sourceCount": 0,
            "sources": [],
            "nativePoolManagerBalanceWei": None,
            "refreshedAt": None,
        }
    completeness = _json_loads(row.get("completeness_json"))
    sources = [item for item in completeness.get("sources") or [] if isinstance(item, dict)]
    return {
        "ok": bool(sources or completeness.get("nativePoolManagerBalanceWei")),
        "status": str(row.get("status") or "complete"),
        "sourceCount": len(sources),
        "sources": sources,
        "poolManager": completeness.get("poolManager") or "",
        "balanceOwner": completeness.get("balanceOwner") or "poolManager",
        "balanceSemantics": completeness.get("balanceSemantics") or "",
        "balanceReadMode": completeness.get("balanceReadMode") or "rpc_snapshot_at_block",
        "blockNumber": completeness.get("blockNumber"),
        "generatedAt": completeness.get("generatedAt"),
        "scanScope": completeness.get("scanScope") or "",
        "sourceLabel": completeness.get("sourceLabel") or "",
        "nativePoolManagerBalanceWei": completeness.get("nativePoolManagerBalanceWei") or None,
        "metadata": completeness.get("metadata") or {},
        "errors": completeness.get("errors") or [],
        "refreshedAt": row.get("refreshed_at"),
    }


__all__ = [
    "POOL_MANAGER_ACCOUNTING_DOMAIN",
    "POOL_MANAGER_ACCOUNTING_PROTOCOL",
    "load_v4_pool_manager_accounting",
    "save_v4_pool_manager_accounting",
]
