from __future__ import annotations

from pathlib import Path
from typing import Any

from intent_trade.flows.flash_loan_pools.presenter import (
    _asset_row,
    _cache_identities,
    _cache_identity,
    _depth_counts,
    _empty_protocol_view,
    _pool_view_capabilities,
)
from web.control_panel_data_cache import borrow_target_universe, read_json


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AAVE_CACHE_PATH = APP_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"
DEFAULT_STABLE_TARGET_CACHE_PATH = APP_ROOT / "runtime" / "cache" / "dex_borrow_targets.json"


def aave_protocol_view(
    *,
    aave_cache_path: Path | None = DEFAULT_AAVE_CACHE_PATH,
    stable_target_cache_path: Path | None = DEFAULT_STABLE_TARGET_CACHE_PATH,
    chain_id: int | None = None,
) -> dict[str, Any]:
    cache = read_json(aave_cache_path) if aave_cache_path else None
    if not cache:
        view = _empty_protocol_view("aave_v3", status="protocol_pool_cache_not_available")
        view["status_message"] = "Aave V3 闪电贷可用资金池缓存尚未生成"
        return view
    cache_chain_id = (cache or {}).get("chain_id")
    if chain_id is not None and cache_chain_id is not None and int(cache_chain_id) != int(chain_id):
        view = _empty_protocol_view("aave_v3", status="protocol_pool_cache_not_available")
        view["status_message"] = "当前链的 Aave V3 闪电贷可用资金池缓存尚未接入"
        return view
    assets = [_asset_row(asset) for asset in (cache or {}).get("assets") or [] if isinstance(asset, dict)]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    universe = (
        borrow_target_universe(aave_cache_path, stable_target_cache_path)
        if aave_cache_path and stable_target_cache_path
        else None
    )
    pool_address = (cache or {}).get("pool_address")
    return {
        "protocol": "aave_v3",
        "display_name": "Aave V3",
        "active": True,
        "cache": {
            "schema_version": (cache or {}).get("schema_version"),
            "chain_id": (cache or {}).get("chain_id"),
            "block_number": (cache or {}).get("block_number"),
            "refreshed_at": (cache or {}).get("refreshed_at"),
            "pool_address": pool_address,
            "identities": _cache_identities(
                _cache_identity("aave_pool", pool_address, label="Aave Pool", is_concrete_pool=True),
            ),
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=1 if pool_address else len(assets),
                borrowable=True,
                flash_capable=True,
                priced_depth=any(float(asset.get("depth_score_usd") or 0) > 0 for asset in assets),
                observable=True,
            ),
            "rpc_url": (cache or {}).get("rpc_url"),
            "asset_count": len(assets),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": (universe or {}).get("target_count", 0),
            "borrow_asset_count": (universe or {}).get("borrow_count", len(assets)),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": universe,
    }


__all__ = ["aave_protocol_view"]
