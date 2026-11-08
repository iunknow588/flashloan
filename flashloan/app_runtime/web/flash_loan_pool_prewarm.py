"""Warm the static Robinhood V4 pool view before the first page request."""

from __future__ import annotations

import os


def prewarm_robinhood_mainnet_v4_pool_view() -> bool:
    if os.getenv("FLASHLOAN_DISABLE_POOL_PREWARM", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    try:
        from core.db.storage_common import database_url
        from core.flash_loan_pool_discovery import protocol_options_for_chain
        from web.flash_loan_pools import flash_loan_pools_payload

        resolved_database_url = database_url()
        discovered_protocols = [
            str(item.get("protocol") or "").strip()
            for item in protocol_options_for_chain(chain_id=4663, database_url=resolved_database_url)
            if str(item.get("protocol") or "").strip()
        ]
        payload = flash_loan_pools_payload(
            chain_id=4663,
            protocols=["uniswap_v4"],
            database_url=resolved_database_url,
            discovered_protocols=discovered_protocols,
        )
        return "protocol_views" in payload
    except Exception:
        return False
