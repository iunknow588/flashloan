from __future__ import annotations

import hashlib
from typing import Optional

from web3 import Web3


def database_url_fingerprint(database_url: str | None) -> str | None:
    if not database_url:
        return None
    return hashlib.sha256(database_url.encode("utf-8")).hexdigest()[:12]


def scan_market_scope(panel, market_id: str | None = None, chain_id: int | None = None) -> dict[str, object]:
    market = (
        panel.liquidation_market_config_for(market_id, chain_id=chain_id)
        if market_id is not None or chain_id is not None
        else panel.liquidation_market_config()
    )
    return {
        "market_id": market.market_id,
        "chain_id": market.chain_id,
        "network": market.network,
        "protocol": market.protocol,
    }


def market_call_kwargs(market_id: str | None = None, chain_id: int | None = None) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if market_id is not None:
        kwargs["market_id"] = market_id
    if chain_id is not None:
        kwargs["chain_id"] = chain_id
    return kwargs


def is_bnb_venus_scope(panel, market_id: str | None = None, chain_id: int | None = None) -> bool:
    if str(market_id or "").strip().lower() == panel.BNB_MARKET_ID:
        return True
    try:
        return market_id is None and int(chain_id) == panel.BNB_CHAIN_ID
    except (TypeError, ValueError):
        return False


def attach_core_execution_summaries(
    panel,
    database_url: str,
    rows: list[dict],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict]:
    return panel.build_core_execution_summaries(
        database_url,
        rows,
        market_kwargs=panel._market_call_kwargs(market_id, chain_id),
        load_latest_for_accounts=panel.db_load_latest_liquidation_execution_attempts_for_accounts,
        load_recent=panel.db_load_recent_liquidation_execution_attempts,
    )


def core_rows_with_execution(
    panel,
    database_url: str,
    limit: int,
    offset: int = 0,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict]:
    return panel.load_core_rows_with_execution(
        database_url,
        limit,
        offset,
        market_kwargs=panel._market_call_kwargs(market_id, chain_id),
        load_core=panel.db_load_liquidation_core_opportunity_pool,
        load_latest_for_accounts=panel.db_load_latest_liquidation_execution_attempts_for_accounts,
        load_recent=panel.db_load_recent_liquidation_execution_attempts,
    )


def pool_tier_payload(
    panel,
    database_url: str,
    limit: int,
    *,
    high_page: int = 1,
    core_page: int = 1,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    return panel.build_pool_tier_payload(
        database_url,
        limit,
        high_page=high_page,
        core_page=core_page,
        market_kwargs=panel._market_call_kwargs(market_id, chain_id),
        load_high=panel.db_load_liquidation_high_frequency_pool,
        load_core=panel.db_load_liquidation_core_opportunity_pool,
        load_counts=panel.db_load_liquidation_pool_counts,
        load_latest_for_accounts=panel.db_load_latest_liquidation_execution_attempts_for_accounts,
        load_recent=panel.db_load_recent_liquidation_execution_attempts,
    )


def tier_accounts(
    panel,
    database_url: str,
    accounts: list[str],
    loader,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[str]:
    return panel._select_tier_accounts(
        database_url,
        accounts,
        loader,
        market_id=market_id,
        chain_id=chain_id,
        market_kwargs_builder=panel._market_call_kwargs,
    )


def select_scan_accounts(
    panel,
    database_url: str,
    accounts: list[str],
    config,
    *,
    force: bool = False,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    return panel._select_scan_accounts(
        database_url,
        accounts,
        config,
        scan_cache=panel.LIQUIDATION_SCAN_CACHE,
        core_loader=panel.db_load_liquidation_core_opportunity_pool,
        high_frequency_loader=panel.db_load_liquidation_high_frequency_pool,
        market_kwargs_builder=panel._market_call_kwargs,
        force=force,
        market_id=market_id,
        chain_id=chain_id,
    )


def liquidation_scan_block_number(rpc_url: str) -> int | None:
    try:
        return int(Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 8})).eth.block_number)
    except Exception:
        return None


def prioritized_accounts(panel, database_url: str, accounts: list[str]) -> list[str]:
    selection = panel.select_liquidation_scan_accounts(
        database_url,
        accounts,
        panel.liquidation_scan_config(),
        force=True,
    )
    return list(selection.get("selected_accounts") or panel._unique_accounts(accounts))


def latest_borrow_pool_batch(
    panel,
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> Optional[dict]:
    batches = panel.db_load_liquidation_borrow_health_scan_batches(
        database_url,
        limit=1,
        **panel._market_call_kwargs(market_id, chain_id),
    )
    return batches[0] if batches else None


def borrow_pool_summary(
    panel,
    rows: list[dict],
    *,
    scanned: bool = False,
    scan_payload: Optional[dict] = None,
    display_limit: Optional[int] = None,
) -> dict:
    scan_summary = dict((scan_payload or {}).get("summary") or {})
    config = panel.liquidation_scan_config()
    return panel.build_borrow_pool_summary(
        rows,
        config=config,
        display_limit=panel._page_size_value(display_limit, panel.liquidation_borrow_pool_display_limit()),
        scan_cache=panel.LIQUIDATION_SCAN_CACHE,
        scan_interval_seconds=panel.liquidation_borrow_pool_scan_cooldown_seconds(config),
        account_tiers=scan_summary.get("account_tiers") or panel.liquidation_account_tier_summary(),
        scanned=scanned,
        scan_payload=scan_payload,
    )


def borrow_pool_scan_cooldown_seconds(panel, config=None) -> float:
    current = config or panel.liquidation_scan_config()
    return max(
        0.1,
        min(
            float(panel.liquidation_scan_interval_seconds()),
            float(current.core_opportunity_refresh_seconds),
        ),
    )
