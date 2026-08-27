from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network, chain_data_file_path
from intent_trade.chains.robinhood.flash_liquidity import cached_robinhood_flash_liquidity_snapshot
from intent_trade.flows.flash_loan_pools.presenter import (
    _as_float,
    _asset_row,
    _cache_identities,
    _cache_identity,
    _depth_counts,
    _dex_pool_row,
    _empty_protocol_view,
    _is_stable_symbol,
    _pool_view_capabilities,
    _protocol_display_name,
)
from web.control_panel_data_cache import read_json


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT = APP_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"


def _network_for_chain_id(chain_id: int | None) -> str:
    if chain_id is None:
        return ""
    try:
        row = chain_data_by_chain_id(int(chain_id)) or {}
    except Exception:
        row = {}
    return str(row.get("network") or "").strip().lower()


def _chain_id_for_network(network: str, default: int) -> int:
    try:
        row = chain_data_by_network(network) or {}
    except Exception:
        row = {}
    try:
        return int(row.get("chain_id") or default)
    except (TypeError, ValueError):
        return default

def _latest_robinhood_pool_static_table(evidence_root: Path | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    root = evidence_root
    if root is None:
        try:
            root = chain_data_file_path("robinhood", "dex_discovery") or DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT
        except Exception:
            root = DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT
    try:
        directories = sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    except OSError:
        return None, None
    expected_chain_id = _chain_id_for_network("robinhood", 4663)
    expected_network = _network_for_chain_id(expected_chain_id) or "robinhood"
    for directory in directories:
        payload = read_json(directory / "pool_static_table.json")
        if not payload:
            continue
        try:
            if int(payload.get("chainId") or payload.get("chain_id") or 0) != expected_chain_id:
                continue
        except (TypeError, ValueError):
            continue
        if str(payload.get("network") or "").strip().lower() != expected_network:
            continue
        return payload, directory / "pool_static_table.json"
    return None, None


def _robinhood_pool_has_verified_evidence(pool: dict[str, Any]) -> bool:
    if str(pool.get("status") or "").strip().lower() != "observable":
        return False
    if not str(pool.get("pool") or "").strip():
        return False
    evidence = pool.get("evidence") if isinstance(pool.get("evidence"), dict) else {}
    if not bool(evidence.get("poolCodeVerified")):
        return False
    if not (bool(evidence.get("eventVerified")) or bool(evidence.get("factoryQueryVerified"))):
        return False
    if evidence.get("poolStateVerified") is False and str(evidence.get("discoveryMethod") or "") != "factory_PoolCreated_event_scan":
        return False
    return True


def _robinhood_dex_pool_row(pool: dict[str, Any]) -> dict[str, Any]:
    row = _dex_pool_row(pool)
    evidence = pool.get("evidence") if isinstance(pool.get("evidence"), dict) else {}
    liquidity = pool.get("liquidity") or evidence.get("liquidity")
    row["a_token_total_supply"] = str(liquidity or "")
    if not _as_float(row.get("depth_score_usd")) and str(pool.get("status") or "").lower() == "observable":
        row["depth_score_usd"] = 1.0
        row["depth_level"] = "dust"
        row["depth_label"] = "可观察"
        row["depth_class"] = "ok"
    row["dex"] = "robinhood_uniswap_v3"
    row["quality"] = pool.get("status") or "observable"
    row["observed_block"] = evidence.get("blockNumber")
    row["borrowable"] = False
    row["flash_capable"] = True
    row["flash_borrowable"] = False
    row["flash_borrowable_reason"] = "pool_balance_unverified"
    row["pool_role"] = "observable_dex_pool"
    return row


def _robinhood_dex_protocol_view(
    protocol: str,
    *,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    cache, path = _latest_robinhood_pool_static_table(evidence_root)
    if not cache:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = "Robinhood DEX 池证据包尚未生成"
        return view
    pools = [
        pool
        for pool in cache.get("pools") or []
        if isinstance(pool, dict)
        and str(pool.get("protocol") or "").strip().lower() == protocol
        and _robinhood_pool_has_verified_evidence(pool)
    ]
    if not pools:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"Robinhood {_protocol_display_name(protocol)} 暂无可展示池证据"
        return view
    assets = [_robinhood_dex_pool_row(pool) for pool in pools]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: (str(item.get("token_symbol") or ""), str(item.get("fee") or "")))
    non_stable_assets.sort(key=lambda item: (str(item.get("token_symbol") or ""), str(item.get("fee") or "")))
    return {
        "protocol": protocol,
        "display_name": f"Robinhood {_protocol_display_name(protocol)}",
        "active": True,
        "display_mode": "observable_dex_pool",
        "pool_role_label": "可观察 DEX 池",
        "status_note": "Robinhood Uniswap V3 池合约具备 flash 协议能力；当前本地证据只验证到池发现，尚未验证余额/可借额度。",
        "cache": {
            "schema_version": None,
            "chain_id": cache.get("chainId") or cache.get("chain_id"),
            "block_number": max((int(((pool.get("evidence") or {}).get("blockNumber") or 0)) for pool in pools), default=None),
            "refreshed_at": cache.get("generatedAt"),
            "pool_address": "",
            "identities": _cache_identities(
                _cache_identity("factory", cache.get("factory"), label="Factory"),
            ),
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(assets),
                flash_capable=True,
                observable=True,
            ),
            "rpc_url": "",
            "asset_count": len(assets),
            "source": str(path or ""),
        },
        "summary": {
            "stable_borrow_pool_count": 0,
            "non_stable_borrow_pool_count": 0,
            "borrow_target_count": 0,
            "borrow_asset_count": 0,
            "observable_pool_count": len(assets),
            "stable_observable_pool_count": len(stable_assets),
            "non_stable_observable_pool_count": len(non_stable_assets),
            "flash_capable_pool_count": len(assets),
            "flashloan_borrowable_pool_count": 0,
            "flashloan_unverified_pool_count": len(assets),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": None,
    }


def _flash_source_pool_row(source: dict[str, Any]) -> dict[str, Any]:
    protocol = str(source.get("protocol") or "").strip().lower()
    token_symbol = str(source.get("token_symbol") or "").strip()
    source_kind = str(source.get("source_kind") or "").strip().lower()
    row = _asset_row(
        {
            "token_symbol": token_symbol,
            "binance_symbol": token_symbol.replace(":", "/"),
            "is_stable": bool(source.get("is_stable")) or _is_stable_symbol(token_symbol),
            "depth_score_usd": source.get("depth_score_usd") or 0,
            "available_liquidity": source.get("available_liquidity") or 0,
            "reserve_data_liquidity": source.get("reserve0") or source.get("reserve_data_liquidity") or 0,
            "a_token_total_supply": source.get("reserve1") or source.get("available_liquidity") or 0,
            "oracle_price": "",
            "token_address": source.get("token_address") or "",
            "pool_address": source.get("provider_address") if source_kind == "flash_swap" else "",
            "provider_address": source.get("provider_address") or "",
            "provider": source.get("provider") or "",
            "fee": source.get("fee_bps"),
            "dex": protocol,
            "quality": "borrowable" if source.get("flash_borrowable") else "unavailable",
            "source_kind": source_kind,
            "flash_capable": bool(source.get("flash_capable")),
            "flash_borrowable": bool(source.get("flash_borrowable")),
            "borrowable": bool(source.get("borrowable")),
            "repayment_model": source.get("repayment_model") or "",
            "adapter_hint": source.get("adapter_hint") or "",
        }
    )
    if source_kind == "flash_swap":
        token0 = str(source.get("token0_symbol") or "").strip()
        token1 = str(source.get("token1_symbol") or "").strip()
        row["is_stable"] = _is_stable_symbol(token0) or _is_stable_symbol(token1)
        row["token0Symbol"] = token0
        row["token1Symbol"] = token1
        row["token0_address"] = source.get("token0_address") or ""
        row["token1_address"] = source.get("token1_address") or ""
    return row


def _robinhood_flash_borrow_rows(protocol: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        if source.get("source_kind") == "flash_swap":
            for symbol_key, address_key, reserve_key in (
                ("token0_symbol", "token0_address", "reserve0"),
                ("token1_symbol", "token1_address", "reserve1"),
            ):
                symbol = str(source.get(symbol_key) or "").strip()
                if not symbol:
                    continue
                rows.append(
                    {
                        "borrow_symbol": symbol,
                        "target_count": 1 if _as_float(source.get(reserve_key)) > 0 else 0,
                        "target_symbols": [str(source.get("token_symbol") or "")],
                        "token_address": source.get(address_key) or "",
                        "provider_address": source.get("provider_address") or "",
                        "source_kind": source.get("source_kind") or "",
                    }
                )
            continue
        rows.append(
            {
                "borrow_symbol": source.get("token_symbol") or "",
                "target_count": 1 if source.get("flash_borrowable") else 0,
                "target_symbols": [source.get("display_name") or _protocol_display_name(protocol)],
                "token_address": source.get("token_address") or "",
                "provider_address": source.get("provider_address") or "",
                "source_kind": source.get("source_kind") or "",
            }
        )
    return rows


def _robinhood_flash_protocol_view(
    protocol: str,
    *,
    snapshot_loader: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    loader = snapshot_loader or cached_robinhood_flash_liquidity_snapshot
    try:
        snapshot = loader()
    except Exception as exc:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"Robinhood {_protocol_display_name(protocol)} 闪电流动性读取失败：{str(exc)[:160]}"
        return view
    sources = [
        source
        for source in snapshot.get("sources") or []
        if isinstance(source, dict) and str(source.get("protocol") or "").strip().lower() == protocol
    ]
    if not sources:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"Robinhood {_protocol_display_name(protocol)} 暂无可展示闪电资金来源"
        view["errors"] = snapshot.get("errors") or []
        return view
    assets = [_flash_source_pool_row(source) for source in sources]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    borrow_rows = _robinhood_flash_borrow_rows(protocol, sources)
    flash_borrowable_count = sum(1 for asset in assets if asset.get("flash_borrowable"))
    display_mode = "flash_swap_source" if any(source.get("source_kind") == "flash_swap" for source in sources) else "flash_loan_source"
    morpho_core = snapshot.get("morphoBlueCore")
    uniswap_factory = snapshot.get("uniswapV2Factory")
    identities = (
        _cache_identities(_cache_identity("core", morpho_core, label="Morpho Blue Core"))
        if protocol == "morpho_blue"
        else _cache_identities(_cache_identity("factory", uniswap_factory, label="Factory"))
    )
    return {
        "protocol": protocol,
        "display_name": f"Robinhood {_protocol_display_name(protocol)}",
        "active": True,
        "display_mode": display_mode,
        "pool_role_label": "Flash Swap 池" if display_mode == "flash_swap_source" else "Flash Loan 池",
        "status_note": "数据来自 rh-flash-toolkit 建模的只读链上读取；当前仍保持观察模式，不代表本项目执行器已开放广播。",
        "cache": {
            "schema_version": "robinhood-flash-liquidity-v1",
            "chain_id": snapshot.get("chainId"),
            "block_number": snapshot.get("blockNumber"),
            "refreshed_at": snapshot.get("generatedAt"),
            "pool_address": "",
            "identities": identities,
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(assets),
                borrowable=flash_borrowable_count > 0,
                flash_capable=True,
                priced_depth=any(float(asset.get("depth_score_usd") or 0) > 0 for asset in assets),
                observable=True,
            ),
            "rpc_url": snapshot.get("rpcUrl") or "",
            "asset_count": len(assets),
            "source": ", ".join(snapshot.get("evidence") or []),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": sum(1 for row in borrow_rows if int(row.get("target_count") or 0) > 0),
            "borrow_asset_count": len(borrow_rows),
            "flash_capable_pool_count": len(assets),
            "flashloan_borrowable_pool_count": flash_borrowable_count,
            "flashloan_unverified_pool_count": 0,
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": {
            "borrow_count": len(borrow_rows),
            "target_count": sum(1 for row in borrow_rows if int(row.get("target_count") or 0) > 0),
            "target_source": "rh-flash-toolkit readonly snapshot",
            "borrow_rows": borrow_rows,
        },
        "errors": snapshot.get("errors") or [],
    }


robinhood_dex_protocol_view = _robinhood_dex_protocol_view
robinhood_flash_protocol_view = _robinhood_flash_protocol_view

__all__ = ["robinhood_dex_protocol_view", "robinhood_flash_protocol_view"]
