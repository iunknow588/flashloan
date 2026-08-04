from web import control_panel_liquidation_audit as liquidation_audit_module
from web import control_panel_liquidation_base as liquidation_base_module
from web import control_panel_liquidation_execute as liquidation_execute_module
from web import control_panel_liquidation_scan as liquidation_scan_module
from web import control_panel_market as market_panel_module

_base_load_liquidation_account_registry = liquidation_base_module.load_liquidation_account_registry
_base_liquidation_account_registry_window = liquidation_base_module.liquidation_account_registry_window
_base_schema_status_payload = liquidation_base_module.schema_status_payload
_base_liquidation_discovery_progress = liquidation_base_module.liquidation_discovery_progress
_base_liquidation_discovery_window = liquidation_base_module.liquidation_discovery_window
_base_liquidation_config_health = liquidation_base_module.liquidation_config_health
_base_liquidation_scan_config_library = liquidation_base_module.liquidation_scan_config_library
_audit_recent_liquidation_execution_attempts = liquidation_audit_module.recent_liquidation_execution_attempts
_audit_recent_liquidation_failure_samples = liquidation_audit_module.recent_liquidation_failure_samples
_audit_liquidation_execution_attempts_for_account = liquidation_audit_module.liquidation_execution_attempts_for_account
_audit_liquidation_failure_samples_for_account = liquidation_audit_module.liquidation_failure_samples_for_account
_audit_record_liquidation_execution_attempt_safely = liquidation_audit_module.record_liquidation_execution_attempt_safely
_audit_liquidation_pause_guard_status = liquidation_audit_module.liquidation_pause_guard_status
_audit_clear_liquidation_pause_guard_status = liquidation_audit_module.clear_liquidation_pause_guard_status
_scan_discover_and_sync_liquidation_accounts = liquidation_scan_module.discover_and_sync_liquidation_accounts
_scan_liquidation_borrow_pool_payload = liquidation_scan_module.liquidation_borrow_pool_payload
_scan_liquidation_borrow_pool_scan_payload = liquidation_scan_module.liquidation_borrow_pool_scan_payload
_scan_liquidation_health_payload = liquidation_scan_module.liquidation_health_payload
_scan_liquidation_account_payload = liquidation_scan_module.liquidation_account_payload
_scan_liquidation_account_cached_payload = liquidation_scan_module.liquidation_account_cached_payload
_scan_liquidation_account_tier_summary = liquidation_scan_module.liquidation_account_tier_summary
_scan_liquidation_core_rows_with_execution = liquidation_scan_module.liquidation_core_rows_with_execution
_execute_liquidation_execution_payload_for_account = liquidation_execute_module.liquidation_execution_payload_for_account
_execute_simulate_liquidation_static_call = liquidation_execute_module.simulate_liquidation_static_call
_execute_flashloan_liquidation_transaction = liquidation_execute_module.execute_flashloan_liquidation_transaction
_execute_self_funded_liquidation_transaction = liquidation_execute_module.execute_self_funded_liquidation_transaction

CONTEXT_NAMES = [
    "AAVE_RESERVE_CACHE_PATH",
    "LIQUIDATION_ACCOUNT_CACHE",
    "LIQUIDATION_ACCOUNTS_PATH",
    "LIQUIDATION_CONFIG_PATH",
    "LIQUIDATION_DISCOVERY_CACHE",
    "LIQUIDATION_DISCOVERY_LOCK",
    "LIQUIDATION_PAUSE_GUARD_PATH",
    "LIQUIDATION_SAMPLE_LIBRARY_PATH",
    "LIQUIDATION_SCAN_CACHE",
    "LIQUIDATION_SCAN_LOCK",
    "aave_rpc_urls",
    "aave_pool_address",
    "build_user_liquidation_report",
    "database_url_or_none",
    "db_liquidation_account_registry_stats",
    "db_liquidation_discovery_scan_progress",
    "db_load_liquidation_borrow_health_scan_batches",
    "db_load_liquidation_accounts",
    "db_load_liquidation_accounts_page",
    "db_load_liquidation_borrow_health_pool",
    "db_load_liquidation_core_opportunity_pool",
    "db_load_liquidation_high_frequency_pool",
    "db_load_liquidation_scan_config_library",
    "db_prune_liquidation_accounts",
    "db_record_liquidation_borrow_health_scan_batch",
    "db_sync_liquidation_borrow_health_pool",
    "db_upsert_liquidation_accounts",
    "dex_router_address",
    "discover_borrower_addresses",
    "ensure_database_schema",
    "liquidation_account_payload",
    "liquidation_account_cached_payload",
    "liquidation_account_tier_summary",
    "liquidation_account_registry_window",
    "liquidation_borrow_pool_display_limit",
    "liquidation_borrow_pool_payload",
    "liquidation_borrow_pool_scan_payload",
    "liquidation_config_health",
    "liquidation_market_payload",
    "liquidation_scan_config_library",
    "liquidation_data_provider_address",
    "liquidation_discovery_progress",
    "liquidation_discovery_window",
    "liquidation_executor_address",
    "liquidation_executor_owner_address",
    "liquidation_executor_private_key",
    "liquidation_execution_controls",
    "liquidation_pause_guard_status",
    "clear_liquidation_pause_guard_status",
    "liquidation_scan_config",
    "liquidation_scan_refresh_profile",
    "liquidation_scan_version",
    "liquidation_self_funded_private_key",
    "load_liquidation_account_registry",
    "load_reserve_assets_for_scan",
    "protocol_data_provider_address",
    "recent_liquidation_execution_attempts",
    "recent_liquidation_failure_samples",
    "record_liquidation_account_scan",
    "liquidation_execution_attempts_for_account",
    "liquidation_failure_samples_for_account",
    "record_liquidation_health_scan_rows",
    "record_liquidation_execution_attempt_safely",
    "record_liquidation_discovery_window",
    "resolve_discovery_block_range",
    "scan_account_health",
    "scan_context_assets",
    "sync_liquidation_accounts_to_database",
]


def install_liquidation_context(panel) -> None:
    def sync_liquidation_module_context() -> None:
        for module in (
            liquidation_base_module,
            liquidation_audit_module,
            liquidation_scan_module,
            liquidation_execute_module,
            market_panel_module,
        ):
            for name in CONTEXT_NAMES:
                if hasattr(panel, name):
                    setattr(module, name, getattr(panel, name))

    def load_liquidation_account_registry(force: bool = False) -> tuple[list[str], str]:
        sync_liquidation_module_context()
        return _base_load_liquidation_account_registry(force=force)

    def liquidation_account_registry_window(market_id: str | None = None, chain_id: int | None = None) -> dict:
        sync_liquidation_module_context()
        return _base_liquidation_account_registry_window(market_id=market_id, chain_id=chain_id)

    def schema_status_payload() -> dict:
        sync_liquidation_module_context()
        return _base_schema_status_payload()

    def liquidation_discovery_progress(pool_address: str, *, market_id: str | None = None, chain_id: int | None = None) -> dict:
        sync_liquidation_module_context()
        return _base_liquidation_discovery_progress(pool_address, market_id=market_id, chain_id=chain_id)

    def liquidation_discovery_window(force_full: bool = False):
        sync_liquidation_module_context()
        return _base_liquidation_discovery_window(force_full=force_full)

    def liquidation_config_health(chain_id: int | None = None) -> dict:
        sync_liquidation_module_context()
        return _base_liquidation_config_health(chain_id=chain_id)

    def liquidation_scan_config_library(limit: int = 100, *, market_id: str | None = None, chain_id: int | None = None) -> dict:
        sync_liquidation_module_context()
        return _base_liquidation_scan_config_library(limit=limit, market_id=market_id, chain_id=chain_id)

    def recent_liquidation_execution_attempts(limit: int = 20, *, market_id: str | None = None, chain_id: int | None = None) -> dict:
        sync_liquidation_module_context()
        return _audit_recent_liquidation_execution_attempts(limit=limit, market_id=market_id, chain_id=chain_id)

    def recent_liquidation_failure_samples(limit: int = 20, *, market_id: str | None = None, chain_id: int | None = None) -> dict:
        sync_liquidation_module_context()
        return _audit_recent_liquidation_failure_samples(limit=limit, market_id=market_id, chain_id=chain_id)

    def liquidation_account_tier_summary() -> dict:
        sync_liquidation_module_context()
        return _scan_liquidation_account_tier_summary()

    def liquidation_core_rows_with_execution(database_url: str, limit: int, offset: int = 0) -> list[dict]:
        sync_liquidation_module_context()
        return _scan_liquidation_core_rows_with_execution(database_url, limit=limit, offset=offset)

    def liquidation_execution_attempts_for_account(
        account: str,
        limit: int = 20,
        *,
        market_id: str | None = None,
        chain_id: int | None = None,
    ) -> dict:
        sync_liquidation_module_context()
        return _audit_liquidation_execution_attempts_for_account(
            account,
            limit=limit,
            market_id=market_id,
            chain_id=chain_id,
        )

    def liquidation_failure_samples_for_account(
        account: str,
        limit: int = 20,
        *,
        market_id: str | None = None,
        chain_id: int | None = None,
    ) -> dict:
        sync_liquidation_module_context()
        return _audit_liquidation_failure_samples_for_account(
            account,
            limit=limit,
            market_id=market_id,
            chain_id=chain_id,
        )

    def record_liquidation_execution_attempt_safely(**kwargs):
        sync_liquidation_module_context()
        return _audit_record_liquidation_execution_attempt_safely(**kwargs)

    def liquidation_pause_guard_status() -> dict:
        sync_liquidation_module_context()
        return _audit_liquidation_pause_guard_status()

    def clear_liquidation_pause_guard_status() -> dict:
        sync_liquidation_module_context()
        return _audit_clear_liquidation_pause_guard_status()

    def discover_and_sync_liquidation_accounts(force_full: bool = False) -> dict:
        sync_liquidation_module_context()
        return _scan_discover_and_sync_liquidation_accounts(force_full=force_full)

    def liquidation_borrow_pool_payload(*args, **kwargs) -> dict:
        sync_liquidation_module_context()
        return _scan_liquidation_borrow_pool_payload(*args, **kwargs)

    def liquidation_borrow_pool_scan_payload(force: bool = False, *args, **kwargs) -> dict:
        sync_liquidation_module_context()
        return _scan_liquidation_borrow_pool_scan_payload(force=force, *args, **kwargs)

    def liquidation_health_payload(force: bool = False) -> dict:
        sync_liquidation_module_context()
        return _scan_liquidation_health_payload(force=force)

    def liquidation_account_payload(account: str) -> dict:
        sync_liquidation_module_context()
        return _scan_liquidation_account_payload(account)

    def liquidation_account_cached_payload(account: str, *, market_id: str | None = None, chain_id: int | None = None) -> dict:
        sync_liquidation_module_context()
        return _scan_liquidation_account_cached_payload(account, market_id=market_id, chain_id=chain_id)

    def liquidation_execution_payload_for_account(account: str, **kwargs) -> dict:
        sync_liquidation_module_context()
        return _execute_liquidation_execution_payload_for_account(account, **kwargs)

    def simulate_liquidation_static_call(payload: dict) -> dict:
        sync_liquidation_module_context()
        return _execute_simulate_liquidation_static_call(payload)

    def execute_flashloan_liquidation_transaction(payload: dict, force: bool = False) -> dict:
        sync_liquidation_module_context()
        return _execute_flashloan_liquidation_transaction(payload, force=force)

    def execute_self_funded_liquidation_transaction(payload: dict, force: bool = False) -> dict:
        sync_liquidation_module_context()
        return _execute_self_funded_liquidation_transaction(payload, force=force)

    panel.sync_liquidation_module_context = sync_liquidation_module_context
    panel.load_liquidation_account_registry = load_liquidation_account_registry
    panel.liquidation_account_registry_window = liquidation_account_registry_window
    panel.schema_status_payload = schema_status_payload
    panel.liquidation_discovery_progress = liquidation_discovery_progress
    panel.liquidation_discovery_window = liquidation_discovery_window
    panel.liquidation_config_health = liquidation_config_health
    panel.liquidation_scan_config_library = liquidation_scan_config_library
    panel.recent_liquidation_execution_attempts = recent_liquidation_execution_attempts
    panel.recent_liquidation_failure_samples = recent_liquidation_failure_samples
    panel.liquidation_account_tier_summary = liquidation_account_tier_summary
    panel.liquidation_core_rows_with_execution = liquidation_core_rows_with_execution
    panel.liquidation_execution_attempts_for_account = liquidation_execution_attempts_for_account
    panel.liquidation_failure_samples_for_account = liquidation_failure_samples_for_account
    panel.record_liquidation_execution_attempt_safely = record_liquidation_execution_attempt_safely
    panel.liquidation_pause_guard_status = liquidation_pause_guard_status
    panel.clear_liquidation_pause_guard_status = clear_liquidation_pause_guard_status
    panel.discover_and_sync_liquidation_accounts = discover_and_sync_liquidation_accounts
    panel.liquidation_borrow_pool_payload = liquidation_borrow_pool_payload
    panel.liquidation_borrow_pool_scan_payload = liquidation_borrow_pool_scan_payload
    panel.liquidation_health_payload = liquidation_health_payload
    panel.liquidation_account_payload = liquidation_account_payload
    panel.liquidation_account_cached_payload = liquidation_account_cached_payload
    panel.liquidation_execution_payload_for_account = liquidation_execution_payload_for_account
    panel.simulate_liquidation_static_call = simulate_liquidation_static_call
    panel.execute_flashloan_liquidation_transaction = execute_flashloan_liquidation_transaction
    panel.execute_self_funded_liquidation_transaction = execute_self_funded_liquidation_transaction
    sync_liquidation_module_context()
