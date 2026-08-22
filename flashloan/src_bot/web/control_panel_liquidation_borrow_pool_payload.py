from __future__ import annotations


def _normalized_pages(panel, page: int, page_size: int, risk_page=None, high_page=None, core_page=None) -> dict:
    return {
        "page_size": panel._page_size_value(page_size),
        "risk_page": panel._page_value(risk_page if risk_page is not None else page),
        "high_page": panel._page_value(high_page if high_page is not None else page),
        "core_page": panel._page_value(core_page if core_page is not None else page),
    }


def borrow_pool_payload(
    panel,
    page: int = 1,
    page_size: int = 20,
    *,
    risk_page: int | None = None,
    high_page: int | None = None,
    core_page: int | None = None,
    skip_schema: bool = False,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    pages = _normalized_pages(panel, page, page_size, risk_page, high_page, core_page)
    if panel._is_bnb_venus_scope(market_id, chain_id):
        return panel.bnb_venus_borrow_pool_payload(**pages)
    database_url = panel.database_url_or_none()
    if not database_url:
        return panel._borrow_pool_blocked_payload(
            configured=False,
            error="DATABASE_URL is required",
            **pages,
        )
    try:
        return panel.build_borrow_pool_display_payload(
            database_url=database_url,
            scope=panel._scan_market_scope(market_id, chain_id),
            market_kwargs=panel._market_call_kwargs(market_id, chain_id),
            ensure_schema=panel.ensure_database_schema,
            load_borrow_health=panel.db_load_liquidation_borrow_health_pool,
            build_tiers=panel.liquidation_pool_tier_payload,
            load_latest_batch=panel.latest_liquidation_borrow_pool_batch,
            load_scan_configs=panel.db_load_liquidation_scan_config_library,
            build_summary=panel.liquidation_borrow_pool_summary,
            decision_from_payload=panel.decision_from_borrow_pool_payload,
            database_fingerprint=panel._database_url_fingerprint,
            skip_schema=skip_schema,
            **pages,
        )
    except Exception as exc:
        return panel._borrow_pool_blocked_payload(
            configured=True,
            error=panel._scan_error_message(exc) or "database unavailable",
            **pages,
        )


def _run_borrow_health_scan(panel, scan_database_url: str, scan_config, *, force: bool, market_kwargs, progress):
    return panel.run_borrow_health_scan(
        scan_database_url,
        scan_config,
        force=force,
        market_kwargs=market_kwargs,
        ensure_schema=panel.ensure_database_schema,
        load_accounts=panel.db_load_liquidation_accounts,
        unique_accounts=panel._unique_accounts,
        select_accounts=panel.select_liquidation_scan_accounts,
        rpc_urls=panel.aave_rpc_urls,
        pool_address=panel.aave_pool_address,
        scan_health=panel.scan_account_health,
        block_number=panel._liquidation_scan_block_number,
        sync_pool=panel.db_sync_liquidation_borrow_health_pool,
        prune_accounts=panel.db_prune_liquidation_accounts,
        retention_days=panel.liquidation_retention_days,
        mark_selection_finished=panel._mark_liquidation_scan_selection_finished,
        scan_error_message=panel._scan_error_message,
        progress=progress,
    )


def borrow_pool_scan_payload(
    panel,
    force: bool = False,
    page: int = 1,
    page_size: int = 20,
    *,
    risk_page: int | None = None,
    high_page: int | None = None,
    core_page: int | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    pages = _normalized_pages(panel, page, page_size, risk_page, high_page, core_page)
    if panel._is_bnb_venus_scope(market_id, chain_id):
        return panel.bnb_venus_borrow_pool_payload(**pages)
    database_url = panel.database_url_or_none()
    if not database_url:
        return panel._borrow_pool_blocked_payload(
            configured=False,
            error="DATABASE_URL is required",
            **pages,
        )
    config = panel.liquidation_scan_config()
    return panel.run_borrow_pool_scan_payload(
        panel.BorrowPoolScanRuntime(
            scan_cache=panel.LIQUIDATION_SCAN_CACHE,
            scan_lock=panel.LIQUIDATION_SCAN_LOCK,
            database_url=database_url,
            config=config,
            scope=panel._scan_market_scope(market_id, chain_id),
            market_kwargs=panel._market_call_kwargs(market_id, chain_id),
            ttl_seconds=panel.liquidation_borrow_pool_scan_cooldown_seconds(config),
            force=force,
            load_display=panel.liquidation_borrow_pool_payload,
            blocked_payload=panel._borrow_pool_blocked_payload,
            run_scan=lambda *args, **kwargs: _run_borrow_health_scan(panel, *args, **kwargs),
            load_borrow_health=panel.db_load_liquidation_borrow_health_pool,
            build_tiers=panel.liquidation_pool_tier_payload,
            record_batch=panel.db_record_liquidation_borrow_health_scan_batch,
            load_scan_configs=panel.db_load_liquidation_scan_config_library,
            build_scan_summary=panel.build_borrow_pool_scan_summary,
            build_pool_summary=panel.liquidation_borrow_pool_summary,
            decision_from_payload=panel.decision_from_borrow_pool_payload,
            account_tiers=panel.liquidation_account_tier_summary,
            database_fingerprint=panel._database_url_fingerprint,
            error_message=panel._scan_error_message,
            **pages,
        )
    )
