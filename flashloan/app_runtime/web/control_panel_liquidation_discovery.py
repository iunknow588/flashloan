"""Liquidation discovery window and scan-config payload helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from web3 import Web3

logger = logging.getLogger(__name__)


def liquidation_scan_config_library(
    limit: int = 100,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "configs": []}
    try:
        ensure_database_schema(database_url)
        kwargs = {}
        if market_id is not None:
            kwargs["market_id"] = market_id
        if chain_id is not None:
            kwargs["chain_id"] = chain_id
        return {"configured": True, "configs": db_load_liquidation_scan_config_library(database_url, limit=limit, **kwargs)}
    except Exception as exc:
        return {"configured": True, "configs": [], "error": redact_sensitive_text(exc)}


def liquidation_scan_config_payload(config_key: str) -> dict:
    configs = liquidation_scan_config_library(limit=100).get("configs") or []
    for item in configs:
        if item.get("config_key") == config_key:
            payload = item.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


def resolve_discovery_block_range(rpc_url: str, from_block: int, to_block: Optional[int]) -> tuple[int, int, int]:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    latest_block = int(w3.eth.block_number)
    start_block = max(0, latest_block + int(from_block)) if int(from_block) < 0 else max(0, int(from_block))
    if to_block is None:
        end_block = latest_block
    else:
        raw_to_block = int(to_block)
        end_block = max(0, latest_block + raw_to_block) if raw_to_block < 0 else min(latest_block, raw_to_block)
    return latest_block, start_block, end_block


def record_liquidation_discovery_window(
    *,
    mode: str,
    status: str,
    rpc_url: str,
    pool_address: str,
    from_block: int,
    to_block: int,
    scan_start_at: datetime,
    scan_end_at: datetime,
    discovered_count: int = 0,
    error: Optional[str] = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> None:
    database_url = database_url_or_none()
    if not database_url:
        return
    try:
        ensure_database_schema(database_url)
        db_record_liquidation_discovery_scan(
            database_url,
            mode=mode,
            status=status,
            rpc_url=rpc_url,
            pool_address=pool_address,
            from_block=from_block,
            to_block=to_block,
            scan_start_at=scan_start_at,
            scan_end_at=scan_end_at,
            discovered_count=discovered_count,
            error=error,
            market_id=market_id,
            chain_id=chain_id,
        )
    except Exception:
        logger.warning("failed to record liquidation discovery scan", exc_info=True)
        pass


def discovery_window_continuity_error(mode: str, from_block: int, to_block: int, progress: dict) -> Optional[str]:
    if from_block > to_block:
        return None
    if mode == "recent":
        latest_to = progress.get("latest_recent_to_block")
        if latest_to is None:
            return None
        if int(from_block) <= int(latest_to) + 1:
            return None
        return f"recent scan gap: previous to_block {latest_to}, next from_block {from_block}"
    if mode == "historical-backfill":
        earliest_from = progress.get("earliest_backfill_from_block")
        if earliest_from is None:
            return None
        if int(to_block) >= int(earliest_from) - 1:
            return None
        return f"historical backfill gap: next to_block {to_block}, previous from_block {earliest_from}"
    return None


def sync_liquidation_accounts_to_database(
    accounts: list[str],
    source: str = "manual",
    scan_start_at: Optional[datetime] = None,
    scan_end_at: Optional[datetime] = None,
    update_existing: bool = True,
) -> None:
    database_url = database_url_or_none()
    if not database_url or not accounts:
        return
    ensure_database_schema(database_url)
    db_upsert_liquidation_accounts(
        database_url,
        accounts,
        source=source,
        active=True,
        scan_start_at=scan_start_at,
        scan_end_at=scan_end_at,
        update_existing=update_existing,
    )
    db_prune_liquidation_accounts(database_url, retained_days=liquidation_retention_days())


def parse_iso_datetime(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def liquidation_discovery_window(force_full: bool = False) -> tuple[datetime, datetime, int, Optional[int], int, dict, str]:
    now = datetime.now(timezone.utc)
    registry = liquidation_account_registry_window()
    pool_address = aave_pool_address()
    progress = liquidation_discovery_progress(pool_address)
    config_cursor = liquidation_scan_config_payload("liquidation_discovery_scans.latest_success")
    if not config_cursor:
        config_cursor = liquidation_scan_config_payload("liquidation_discovery_scans.latest")
    if progress.get("latest_recent_to_block") is None and config_cursor.get("status") == "success":
        try:
            progress["latest_recent_to_block"] = int(config_cursor.get("to_block"))
            progress["latest_recent_scan_end_at"] = config_cursor.get("scan_end_at")
        except (TypeError, ValueError):
            pass
    overlap_blocks = liquidation_discovery_block_overlap()
    latest_recent_to_block = progress.get("latest_recent_to_block")
    latest_end = parse_iso_datetime(progress.get("latest_recent_scan_end_at")) or parse_iso_datetime(registry.get("latest_scan_end_at"))
    start_anchor = now - timedelta(days=liquidation_account_scan_start_days())
    mode = "recent"

    if force_full or latest_recent_to_block is None:
        scan_start_at = start_anchor
        scan_end_at = now
        now_for_blocks = datetime.now(timezone.utc)
        lookback_blocks = max(1, int(max(0.0, (now_for_blocks - scan_start_at).total_seconds()) / liquidation_block_seconds()))
        from_block = -lookback_blocks
        to_block = None
        registry["discovery_scan_progress"] = progress
        registry["discovery_cursor"] = {
            "source": "full-year-bootstrap" if latest_recent_to_block is None else "full-year-rescan",
            "mode": mode,
            "scan_days": liquidation_account_scan_start_days(),
            "overlap_blocks": overlap_blocks,
            "next_from_block": from_block,
            "next_to_block": to_block,
        }
        return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode

    if latest_recent_to_block is not None and not force_full:
        scan_start_at = max(start_anchor, latest_end) if latest_end else now - timedelta(days=liquidation_recent_discovery_days())
        scan_end_at = now
        from_block = max(0, int(latest_recent_to_block) + 1 - overlap_blocks)
        to_block = None
        lookback_blocks = 0
        registry["discovery_scan_progress"] = progress
        registry["discovery_cursor"] = {
            "source": "block-ledger",
            "direction": "forward-from-start",
            "mode": mode,
            "overlap_blocks": overlap_blocks,
            "previous_latest_to_block": int(latest_recent_to_block),
            "next_from_block": from_block,
            "next_to_block": None,
        }
        return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode

    scan_start_at = start_anchor
    scan_end_at = now
    lookback_blocks = max(1, int(max(0.0, (now - scan_start_at).total_seconds()) / liquidation_block_seconds()))
    from_block = -lookback_blocks
    to_block = None
    registry["discovery_scan_progress"] = progress
    registry["discovery_cursor"] = {
        "source": "full-year-bootstrap",
        "mode": mode,
        "scan_days": liquidation_account_scan_start_days(),
        "overlap_blocks": overlap_blocks,
        "next_from_block": from_block,
        "next_to_block": to_block,
    }
    return scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode


def _bind_liquidation_base_globals() -> None:
    """Bind base panel globals after both modules are safely initialized."""
    from web.control_panel_liquidation_base import (
        aave_pool_address,
        database_url_or_none,
        db_load_liquidation_scan_config_library,
        db_prune_liquidation_accounts,
        db_record_liquidation_discovery_scan,
        db_upsert_liquidation_accounts,
        ensure_database_schema,
        liquidation_account_registry_window,
        liquidation_account_scan_start_days,
        liquidation_block_seconds,
        liquidation_discovery_block_overlap,
        liquidation_discovery_progress,
        liquidation_recent_discovery_days,
        liquidation_retention_days,
        redact_sensitive_text,
    )
    globals().update(
        {
            "aave_pool_address": aave_pool_address,
            "database_url_or_none": database_url_or_none,
            "db_load_liquidation_scan_config_library": db_load_liquidation_scan_config_library,
            "db_prune_liquidation_accounts": db_prune_liquidation_accounts,
            "db_record_liquidation_discovery_scan": db_record_liquidation_discovery_scan,
            "db_upsert_liquidation_accounts": db_upsert_liquidation_accounts,
            "ensure_database_schema": ensure_database_schema,
            "liquidation_account_registry_window": liquidation_account_registry_window,
            "liquidation_account_scan_start_days": liquidation_account_scan_start_days,
            "liquidation_block_seconds": liquidation_block_seconds,
            "liquidation_discovery_block_overlap": liquidation_discovery_block_overlap,
            "liquidation_discovery_progress": liquidation_discovery_progress,
            "liquidation_recent_discovery_days": liquidation_recent_discovery_days,
            "liquidation_retention_days": liquidation_retention_days,
            "redact_sensitive_text": redact_sensitive_text,
        }
    )


_bind_liquidation_base_globals()
