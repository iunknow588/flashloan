from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiquidationAccountPayloadRuntime:
    is_bnb_venus_scope: Any
    bnb_account_payload: Any
    checksum_address: Any
    scan_context_assets: Any
    build_user_report: Any
    pool_address: Any
    protocol_data_provider_address: Any
    liquidation_data_provider_address: Any
    scan_config: Any
    database_url: Any
    record_account_scan: Any
    prune_accounts: Any
    retention_days: Any
    market_config: Any
    load_account_snapshot: Any


def liquidation_account_payload(
    account: str,
    *,
    runtime: LiquidationAccountPayloadRuntime,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    if runtime.is_bnb_venus_scope(market_id, chain_id):
        return runtime.bnb_account_payload(account)
    raw_account = account.strip()
    if not raw_account:
        raise ValueError("account is required")
    checksum = runtime.checksum_address(raw_account)
    rpc_url, reserve_assets, asset_error = runtime.scan_context_assets()
    if not rpc_url:
        raise RuntimeError(asset_error or "unable to resolve rpc_url")
    report = runtime.build_user_report(
        checksum,
        rpc_url,
        runtime.pool_address(),
        reserve_assets,
        runtime.protocol_data_provider_address(),
        runtime.liquidation_data_provider_address(),
        runtime.scan_config(),
    )
    try:
        database_url = runtime.database_url()
        if database_url:
            runtime.record_account_scan(database_url, report)
            runtime.prune_accounts(database_url, retained_days=runtime.retention_days())
    except Exception as exc:
        report.setdefault("warnings", []).append({
            "code": "account_scan_persistence_failed",
            "message": str(exc),
        })
    report["context"] = {
        "rpc_url": rpc_url,
        "pool_address": runtime.pool_address(),
        "protocol_data_provider_address": runtime.protocol_data_provider_address() or None,
        "liquidation_data_provider_address": runtime.liquidation_data_provider_address() or None,
        "reserve_asset_count": len(reserve_assets),
        "error": asset_error,
        "market": runtime.market_config().as_dict(),
    }
    return report


def liquidation_account_cached_payload(
    account: str,
    *,
    runtime: LiquidationAccountPayloadRuntime,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    checksum = runtime.checksum_address(str(account or "").strip())
    if runtime.is_bnb_venus_scope(market_id, chain_id):
        return runtime.bnb_account_payload(checksum)
    database_url = runtime.database_url()
    if not database_url:
        return {
            "found": False,
            "cached": True,
            "account": checksum,
            "error": "local KV store unavailable",
        }
    return runtime.load_account_snapshot(
        database_url,
        checksum,
        market_id=market_id,
        chain_id=chain_id,
    )
