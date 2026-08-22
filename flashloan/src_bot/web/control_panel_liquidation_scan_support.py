from __future__ import annotations


def run_account_backfill_once(panel) -> dict:
    return panel.run_account_backfill_workflow(
        panel.AccountBackfillRuntime(
            cache=panel.LIQUIDATION_ACCOUNT_BACKFILL_CACHE,
            lock=panel.LIQUIDATION_ACCOUNT_BACKFILL_LOCK,
            stop_event=panel.LIQUIDATION_ACCOUNT_BACKFILL_STOP,
            status_payload=panel.account_backfill_status_payload,
            database_url=panel.database_url_or_none,
            auto_discovery_enabled=lambda: panel.os.getenv(
                "LIQUIDATION_AUTO_DISCOVER_ACCOUNTS",
                "true",
            ).strip().lower() not in {"0", "false", "no"},
            pool_address=panel.aave_pool_address,
            discovery_window=panel.liquidation_discovery_window,
            account_registry_window=panel.liquidation_account_registry_window,
            chunk_size=lambda: panel.parse_env_int(
                "LIQUIDATION_BORROW_SCAN_CHUNK_SIZE",
                1000,
                minimum=1,
            )[0],
            rpc_urls=panel.aave_rpc_urls,
            resolve_block_range=panel.resolve_discovery_block_range,
            discover_borrowers=panel.discover_borrower_addresses,
            configured_database_url=panel.configured_database_url,
            upsert_accounts=panel.db_upsert_liquidation_accounts,
            invalidate_account_cache=lambda: panel.LIQUIDATION_ACCOUNT_CACHE.__setitem__(
                "updated_at",
                0.0,
            ),
            set_progress=panel._set_account_backfill_progress,
            error_message=panel._scan_error_message,
        )
    )


def normalize_account_values(panel, values: object) -> list[str]:
    if isinstance(values, str):
        raw_items = values.replace("\r", "\n").replace(",", "\n").splitlines()
    elif isinstance(values, list):
        raw_items = values
    else:
        raw_items = []
    normalized: list[str] = []
    for item in raw_items:
        try:
            checksum = panel.Web3.to_checksum_address(str(item).strip())
        except ValueError:
            continue
        if checksum not in normalized:
            normalized.append(checksum)
    return normalized


def start_refresh_loop(panel) -> None:
    panel.start_liquidation_refresh_workflow(
        panel.LiquidationRefreshRuntime(
            get_thread=lambda: panel.LIQUIDATION_REFRESH_THREAD,
            set_thread=panel._set_liquidation_refresh_thread,
            thread_factory=panel.threading.Thread,
            stop_event=panel.LIQUIDATION_REFRESH_STOP,
            enabled=panel.liquidation_background_refresh_enabled,
            discover_accounts=panel.discover_and_sync_liquidation_accounts,
            scan_borrow_pool=panel.liquidation_borrow_pool_scan_payload,
            discovery_cache=panel.LIQUIDATION_DISCOVERY_CACHE,
            backfill_interval_seconds=panel.liquidation_backfill_interval_seconds,
            cooldown_seconds=panel.liquidation_borrow_pool_scan_cooldown_seconds,
        )
    )


def initialize_runtime(panel) -> None:
    panel.discover_and_sync_liquidation_accounts(force_full=False)
    panel.load_liquidation_account_registry(force=True)
    panel.liquidation_borrow_pool_scan_payload(force=True)
    panel.start_liquidation_refresh_loop()


def scan_context_assets(panel) -> tuple[object | None, list[dict], object | None]:
    pool_address = panel.aave_pool_address()
    if not pool_address:
        return None, [], "missing AAVE_POOL_ADDRESS"
    last_error = None
    for candidate in panel.aave_rpc_urls():
        try:
            assets = panel.load_reserve_assets_for_scan(
                candidate,
                pool_address,
                limit=panel.AAVE_RESERVE_SYMBOL_LIMIT,
            )
            if assets:
                return candidate, assets, None
        except Exception as exc:
            last_error = panel._scan_error_message(exc)
    return None, [], last_error or "unable to load reserve assets"


def health_summary(panel, rows: list[dict], account_count: int, account_source: str, config, rpc_url, error) -> dict:
    return panel.build_health_summary(
        rows,
        account_count=account_count,
        account_source=account_source,
        config=config,
        rpc_url=rpc_url,
        error=error,
        registry_window=panel.liquidation_account_registry_window(),
        scan_cache=panel.LIQUIDATION_SCAN_CACHE,
        discovery_cache=panel.LIQUIDATION_DISCOVERY_CACHE,
        retention_days=panel.liquidation_retention_days(),
        discovery_interval_seconds=panel.liquidation_discovery_interval_seconds(),
        backfill_interval_seconds=panel.liquidation_backfill_interval_seconds(),
        recent_discovery_days=panel.liquidation_recent_discovery_days(),
        backfill_window_days=panel.liquidation_backfill_window_days(),
    )


def health_with_scan_state(
    panel,
    payload: dict,
    ttl_seconds: float,
    *,
    running: bool,
    cache_age_seconds=None,
    cooldown_remaining_seconds=None,
) -> dict:
    return panel.attach_scan_state(
        payload,
        ttl_seconds,
        scan_cache=panel.LIQUIDATION_SCAN_CACHE,
        running=running,
        cache_age_seconds=cache_age_seconds,
        cooldown_remaining_seconds=cooldown_remaining_seconds,
    )


def health_display_rows(panel, rows: list[dict], limit: int) -> list[dict]:
    return panel.display_health_rows(rows, limit=limit, band=panel.health_factor_band)


def record_health_scan_rows(panel, rows: list[dict]) -> None:
    database_url = panel.database_url_or_none()
    if not database_url or not rows:
        return
    try:
        config = panel.liquidation_scan_config()
        panel.db_sync_liquidation_borrow_health_pool(
            database_url,
            rows,
            config.watch_health_factor,
            account_reports=panel.build_account_health_reports(rows),
            min_operator_net_profit_usd=config.min_operator_net_profit_usd,
        )
        panel.db_prune_liquidation_accounts(database_url, retained_days=panel.liquidation_retention_days())
    except Exception:
        pass


def account_tier_summary(panel) -> dict:
    return panel.build_account_tier_summary(panel.liquidation_account_registry_window())


def health_runtime(panel):
    return panel.LiquidationHealthRuntime(
        scan_cache=panel.LIQUIDATION_SCAN_CACHE,
        scan_lock=panel.LIQUIDATION_SCAN_LOCK,
        scan_interval_seconds=panel.liquidation_scan_interval_seconds,
        scan_config=panel.liquidation_scan_config,
        load_account_registry=panel.load_liquidation_account_registry,
        auto_discovery_enabled=lambda: panel.os.getenv(
            "LIQUIDATION_AUTO_DISCOVER_ACCOUNTS",
            "true",
        ).strip().lower() not in {"0", "false", "no"},
        discover_accounts=panel.discover_and_sync_liquidation_accounts,
        rpc_urls=panel.aave_rpc_urls,
        pool_address=panel.aave_pool_address,
        scan_account_health=panel.scan_account_health,
        record_scan_rows=panel.record_liquidation_health_scan_rows,
        error_message=panel._scan_error_message,
        display_limit=panel.liquidation_health_display_limit,
        watched_health_rows=panel.watched_health_rows,
        display_rows=panel.liquidation_health_display_rows,
        summary=panel.liquidation_health_summary,
        attach_scan_state=panel.liquidation_health_with_scan_state,
    )


def account_payload_runtime(panel):
    return panel.LiquidationAccountPayloadRuntime(
        is_bnb_venus_scope=panel._is_bnb_venus_scope,
        bnb_account_payload=panel.bnb_venus_account_payload,
        checksum_address=panel.Web3.to_checksum_address,
        scan_context_assets=panel.scan_context_assets,
        build_user_report=panel.build_user_liquidation_report,
        pool_address=panel.aave_pool_address,
        protocol_data_provider_address=panel.protocol_data_provider_address,
        liquidation_data_provider_address=panel.liquidation_data_provider_address,
        scan_config=panel.liquidation_scan_config,
        database_url=panel.database_url_or_none,
        record_account_scan=panel.record_liquidation_account_scan,
        prune_accounts=panel.db_prune_liquidation_accounts,
        retention_days=panel.liquidation_retention_days,
        market_config=panel.liquidation_market_config,
        load_account_snapshot=panel.db_load_liquidation_account_pool_snapshot,
    )


def page_value(panel, value: object, default: int = 1) -> int:
    return panel.page_value(value, default)


def page_size_value(panel, value: object, default: int = 20) -> int:
    return panel.page_size_value(value, default)


def pagination(panel, page: int, page_size: int, total_count: int) -> dict[str, int]:
    return panel.pagination(page, page_size, total_count)


def empty_tier_payload(panel, page_size: int = 20, *, high_page: int = 1, core_page: int = 1) -> dict:
    return panel.empty_tier_payload(page_size, high_page=high_page, core_page=core_page)


def database_block_reason(panel, configured: bool, error: str | None) -> str:
    return panel.database_block_reason(configured, error)


def database_next_action(panel, reason: str) -> str:
    return panel.database_next_action(reason)


def core_execution_summary(panel, row: dict, latest_attempt=None) -> dict:
    return panel.core_execution_summary(row, latest_attempt)


def parse_core_execution_time(panel, value: object):
    return panel.parse_core_execution_time(value)


def suppressed_payload(panel, payload: dict, ttl_seconds: float, *, cache_age_seconds: float) -> dict:
    return panel._borrow_pool_suppressed_payload(
        payload,
        cache=panel.LIQUIDATION_SCAN_CACHE,
        ttl_seconds=ttl_seconds,
        cache_age_seconds=cache_age_seconds,
    )


def health_payload(panel, force: bool = False) -> dict:
    return panel.build_liquidation_health_payload(
        runtime=health_runtime(panel),
        force=force,
    )


def account_payload(panel, account: str, *, market_id=None, chain_id=None) -> dict:
    return panel.build_liquidation_account_payload(
        account,
        runtime=account_payload_runtime(panel),
        market_id=market_id,
        chain_id=chain_id,
    )


def cached_account_payload(panel, account: str, *, market_id=None, chain_id=None) -> dict:
    return panel.build_liquidation_account_cached_payload(
        account,
        runtime=account_payload_runtime(panel),
        market_id=market_id,
        chain_id=chain_id,
    )
