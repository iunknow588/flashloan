from __future__ import annotations

from typing import Any, Callable

from intent_trade.chains.robinhood_testnet.v4_flash_accounting_sources import (
    _as_int_units,
    v4_flash_accounting_source_rows,
)
from intent_trade.chains.robinhood_testnet.v4_pool_index import load_robinhood_testnet_v4_pool_index
from intent_trade.chains.robinhood_testnet.v4_pool_liquidity import load_robinhood_testnet_v4_pool_liquidity
from intent_trade.chains.robinhood.shared import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    robinhood_chain_config,
)
from intent_trade.flows.flash_loan_pools.presenter import (
    _cache_identities,
    _cache_identity,
    _depth_counts,
    _dex_pool_row,
    _pool_view_capabilities,
)
ROBINHOOD_TESTNET_MARKET_ID = "dex-arbitrage-46630-uniswap-v4"
ROBINHOOD_TESTNET_V4_POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951"


def is_robinhood_testnet_flash_scope(chain_id: int | None) -> bool:
    try:
        return int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID
    except (TypeError, ValueError):
        return False


def _testnet_config() -> dict[str, Any]:
    return robinhood_chain_config(
        ROBINHOOD_TESTNET_NETWORK,
        default_chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        default_market_id=ROBINHOOD_TESTNET_MARKET_ID,
        default_pool_manager=ROBINHOOD_TESTNET_V4_POOL_MANAGER,
    )


def _v4_pool_rows(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool in pools:
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("id") or "").strip()
        token0 = str(pool.get("token0Symbol") or pool.get("token0_symbol") or "").strip()
        token1 = str(pool.get("token1Symbol") or pool.get("token1_symbol") or "").strip()
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
        token0_metadata = pool_key.get("currency0Metadata") if isinstance(pool_key.get("currency0Metadata"), dict) else {}
        token1_metadata = pool_key.get("currency1Metadata") if isinstance(pool_key.get("currency1Metadata"), dict) else {}
        liquidity_units = _as_int_units(pool.get("liquidity"))
        normalized = {
            **pool,
            "pool": pool.get("pool") or pool_id,
            "symbolPair": pool.get("symbolPair")
            or pool.get("symbol_pair")
            or ":".join(item for item in [token0, token1] if item)
            or (f"poolId:{pool_id[2:10]}" if pool_id.startswith("0x") else pool_id),
            "token0Symbol": token0,
            "token1Symbol": token1,
            "tvlUsd": pool.get("tvlUsd") or pool.get("tvl_usd") or pool.get("depth_score_usd"),
            "token0Usd": pool.get("token0Usd") or pool.get("token0_usd"),
            "token1Usd": pool.get("token1Usd") or pool.get("token1_usd"),
            "dex": pool.get("dex") or "uniswap_v4",
        }
        row = _dex_pool_row(normalized)
        if row.get("token0Symbol") or row.get("token1Symbol"):
            row["depth_level"] = "observable"
            row["depth_label"] = "可观察"
            row["depth_class"] = "ok"
            if pool.get("fee") is not None:
                row["fee"] = pool.get("fee")
            row["available_liquidity"] = 0
            row["liquidity_units"] = liquidity_units
            row["token_address"] = pool.get("token1") or pool.get("token0") or pool_id
            row["pool_address"] = ""
            row["pool_identifier"] = pool_id
        row["pool_id"] = pool_id
        row["pool_key"] = pool.get("poolKey") or pool.get("pool_key")
        row["pool_manager_address"] = pool.get("poolManager") or pool.get("pool_manager")
        row["token0"] = pool.get("token0")
        row["token1"] = pool.get("token1")
        row["tickSpacing"] = pool.get("tickSpacing")
        row["pool_row_kind"] = "v4_pool_key"
        row["pool_liquidity_units"] = liquidity_units
        row["pool_liquidity_units_raw"] = str(pool.get("liquidity") or "")
        row["v4_liquidity_units"] = liquidity_units
        row["pool_manager_balance0"] = token0_metadata.get("poolManagerBalance")
        row["pool_manager_balance1"] = token1_metadata.get("poolManagerBalance")
        row["pool_manager_balance0_raw"] = token0_metadata.get("poolManagerBalanceRaw")
        row["pool_manager_balance1_raw"] = token1_metadata.get("poolManagerBalanceRaw")
        row["borrowable"] = bool(pool.get("borrowable", False))
        row["lending_borrowable"] = bool(pool.get("lending_borrowable", False))
        row["flash_borrowable"] = bool(pool.get("flash_borrowable", False))
        row["flash_capable"] = True
        row["flash_borrowable_reason"] = str(
            pool.get("flash_borrowable_reason")
            or "poolkey_is_observation_only_not_flash_borrowable_asset"
        )
        row["pool_role"] = "v4_observable_pool_key"
        rows.append(row)
    return rows


def _pool_id(pool: dict[str, Any]) -> str:
    return str(pool.get("poolId") or pool.get("pool_id") or pool.get("id") or "").strip().lower()


def _active_pool_ids(liquidity: dict[str, Any]) -> set[str]:
    return {
        pool_id
        for pool in liquidity.get("pools") or []
        if isinstance(pool, dict)
        for pool_id in [_pool_id(pool)]
        if pool_id
    }


def _display_pools(liquidity: dict[str, Any], pool_index: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    active_ids = _active_pool_ids(liquidity)
    indexed_pools = [pool for pool in pool_index.get("pools") or [] if isinstance(pool, dict)]
    if pool_index.get("ok") and active_ids:
        matched = [pool for pool in indexed_pools if _pool_id(pool) in active_ids]
        if matched:
            return matched, pool_index.get("source")
    return [pool for pool in liquidity.get("pools") or [] if isinstance(pool, dict)], liquidity.get("source")


def _pool_token_addresses(pools: list[dict[str, Any]]) -> set[str]:
    addresses: set[str] = set()
    for pool in pools:
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
        for key in ("token0", "token1", "currency0", "currency1"):
            value = str(pool.get(key) or pool_key.get(key) or "").strip()
            if value.startswith("0x") and len(value) == 42 and value.lower() != "0x0000000000000000000000000000000000000000":
                addresses.add(value.lower())
    return addresses


def robinhood_testnet_flash_protocol_view(
    protocol: str,
    *,
    empty_protocol_view: Callable[[str], dict[str, Any]],
    pool_liquidity_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_liquidity,
    pool_index_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_index,
) -> dict[str, Any]:
    """Return the chain-46630 flash-pool view without touching mainnet caches."""

    config = _testnet_config()
    liquidity = pool_liquidity_loader()
    pool_index = pool_index_loader()
    display_pools, display_pool_source = _display_pools(liquidity, pool_index)
    pool_rows = _v4_pool_rows(display_pools)
    decoded_pool_key_count = int((liquidity.get("payload") or {}).get("decodedPoolKeyCount") or 0)
    token_address_count = int((liquidity.get("payload") or {}).get("tokenAddressCount") or 0)
    if pool_index.get("ok"):
        decoded_pool_key_count = sum(
            1
            for pool in display_pools
            if isinstance(pool, dict) and (pool.get("poolKeyDecoded") or pool.get("token0Symbol") or pool.get("token1Symbol"))
        )
        token_address_count = max(token_address_count, len(_pool_token_addresses(display_pools)))
    flash_source_evidence_rows = v4_flash_accounting_source_rows(
        liquidity,
        pool_manager=config["pool_manager"],
        include_ineffective=True,
    )
    flash_source_rows = [
        row
        for row in flash_source_evidence_rows
        if row.get("effective_flash_accounting_asset", row.get("effective_flash_asset", True))
    ]
    has_flash_sources = bool(flash_source_rows)
    executor_supported_flash_rows = [row for row in flash_source_rows if row.get("executor_supported")]
    stable_pool_key_rows = [row for row in pool_rows if row.get("is_stable")]
    non_stable_pool_key_rows = [row for row in pool_rows if not row.get("is_stable")]
    stable_flash_source_rows = [row for row in flash_source_rows if row.get("is_stable")]
    non_stable_flash_source_rows = [row for row in flash_source_rows if not row.get("is_stable")]
    stable_pool_key_rows.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_pool_key_rows.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    stable_rows = stable_flash_source_rows
    non_stable_rows = non_stable_flash_source_rows
    view = empty_protocol_view(protocol)
    if has_flash_sources or (pool_rows and liquidity.get("ok")):
        view.pop("status", None)
    view["display_name"] = "Robinhood Testnet Uniswap V4"
    view["display_mode"] = "robinhood_testnet_v4_flash_accounting"
    view["pool_role_label"] = "V4 PoolManager flash accounting"
    if not has_flash_sources and (not pool_rows or not liquidity.get("ok")):
        view["status"] = str(liquidity.get("status") or "v4_pool_liquidity_cache_not_available")
    fallback_status_message = (
        "Robinhood Testnet 是 chain 46630。V4 PoolManager 不是独立池地址，但它的币种余额就是闪电贷可用资金来源；"
        "执行可用性仍需 eth_call 验证。"
        "No Aave or Morpho lending pool is verified here; "
        "only isolated Uniswap V4 PoolManager flash-accounting evidence is used."
    )
    view["status_message"] = str(liquidity.get("status_message") or fallback_status_message)
    if has_flash_sources:
        view["status_note"] = "本地证据已发现 PoolManager 非零余额币种；这些是 V4 flash accounting 资金来源，最终执行可用性以 eth_call unlock/take/settle 模拟为准。"
    view["cache"].update(
        {
            "chain_id": config["chain_id"],
            "pool_address": "",
            "pool_manager_address": config["pool_manager"],
            "identities": _cache_identities(
                _cache_identity("pool_manager", config["pool_manager"], label="PoolManager"),
            ),
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(pool_rows),
                borrowable=has_flash_sources,
                flash_capable=True,
                observable=bool(pool_rows and liquidity.get("ok")),
            ),
            "source": "chains/robinhood-testnet/chain.json",
            "pool_liquidity_source": liquidity.get("source"),
            "pool_index_source": pool_index.get("source"),
            "display_pool_source": display_pool_source,
            "schema_version": (liquidity.get("payload") or {}).get("schemaVersion"),
            "block_number": liquidity.get("block_number"),
            "refreshed_at": liquidity.get("refreshed_at"),
            "asset_count": 0,
            "flash_accounting_evidence_asset_count": len(flash_source_evidence_rows),
            "flash_accounting_asset_count": len(flash_source_rows),
            "executor_supported_flash_accounting_asset_count": len(executor_supported_flash_rows),
            "observable_pool_key_count": len(pool_rows),
            "pool_key_count": len(liquidity.get("pool_keys") or []),
            "indexed_pool_key_count": len(pool_index.get("pool_keys") or []),
        }
    )
    view["summary"].update(
        {
            "flash_accounting_poolmanager_count": 1 if protocol == "uniswap_v4" and flash_source_rows else 0,
            "v4_pool_key_count": len(liquidity.get("pool_keys") or []),
            "v4_pool_liquidity_pool_count": len(pool_rows),
            "v4_decoded_pool_key_count": decoded_pool_key_count,
            "v4_token_address_count": token_address_count,
            "v4_indexed_pool_key_count": len(pool_index.get("pool_keys") or []),
            "v4_active_pool_id_count": len(_active_pool_ids(liquidity)),
            "lending_pool_count": 0,
            "aave_pool_count": 0,
            "morpho_pool_count": 0,
            "stable_borrow_pool_count": len(stable_rows),
            "non_stable_borrow_pool_count": len(non_stable_rows),
            "stable_flash_accounting_source_count": len(stable_flash_source_rows),
            "non_stable_flash_accounting_source_count": len(non_stable_flash_source_rows),
            "flash_accounting_evidence_asset_count": len(flash_source_evidence_rows),
            "flash_accounting_excluded_asset_count": len(flash_source_evidence_rows) - len(flash_source_rows),
            "min_candidate_pool_count": flash_source_evidence_rows[0].get("min_candidate_pool_count") if flash_source_evidence_rows else 5,
            "min_poolmanager_balance": flash_source_evidence_rows[0].get("min_poolmanager_balance") if flash_source_evidence_rows else 1.0,
            "stable_pool_key_count": len(stable_pool_key_rows),
            "non_stable_pool_key_count": len(non_stable_pool_key_rows),
            "observable_pool_key_count": len(pool_rows),
            "borrow_asset_count": len(flash_source_rows),
            "verified_lending_borrow_asset_count": 0,
            "flash_accounting_asset_count": len(flash_source_rows),
            "flash_borrowable_asset_count": len(flash_source_rows),
            "executor_supported_flash_asset_count": len(executor_supported_flash_rows),
            "eth_call_verified_flash_asset_count": 0,
            "native_currency_flash_asset_count": sum(1 for row in flash_source_rows if not row.get("executor_supported")),
            "borrow_target_count": len(flash_source_rows),
            "broadcast_enabled": False,
            "depth_counts": _depth_counts(pool_rows),
        }
    )
    view["stable_borrow_pools"] = stable_rows
    view["non_stable_borrow_pools"] = non_stable_rows
    view["v4_pool_key_rows"] = pool_rows
    view["stable_pool_key_rows"] = stable_pool_key_rows
    view["non_stable_pool_key_rows"] = non_stable_pool_key_rows
    view["v4_flash_accounting_source_rows"] = flash_source_rows
    view["v4_flash_accounting_evidence_rows"] = flash_source_evidence_rows
    view["stable_v4_flash_source_rows"] = stable_flash_source_rows
    view["non_stable_v4_flash_source_rows"] = non_stable_flash_source_rows
    view["borrow_target_universe"] = None
    view["v4_pool_key_status"] = {
        "observable_pool_key_count": len(pool_rows),
        "stable_pool_key_count": len(stable_pool_key_rows),
        "non_stable_pool_key_count": len(non_stable_pool_key_rows),
        "flash_accounting_asset_count": len(flash_source_rows),
        "poolmanager_balance_asset_count": len(flash_source_rows),
        "verified_lending_borrow_asset_count": 0,
        "flash_borrowable_asset_count": len(flash_source_rows),
        "executor_supported_flash_asset_count": len(executor_supported_flash_rows),
        "eth_call_verified_flash_asset_count": 0,
        "source": "robinhood_testnet_v4_poolkey_liquidity_cache",
    }
    view["chainIsolation"] = {
        "network": config["network"],
        "chainId": config["chain_id"],
        "marketId": config["market_id"],
        "usesMainnetRobinhoodCache": False,
        "usesAaveOrMorphoPool": False,
        "poolManager": config["pool_manager"],
        "poolManagerIsConcretePool": False,
        "poolLiquidityCacheAvailable": bool(pool_rows and liquidity.get("ok")),
        "poolLiquiditySource": liquidity.get("source"),
        "poolIndexSource": pool_index.get("source"),
        "displayPoolSource": display_pool_source,
        "blockingReasons": list(liquidity.get("blocking_reasons") or []),
    }
    return view


__all__ = [
    "is_robinhood_testnet_flash_scope",
    "robinhood_testnet_flash_protocol_view",
]
