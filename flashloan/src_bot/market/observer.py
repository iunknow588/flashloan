import asyncio

import market.observer_common as _observer_common
from market.observer_common import (
    AAVE_ORACLE,
    ASSETS,
    COINGECKO_MARKETS_URL,
    DEFAULT_BINANCE_REST_BASES,
    DEFAULT_BINANCE_WS_BASES,
    DEFAULT_BINANCE_WS_CHUNK_SIZE,
    DEFAULT_EXECUTABLE_SYMBOLS,
    DEFAULT_RPC,
    DEFAULT_RPC_CANDIDATES,
    DEFAULT_SYMBOLS,
    LATEST_ARBITRAGE_PATH,
    LATEST_EXTREMES_PATH,
    LEGACY_ASSETS,
    LOG,
    ORACLE_ABI,
    STATIC_TOP_MARKET_SYMBOLS,
    AssetConfig,
    ObserverConfig,
    UtcFormatter,
    age_seconds,
    auto_stop_after,
    avalanche_rpc_urls,
    binance_stream_url,
    env_bool,
    env_float,
    env_int,
    env_list,
    env_urls,
    fetch_binance_24h_tickers,
    fetch_binance_rest_price,
    fetch_binance_usdt_symbols,
    fetch_json,
    load_aave_reserve_assets,
    load_aave_reserve_symbols,
    load_borrow_pool_binance_symbols,
    load_stable_pool_binance_symbols,
    load_usdc_pool_binance_symbols,
    mask_url,
    now_iso,
    parse_rpc_urls,
    pct_diff,
    read_aave_flashloan_premium,
    setup_logging,
    should_compute_conversion_profits,
    sleep_until_next,
    utc_from_ms,
    web3_for_rpc_url,
    write_json_atomic,
)
from market.observer_runtime import (
    aave_poller,
    binance_listener,
    binance_rest_poller,
    chunked_symbols,
    extreme_and_arbitrage_reporter,
    main,
    reporter,
)
from market.observer_state import PriceState, insert_extreme

_PATCHABLE_COMMON_NAMES = (
    "avalanche_rpc_urls",
    "fetch_binance_24h_tickers",
    "fetch_binance_usdt_symbols",
    "fetch_json",
    "load_aave_reserve_assets",
    "load_aave_reserve_symbols",
    "load_borrow_pool_binance_symbols",
    "load_stable_pool_binance_symbols",
    "load_usdc_pool_binance_symbols",
    "parse_rpc_urls",
    "read_aave_flashloan_premium",
)


def _sync_common_overrides() -> None:
    for name in _PATCHABLE_COMMON_NAMES:
        if name in globals():
            setattr(_observer_common, name, globals()[name])


def load_config() -> ObserverConfig:
    _sync_common_overrides()
    return _observer_common.load_config()


def resolve_aave_binance_overlap_symbols(rest_bases: list[str], limit: int) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_aave_binance_overlap_symbols(rest_bases, limit)


def resolve_binance_all_usdt_symbols(rest_bases: list[str], limit: int) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_binance_all_usdt_symbols(rest_bases, limit)


def resolve_binance_market_cap_symbols(rest_bases: list[str], limit: int) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_binance_market_cap_symbols(rest_bases, limit)


def resolve_binance_mover_symbols(rest_bases: list[str], limit: int) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_binance_mover_symbols(rest_bases, limit)


def resolve_binance_top_symbols(rest_bases: list[str], limit: int) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_binance_top_symbols(rest_bases, limit)


def resolve_dex_borrow_pool_symbols(rest_bases: list[str], limit: int, borrow_assets: list[dict]) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_dex_borrow_pool_symbols(rest_bases, limit, borrow_assets)


def resolve_dex_stable_pool_symbols(rest_bases: list[str], limit: int) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_dex_stable_pool_symbols(rest_bases, limit)


def resolve_dex_usdc_pool_symbols(rest_bases: list[str], limit: int) -> list[str]:
    _sync_common_overrides()
    return _observer_common.resolve_dex_usdc_pool_symbols(rest_bases, limit)


__all__ = [name for name in globals() if not name.startswith("_")]

if __name__ == "__main__":
    asyncio.run(main())
