from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from core.flash_loan_pool_discovery import cached_robinhood_flash_liquidity_snapshot
from core.market_config import PROTOCOL_CAPABILITIES, supported_market_summaries
from web.control_panel_data_cache import borrow_target_universe, read_json


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"
DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH = REPO_ROOT / "data_info" / "BNB" / "liquidation" / "venus_market_static_table.json"
DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT = APP_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"
DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"

LENDING_PROTOCOLS = {"aave_v3", "hyper_evm_aave_v3", "venus_core", "morpho_blue"}
DEX_FLASH_PROTOCOLS = {
    "uniswap_v2",
    "uniswap_v3",
    "pancakeswap_v2",
    "pancakeswap_v3",
    "biswap_v2",
    "quickswap_v2",
    "traderjoe_v2",
    "curve",
    "balancer_v2",
}
EXECUTION_RAIL_PROTOCOLS = {"cow_protocol", "hyperliquid_native"}
ROUTE_ONLY_PROTOCOLS = {"hyperliquid_native"}
CHAIN_DEFAULT_PROTOCOL_PREFERENCES = {
    56: ["venus_core", "pancakeswap_v3", "pancakeswap_v2", "aave_v3"],
    43114: ["aave_v3", "uniswap_v3", "traderjoe_v2"],
    4663: ["morpho_blue", "uniswap_v2", "uniswap_v3", "aave_v3"],
}
STABLE_SYMBOLS = {
    "AUSD",
    "BUSD",
    "DAI",
    "DAI.E",
    "EURC",
    "FDUSD",
    "FRAX",
    "FRAX.E",
    "GHO",
    "MIM",
    "SUSDE",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDT",
    "USDT.E",
}
AVALANCHE_V2_DEX_PROTOCOLS = {
    "trader_joe_v1": "traderjoe_v2",
    "trader_joe_v2": "traderjoe_v2",
    "traderjoe_v2": "traderjoe_v2",
}


def _depth_level(depth_score_usd: object) -> dict[str, str]:
    try:
        depth = float(depth_score_usd or 0)
    except (TypeError, ValueError):
        depth = 0.0
    if depth >= 1_000_000:
        return {"level": "deep", "label": "深", "class": "good"}
    if depth >= 100_000:
        return {"level": "medium", "label": "中", "class": "ok"}
    if depth >= 10_000:
        return {"level": "shallow", "label": "浅", "class": "warn"}
    if depth > 0:
        return {"level": "dust", "label": "极浅", "class": "warn"}
    return {"level": "unavailable", "label": "不可用", "class": "bad"}


def _asset_row(asset: dict[str, Any]) -> dict[str, Any]:
    depth = _depth_level(asset.get("depth_score_usd"))
    return {
        **asset,
        "depth_level": depth["level"],
        "depth_label": depth["label"],
        "depth_class": depth["class"],
        "liquidity_units": max(
            int(asset.get("available_liquidity") or 0),
            int(asset.get("reserve_data_liquidity") or 0),
            int(asset.get("a_token_total_supply") or 0),
        ),
    }


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else default


def _unit_amount(raw: object, decimals: object) -> float:
    value = _as_float(raw)
    try:
        scale = 10 ** int(decimals or 0)
    except (TypeError, ValueError, OverflowError):
        scale = 1
    return value / scale if scale else value


def _is_stable_symbol(symbol: object) -> bool:
    normalized = str(symbol or "").strip().upper()
    bridge_base = normalized[:-2] if normalized.endswith(".E") else normalized
    return normalized in STABLE_SYMBOLS or bridge_base in STABLE_SYMBOLS or normalized.endswith("USD")


def _depth_counts(assets: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for asset in assets:
        counts[asset["depth_level"]] = counts.get(asset["depth_level"], 0) + 1
    return counts


def _aave_protocol_view(
    *,
    aave_cache_path: Path | None,
    stable_target_cache_path: Path | None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    cache = read_json(aave_cache_path) if aave_cache_path else None
    if not cache:
        view = _empty_protocol_view("aave_v3", status="protocol_pool_cache_not_available")
        view["status_message"] = "Aave V3 资金池缓存尚未生成"
        return view
    cache_chain_id = (cache or {}).get("chain_id")
    if chain_id is not None and cache_chain_id is not None and int(cache_chain_id) != int(chain_id):
        view = _empty_protocol_view("aave_v3", status="protocol_pool_cache_not_available")
        view["status_message"] = "当前链的 Aave V3 资金池缓存尚未接入"
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
    return {
        "protocol": "aave_v3",
        "display_name": "Aave V3",
        "active": True,
        "cache": {
            "schema_version": (cache or {}).get("schema_version"),
            "chain_id": (cache or {}).get("chain_id"),
            "block_number": (cache or {}).get("block_number"),
            "refreshed_at": (cache or {}).get("refreshed_at"),
            "pool_address": (cache or {}).get("pool_address"),
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


def _venus_market_row(market: dict[str, Any]) -> dict[str, Any]:
    symbol = str(market.get("underlying_symbol") or market.get("vtoken_symbol") or "").strip()
    decimals = market.get("underlying_decimals")
    cash_units = _unit_amount(market.get("cash"), decimals)
    borrow_units = _unit_amount(market.get("total_borrows"), decimals)
    supply_units = _unit_amount(market.get("total_supply"), market.get("vtoken_decimals"))
    price = _as_float(market.get("oracle_price_usd") or market.get("oracle_price"))
    depth_score_usd = cash_units * price
    return _asset_row(
        {
            "token_symbol": symbol,
            "binance_symbol": f"{symbol.upper()}USDT" if symbol else "",
            "is_stable": bool(market.get("stable")) or _is_stable_symbol(symbol),
            "depth_score_usd": depth_score_usd,
            "available_liquidity": cash_units,
            "reserve_data_liquidity": borrow_units,
            "a_token_total_supply": supply_units,
            "oracle_price": price,
            "token_address": market.get("underlying_address") or "",
            "pool_address": market.get("vtoken_address") or "",
            "vtoken_symbol": market.get("vtoken_symbol") or "",
            "borrow_disabled": market.get("borrow_disabled"),
            "collateral_disabled": market.get("collateral_disabled"),
        }
    )


def _venus_protocol_view(
    *,
    chain_id: int | None,
    venus_market_path: Path | None = None,
) -> dict[str, Any]:
    path = venus_market_path or DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH
    cache = read_json(path)
    if not cache:
        view = _empty_protocol_view("venus_core", status="protocol_pool_cache_not_available")
        view["status_message"] = "Venus Core 市场静态表尚未生成"
        return view
    cache_chain_id = cache.get("chain_id") or cache.get("chainId")
    if chain_id is not None and cache_chain_id is not None and int(cache_chain_id) != int(chain_id):
        view = _empty_protocol_view("venus_core", status="protocol_pool_cache_not_available")
        view["status_message"] = "当前链的 Venus Core 市场静态表尚未接入"
        return view
    assets = [
        _venus_market_row(market)
        for market in cache.get("markets") or []
        if isinstance(market, dict) and market.get("is_listed") is not False
    ]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    return {
        "protocol": "venus_core",
        "display_name": "Venus Core",
        "active": True,
        "cache": {
            "schema_version": None,
            "chain_id": cache_chain_id,
            "block_number": cache.get("block_number"),
            "refreshed_at": cache.get("generated_at"),
            "pool_address": cache.get("comptroller_address"),
            "rpc_url": cache.get("rpc_url"),
            "asset_count": len(assets),
            "source": str(path),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": 0,
            "borrow_asset_count": len(assets),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": None,
    }


def _dex_pool_row(pool: dict[str, Any]) -> dict[str, Any]:
    token0 = str(pool.get("token0Symbol") or "").strip()
    token1 = str(pool.get("token1Symbol") or "").strip()
    pair = str(pool.get("symbolPair") or ":".join([token0, token1])).strip(":")
    tvl = _as_float(pool.get("tvlUsd"))
    is_stable_pair = _is_stable_symbol(token0) or _is_stable_symbol(token1)
    row = _asset_row(
        {
            "token_symbol": pair,
            "binance_symbol": pair.replace(":", "/"),
            "is_stable": is_stable_pair,
            "depth_score_usd": tvl,
            "available_liquidity": tvl,
            "reserve_data_liquidity": _as_float(pool.get("token0Usd")) + _as_float(pool.get("token1Usd")),
            "a_token_total_supply": _as_float(pool.get("liquidity")),
            "oracle_price": "",
            "token_address": pool.get("pool") or "",
            "pool_address": pool.get("pool") or "",
            "fee": pool.get("fee") or "",
            "dex": pool.get("dex") or "",
            "quality": pool.get("status") or pool.get("quality") or "",
            "token0Symbol": token0,
            "token1Symbol": token1,
        }
    )
    row["depth_label"] = row.get("depth_label") or ("可用" if tvl > 0 else "不可用")
    return row


def _latest_robinhood_pool_static_table(evidence_root: Path | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    root = evidence_root or DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT
    try:
        directories = sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    except OSError:
        return None, None
    for directory in directories:
        payload = read_json(directory / "pool_static_table.json")
        if not payload:
            continue
        try:
            if int(payload.get("chainId") or payload.get("chain_id") or 0) != 4663:
                continue
        except (TypeError, ValueError):
            continue
        if str(payload.get("network") or "").strip().lower() != "robinhood":
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
            "pool_address": cache.get("factory") or "",
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
            "pool_address": source.get("provider_address") or "",
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
            "pool_address": snapshot.get("morphoBlueCore") if protocol == "morpho_blue" else snapshot.get("uniswapV2Factory"),
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


def _avalanche_v3_pool_rows(cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pair in cache.get("pools") or []:
        if not isinstance(pair, dict):
            continue
        for pool in pair.get("pools") or pair.get("executionPools") or []:
            if not isinstance(pool, dict):
                continue
            pool_address = str(pool.get("pool") or "").strip()
            if not pool_address:
                continue
            fee = pool.get("fee")
            key = f"{pool_address.lower()}:{fee}"
            if key in seen:
                continue
            seen.add(key)
            token0 = str(pool.get("token0Symbol") or pool.get("token0_symbol") or "").strip()
            token1 = str(pool.get("token1Symbol") or pool.get("token1_symbol") or "").strip()
            rows.append(
                {
                    **pool,
                    "dex": "uniswap_v3",
                    "protocol": "uniswap_v3",
                    "pool": pool_address,
                    "symbolPair": pool.get("symbolPair") or ":".join(part for part in (token0, token1) if part),
                    "token0Symbol": token0,
                    "token1Symbol": token1,
                    "token0Usd": pool.get("token0Usd"),
                    "token1Usd": pool.get("token1Usd"),
                    "tvlUsd": pool.get("tvlUsd"),
                    "status": pool.get("status") or pool.get("quality") or "observable",
                }
            )
    return rows


def _avalanche_v2_pool_rows(cache: dict[str, Any], protocol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool in cache.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        mapped_protocol = AVALANCHE_V2_DEX_PROTOCOLS.get(str(pool.get("dex") or "").strip().lower())
        if mapped_protocol != protocol:
            continue
        rows.append(
            {
                **pool,
                "dex": protocol,
                "protocol": protocol,
                "fee": pool.get("fee") or pool.get("feeBps"),
                "status": pool.get("status") or "observable",
            }
        )
    return rows


def _pool_rows_protocol_view(
    protocol: str,
    *,
    cache: dict[str, Any],
    path: Path,
    rows: list[dict[str, Any]],
    target_source: str,
) -> dict[str, Any]:
    if not rows:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"{_protocol_display_name(protocol)} 暂无静态池缓存"
        return view
    assets = [_dex_pool_row(pool) for pool in rows]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    recommended = sum(1 for pool in rows if str(pool.get("status") or pool.get("quality") or "").lower() == "recommended")
    return {
        "protocol": protocol,
        "display_name": _protocol_display_name(protocol),
        "active": True,
        "cache": {
            "schema_version": cache.get("schemaVersion") or cache.get("schema_version"),
            "chain_id": cache.get("chainId") or cache.get("chain_id"),
            "block_number": cache.get("generatedBlockNumber") or cache.get("block_number"),
            "refreshed_at": cache.get("generatedAt") or cache.get("fetched_at"),
            "pool_address": cache.get("router") or cache.get("factory") or "",
            "rpc_url": cache.get("rpcUrl") or cache.get("rpc_url"),
            "asset_count": len(assets),
            "source": str(path),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": recommended,
            "borrow_asset_count": len(assets),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": {
            "borrow_count": len(assets),
            "target_count": recommended,
            "target_source": target_source,
            "borrow_rows": [
                {
                    "borrow_symbol": asset.get("token_symbol"),
                    "target_count": 1 if str(asset.get("quality") or "").lower() == "recommended" else 0,
                    "target_symbols": [asset.get("binance_symbol")] if asset.get("binance_symbol") else [],
                }
                for asset in assets[:80]
            ],
        },
    }


def _avalanche_dex_protocol_view(
    protocol: str,
    *,
    avalanche_v3_pool_static_path: Path | None = None,
    avalanche_v2_pool_discovery_path: Path | None = None,
) -> dict[str, Any]:
    if protocol == "uniswap_v3":
        path = avalanche_v3_pool_static_path or DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH
        cache = read_json(path)
        if not cache:
            return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        return _pool_rows_protocol_view(
            protocol,
            cache=cache,
            path=path,
            rows=_avalanche_v3_pool_rows(cache),
            target_source="avalanche_l1_pool_static_table",
        )
    if protocol == "traderjoe_v2":
        path = avalanche_v2_pool_discovery_path or DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH
        cache = read_json(path)
        if not cache:
            return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        return _pool_rows_protocol_view(
            protocol,
            cache=cache,
            path=path,
            rows=_avalanche_v2_pool_rows(cache, protocol),
            target_source="avalanche_multi_dex_v2_pool_discovery",
        )
    return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")


def _dex_protocol_view(
    protocol: str,
    *,
    chain_id: int | None,
    dex_pool_static_path: Path | None = None,
    robinhood_evidence_root: Path | None = None,
    robinhood_flash_liquidity_loader: Callable[[], dict[str, Any]] | None = None,
    avalanche_v3_pool_static_path: Path | None = None,
    avalanche_v2_pool_discovery_path: Path | None = None,
) -> dict[str, Any]:
    if chain_id == 4663:
        if protocol == "uniswap_v2":
            return _robinhood_flash_protocol_view(
                protocol,
                snapshot_loader=robinhood_flash_liquidity_loader,
            )
        return _robinhood_dex_protocol_view(protocol, evidence_root=robinhood_evidence_root)
    if chain_id == 43114:
        return _avalanche_dex_protocol_view(
            protocol,
            avalanche_v3_pool_static_path=avalanche_v3_pool_static_path,
            avalanche_v2_pool_discovery_path=avalanche_v2_pool_discovery_path,
        )
    path = dex_pool_static_path or DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH
    cache = read_json(path)
    if not cache:
        return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
    cache_chain_id = cache.get("chainId") or cache.get("chain_id")
    if chain_id is not None and cache_chain_id is not None and int(cache_chain_id) != int(chain_id):
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = "当前链的 DEX 静态池表尚未接入"
        return view
    pools = [
        pool
        for pool in cache.get("pools") or []
        if isinstance(pool, dict) and str(pool.get("dex") or "").strip().lower() == protocol
    ]
    if not pools:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"{_protocol_display_name(protocol)} 暂无静态池缓存"
        return view
    assets = [_dex_pool_row(pool) for pool in pools]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    recommended = sum(1 for pool in pools if str(pool.get("status") or "").lower() == "recommended")
    return {
        "protocol": protocol,
        "display_name": _protocol_display_name(protocol),
        "active": True,
        "cache": {
            "schema_version": None,
            "chain_id": cache_chain_id,
            "block_number": cache.get("generatedBlockNumber"),
            "refreshed_at": cache.get("generatedAt"),
            "pool_address": cache.get("router") or "",
            "rpc_url": cache.get("rpcUrl"),
            "asset_count": len(assets),
            "source": str(path),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": recommended,
            "borrow_asset_count": len(assets),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": {
            "borrow_count": len(assets),
            "target_count": recommended,
            "target_source": "bnb_arbitrage_pool_static_table",
            "borrow_rows": [
                {
                    "borrow_symbol": asset.get("token_symbol"),
                    "target_count": 1 if str(asset.get("quality") or "").lower() == "recommended" else 0,
                    "target_symbols": [asset.get("binance_symbol")] if asset.get("binance_symbol") else [],
                }
                for asset in assets[:80]
            ],
        },
    }


def _empty_protocol_view(protocol: str, *, status: str = "protocol_pool_cache_not_available") -> dict[str, Any]:
    messages = {
        "chain_protocol_not_supported": "当前链不支持该闪电贷协议",
        "protocol_pool_cache_not_available": "该协议资金池缓存尚未接入",
    }
    return {
        "protocol": protocol,
        "display_name": _protocol_display_name(protocol),
        "active": True,
        "cache": {
            "schema_version": None,
            "chain_id": None,
            "block_number": None,
            "refreshed_at": None,
            "pool_address": "",
            "rpc_url": "",
            "asset_count": 0,
        },
        "summary": {
            "stable_borrow_pool_count": 0,
            "non_stable_borrow_pool_count": 0,
            "borrow_target_count": 0,
            "borrow_asset_count": 0,
            "depth_counts": {},
        },
        "stable_borrow_pools": [],
        "non_stable_borrow_pools": [],
        "borrow_target_universe": None,
        "status": status,
        "status_message": messages.get(status, "该协议资金池暂不可用"),
    }


def _route_only_protocol_view(protocol: str) -> dict[str, Any]:
    view = _empty_protocol_view(protocol, status="chain_protocol_not_supported")
    view["status_message"] = f"{_protocol_display_name(protocol)} 属于链路路由/原生执行轨道，不是可复用闪电贷资金池协议"
    return view


def _is_route_only_protocol(protocol: str) -> bool:
    return str(protocol or "").strip().lower() in ROUTE_ONLY_PROTOCOLS


def _protocol_display_name(protocol: str) -> str:
    labels = {
        "aave_v3": "Aave V3",
        "hyper_evm_aave_v3": "HyperEVM Aave V3",
        "venus_core": "Venus Core",
        "morpho_blue": "Morpho Blue",
        "uniswap_v2": "Uniswap V2",
        "uniswap_v3": "Uniswap V3",
        "pancakeswap_v2": "PancakeSwap V2",
        "pancakeswap_v3": "PancakeSwap V3",
        "biswap_v2": "Biswap V2",
        "quickswap_v2": "QuickSwap V2",
        "traderjoe_v2": "Trader Joe V2",
        "balancer_v2": "Balancer V2",
        "cow_protocol": "CoW Protocol",
        "hyperliquid_native": "Hyperliquid Native",
    }
    normalized = str(protocol or "").strip().lower()
    if normalized in labels:
        return labels[normalized]
    value = str(protocol or "").strip().replace("_", " ")
    return value.title() if value else "Unknown"


def _provider_kind(protocol: str, market_type: str) -> str:
    normalized = str(protocol or "").strip().lower()
    if normalized in LENDING_PROTOCOLS:
        return "lending_pool"
    if normalized in DEX_FLASH_PROTOCOLS:
        return "dex_pool"
    if normalized in EXECUTION_RAIL_PROTOCOLS or str(market_type or "").strip().lower() == "execution_rail":
        return "execution_rail"
    return "unknown"


def _bot_consumable(row: dict[str, Any], provider_kind: str) -> bool:
    feature = str(row.get("feature") or "").strip().lower()
    return feature == "liquidation" and provider_kind in {"lending_pool", "execution_rail"}


def _dex_consumable(row: dict[str, Any], provider_kind: str) -> bool:
    feature = str(row.get("feature") or "").strip().lower()
    protocol = str(row.get("protocol") or "").strip().lower()
    if feature == "dex_arbitrage":
        return provider_kind in {"lending_pool", "dex_pool", "execution_rail"}
    return protocol in LENDING_PROTOCOLS


def _provider_from_market(row: dict[str, Any]) -> dict[str, Any]:
    protocol = str(row.get("protocol") or "").strip().lower()
    capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
    protocol_supported = bool(row.get("protocol_supported", capabilities.get("supported", False)))
    observation_only = bool(row.get("observation_only")) or not protocol_supported
    market_type = str(row.get("market_type") or "").strip().lower()
    provider_kind = _provider_kind(protocol, market_type)
    evidence = [
        "market_config",
        "protocol_capability",
    ]
    if row.get("migration_type"):
        evidence.append(str(row["migration_type"]))
    return {
        "market_id": row.get("market_id") or "",
        "feature": row.get("feature") or "",
        "display_name": row.get("display_name") or row.get("market_id") or "Unknown market",
        "network": row.get("network") or "",
        "chain_id": row.get("chain_id"),
        "native_symbol": row.get("native_symbol") or "",
        "market_type": market_type,
        "protocol": protocol,
        "protocol_display_name": _protocol_display_name(protocol),
        "provider_kind": provider_kind,
        "adapter": capabilities.get("adapter") or "ProtocolAdapter",
        "executor": capabilities.get("executor") or "Executor",
        "supported": protocol_supported,
        "observation_only": observation_only,
        "bot_consumable": _bot_consumable(row, provider_kind),
        "dex_consumable": _dex_consumable(row, provider_kind),
        "premium_bps": row.get("premium_bps"),
        "liquidity_status": row.get("liquidity_status") or "unknown",
        "pool_address": row.get("pool_address") or "",
        "provider_address": row.get("pool_address") or row.get("dex_router_address") or "",
        "notes": capabilities.get("notes") or "",
        "evidence": evidence,
    }


def _default_protocols_for_scope(
    *,
    chain_id: int | None,
    selected_market_row: dict[str, Any] | None,
    available_protocols: set[str],
) -> list[str]:
    effective_chain_id = chain_id
    if effective_chain_id is None and selected_market_row is not None:
        raw_chain_id = selected_market_row.get("chain_id")
        if raw_chain_id is not None:
            try:
                effective_chain_id = int(raw_chain_id)
            except (TypeError, ValueError):
                effective_chain_id = None
    preferred = CHAIN_DEFAULT_PROTOCOL_PREFERENCES.get(effective_chain_id or -1, ["aave_v3", "venus_core", "uniswap_v3"])
    for protocol in preferred:
        if protocol in available_protocols:
            return [protocol]
    non_route_protocols = sorted(protocol for protocol in available_protocols if not _is_route_only_protocol(protocol))
    if non_route_protocols:
        return [non_route_protocols[0]]
    route_protocols = sorted(protocol for protocol in available_protocols if _is_route_only_protocol(protocol))
    if route_protocols:
        return [route_protocols[0]]
    return ["aave_v3"]


def flash_loan_pools_payload(
    *,
    database_url: str | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    aave_cache_path: Path | None = None,
    stable_target_cache_path: Path | None = None,
    venus_market_path: Path | None = None,
    dex_pool_static_path: Path | None = None,
    robinhood_evidence_root: Path | None = None,
    robinhood_flash_liquidity_loader: Callable[[], dict[str, Any]] | None = None,
    discovered_protocols: list[str] | None = None,
    avalanche_v3_pool_static_path: Path | None = None,
    avalanche_v2_pool_discovery_path: Path | None = None,
) -> dict[str, Any]:
    requested_protocols: list[str] = []
    for protocol in protocols or []:
        normalized = str(protocol or "").strip().lower()
        if normalized and normalized not in requested_protocols:
            requested_protocols.append(normalized)
    requested_protocol_set = set(requested_protocols)
    discovered_protocol_set = {
        str(protocol or "").strip().lower()
        for protocol in discovered_protocols or []
        if str(protocol or "").strip()
    }
    requested_market_id = str(market_id or "").strip()
    selected_market_row = None
    market_rows = supported_market_summaries(database_url)
    if requested_market_id:
        selected_market_row = next(
            (row for row in market_rows if str(row.get("market_id") or "") == requested_market_id),
            None,
        )
    scoped_providers = []
    for row in market_rows:
        if chain_id is not None:
            if row.get("chain_id") is None:
                continue
            if int(row["chain_id"]) != int(chain_id):
                continue
        elif selected_market_row is not None:
            if row.get("chain_id") != selected_market_row.get("chain_id"):
                continue
            if str(row.get("network") or "") != str(selected_market_row.get("network") or ""):
                continue
        provider = _provider_from_market(row)
        if discovered_protocols is not None and provider["protocol"] not in discovered_protocol_set:
            continue
        scoped_providers.append(provider)

    available_protocols = {item["protocol"] for item in scoped_providers if item.get("protocol")}
    providers = []
    for provider in scoped_providers:
        if requested_protocol_set and provider["protocol"] not in requested_protocol_set:
            continue
        providers.append(provider)
    providers.sort(
        key=lambda item: (
            str(item.get("network") or ""),
            str(item.get("provider_kind") or ""),
            str(item.get("protocol") or ""),
            str(item.get("market_id") or ""),
        )
    )
    summary = {
        "provider_count": len(providers),
        "execution_capable_count": sum(1 for item in providers if not item["observation_only"]),
        "observation_only_count": sum(1 for item in providers if item["observation_only"]),
        "bot_consumable_count": sum(1 for item in providers if item["bot_consumable"]),
        "dex_consumable_count": sum(1 for item in providers if item["dex_consumable"]),
        "chain_count": len({item.get("chain_id") for item in providers if item.get("chain_id") is not None}),
        "protocol_count": len({item.get("protocol") for item in providers if item.get("protocol")}),
    }
    selected_protocols = requested_protocols or _default_protocols_for_scope(
        chain_id=chain_id,
        selected_market_row=selected_market_row,
        available_protocols=available_protocols,
    )
    protocol_views = []
    has_protocol_scope = chain_id is not None or selected_market_row is not None
    for protocol in selected_protocols:
        view_chain_id = chain_id
        if view_chain_id is None and selected_market_row is not None and selected_market_row.get("chain_id") is not None:
            view_chain_id = int(selected_market_row["chain_id"])
        if view_chain_id is None:
            protocol_chain_ids = {
                int(provider["chain_id"])
                for provider in scoped_providers
                if provider.get("protocol") == protocol and provider.get("chain_id") is not None
            }
            if len(protocol_chain_ids) == 1:
                view_chain_id = next(iter(protocol_chain_ids))
        if _is_route_only_protocol(protocol):
            protocol_views.append(_route_only_protocol_view(protocol))
        elif has_protocol_scope and protocol not in available_protocols:
            protocol_views.append(_empty_protocol_view(protocol, status="chain_protocol_not_supported"))
        elif protocol == "morpho_blue" and view_chain_id == 4663:
            protocol_views.append(
                _robinhood_flash_protocol_view(
                    protocol,
                    snapshot_loader=robinhood_flash_liquidity_loader,
                )
            )
        elif protocol == "aave_v3":
            protocol_views.append(
                _aave_protocol_view(
                    aave_cache_path=aave_cache_path,
                    stable_target_cache_path=stable_target_cache_path,
                    chain_id=view_chain_id,
                )
            )
        elif protocol == "venus_core":
            protocol_views.append(
                _venus_protocol_view(
                    chain_id=view_chain_id,
                    venus_market_path=venus_market_path,
                )
            )
        elif protocol in DEX_FLASH_PROTOCOLS:
            protocol_views.append(
                _dex_protocol_view(
                    protocol,
                    chain_id=view_chain_id,
                    dex_pool_static_path=dex_pool_static_path,
                    robinhood_evidence_root=robinhood_evidence_root,
                    robinhood_flash_liquidity_loader=robinhood_flash_liquidity_loader,
                    avalanche_v3_pool_static_path=avalanche_v3_pool_static_path,
                    avalanche_v2_pool_discovery_path=avalanche_v2_pool_discovery_path,
                )
            )
        else:
            protocol_views.append(_empty_protocol_view(protocol))
    primary_view = protocol_views[0]
    scope = {
        "chain_id": chain_id,
        "protocols": requested_protocols,
    }
    if requested_market_id:
        scope["market_id"] = requested_market_id
    return {
        "ok": True,
        "scope": scope,
        "summary": summary,
        "providers": providers,
        "protocol_views": protocol_views,
        "reserve_cache": primary_view["cache"],
        "borrow_target_universe": primary_view["borrow_target_universe"],
    }
