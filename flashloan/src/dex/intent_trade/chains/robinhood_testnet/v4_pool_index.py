"""Robinhood testnet Uniswap V4 PoolKey index adapter.

The chain-agnostic implementation lives in ``robinhood_common``. This module
only supplies testnet configuration and preserves the public API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from dex.intent_trade.chains.robinhood_common.v4_pool_index_core import (
    build_v4_directional_pool_backfill as _backfill_core,
    build_v4_pool_index_from_initialize_logs as _build_core,
    load_robinhood_v4_pool_index as _load_core,
    write_v4_pool_index_from_initialize_logs as _write_core,
)
from dex.intent_trade.chains.robinhood_common.v4_sqlite_view import (
    load_v4_pool_index_payload as _load_db_index_payload,
)
from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    ROBINHOOD_TESTNET_V4_POOL_MANAGER,
    _rpc_url_from_env_or_default,
)


def default_v4_pool_index_path() -> Path | None:
    return chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_index")


def build_v4_pool_index_from_initialize_logs(
    *,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    from_block: int = 0,
    to_block: int | None = None,
    chunk_size: int = 5_000_000,
    pool_limit: int = 0,
    non_eth_only: bool = False,
    include_metadata: bool = False,
    mark_recent_swaps: bool = True,
    recent_swap_lookback_blocks: int = 20_000,
) -> dict[str, Any]:
    return _build_core(
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        pool_manager=pool_manager or ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        rpc_url=rpc_url or _rpc_url_from_env_or_default(),
        schema_version="robinhood-testnet-v4-pool-index-v1",
        include_currency_metadata=True,
        from_block=from_block,
        to_block=to_block,
        chunk_size=chunk_size,
        pool_limit=pool_limit,
        non_eth_only=non_eth_only,
        include_metadata=include_metadata,
        mark_recent_swaps=mark_recent_swaps,
        recent_swap_lookback_blocks=recent_swap_lookback_blocks,
        rpc_user_agent="flashloan-robinhood-testnet-v4/1.0",
    )


def build_v4_directional_pool_backfill(
    *,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    direction_targets: dict,
    direction_floor_block: int = 0,
    from_block: int = 0,
    to_block: int | None = None,
    chunk_size: int = 100_000,
    confirm_pool_liquidity: object | None = None,
) -> dict:
    """Scan testnet Initialize logs newest-to-oldest until directions are full."""
    return _backfill_core(
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        pool_manager=pool_manager or ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        rpc_url=rpc_url or _rpc_url_from_env_or_default(),
        schema_version="robinhood-testnet-v4-pool-index-v1",
        direction_targets=direction_targets,
        direction_floor_block=direction_floor_block,
        include_currency_metadata=False,
        chunk_size=chunk_size,
        confirm_pool_liquidity=confirm_pool_liquidity,
        rpc_user_agent="flashloan-robinhood-testnet-v4/1.0",
    )


def write_v4_pool_index_from_initialize_logs(
    output_path: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _write_core(
        output_path=output_path,
        default_output_path=default_v4_pool_index_path(),
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        pool_manager=kwargs.pop("pool_manager", None) or ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        rpc_url=kwargs.pop("rpc_url", None) or _rpc_url_from_env_or_default(),
        schema_version="robinhood-testnet-v4-pool-index-v1",
        include_currency_metadata=True,
        rpc_user_agent="flashloan-robinhood-testnet-v4/1.0",
        **kwargs,
    )


def load_robinhood_testnet_v4_pool_index(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        return _load_db_index_payload(
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
        )
    return _load_core(
        path=path or default_v4_pool_index_path(),
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )


__all__ = [
    "build_v4_directional_pool_backfill",
    "build_v4_pool_index_from_initialize_logs",
    "default_v4_pool_index_path",
    "load_robinhood_testnet_v4_pool_index",
    "write_v4_pool_index_from_initialize_logs",
]
