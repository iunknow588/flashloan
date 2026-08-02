import os
from pathlib import Path
from typing import Optional

from web3 import Web3

from core.config_schema import parse_env_int
from execution.dex_costs import TRADER_JOE_V2_ROUTER, USDC
from market.aave_reserve_cache import cache_is_fresh, parse_rpc_urls, web3_for_rpc_url, write_cache
from market.dex_target_common import (
    PAIR_CREATED_TOPIC,
    ROUTER_FACTORY_ABI,
    default_borrow_cache_path,
    default_cache_path,
    default_usdc_cache_path,
    event_pair_address,
    fetch_dexscreener_borrow_pool_assets,
    fetch_dexscreener_stable_pool_assets,
    is_stable_token_symbol,
    now_iso,
    parse_stable_tokens,
    borrow_tokens_from_assets,
    pair_token_addresses,
    read_cache,
    token_meta,
    topic_for_address,
)
def fetch_usdc_pool_assets(
    rpc_url: str,
    router_address: str = TRADER_JOE_V2_ROUTER,
    usdc_address: str = USDC,
    ignored_addresses: set[str] | None = None,
    from_block: int = 0,
    chunk_size: int = 50_000,
) -> list[dict]:
    w3 = web3_for_rpc_url(rpc_url, timeout=20)
    router = w3.eth.contract(address=Web3.to_checksum_address(router_address), abi=ROUTER_FACTORY_ABI)
    factory_address = Web3.to_checksum_address(router.functions.factory().call())
    usdc = Web3.to_checksum_address(usdc_address)
    ignored = {address.lower() for address in (ignored_addresses or set())}
    ignored.add(usdc.lower())
    latest_block = int(w3.eth.block_number)
    start_block = max(0, int(from_block))
    chunk = max(1, min(int(chunk_size), 50_000))
    token_addresses: dict[str, str] = {}

    for topic_index in (1, 2):
        topics = [PAIR_CREATED_TOPIC, None, None]
        topics[topic_index] = topic_for_address(usdc)
        current = start_block
        while current <= latest_block:
            end_block = min(latest_block, current + chunk - 1)
            logs = w3.eth.get_logs(
                {
                    "address": factory_address,
                    "fromBlock": current,
                    "toBlock": end_block,
                    "topics": topics,
                }
            )
            for log in logs:
                pair_address = event_pair_address(log)
                if not pair_address:
                    continue
                pair_tokens = pair_token_addresses(w3, pair_address)
                if not pair_tokens:
                    continue
                token0, token1 = pair_tokens
                other = token1 if token0.lower() == usdc.lower() else token0
                if other.lower() not in ignored:
                    token_addresses[other.lower()] = other
            current = end_block + 1

    assets = []
    for address in token_addresses.values():
        item = token_meta(w3, address)
        if item and not is_stable_token_symbol(str(item.get("token_symbol", ""))):
            assets.append(item)
    return sorted(assets, key=lambda item: item["binance_symbol"])


def fetch_stable_pool_assets(
    rpc_url: str,
    stable_tokens: dict[str, str],
    router_address: str = TRADER_JOE_V2_ROUTER,
    from_block: int = 0,
    chunk_size: int = 50_000,
) -> list[dict]:
    stable_addresses = {Web3.to_checksum_address(address) for address in stable_tokens.values()}
    by_token: dict[str, dict] = {}
    for stable_label, stable_address in stable_tokens.items():
        for asset in fetch_usdc_pool_assets(
            rpc_url,
            router_address=router_address,
            usdc_address=stable_address,
            ignored_addresses=stable_addresses,
            from_block=from_block,
            chunk_size=chunk_size,
        ):
            key = str(asset["token_address"]).lower()
            existing = by_token.setdefault(key, {**asset, "via_stables": []})
            if stable_label not in existing["via_stables"]:
                existing["via_stables"].append(stable_label)
    return sorted(by_token.values(), key=lambda item: item["binance_symbol"])


def fetch_borrow_pool_assets(
    rpc_url: str,
    borrow_tokens: dict[str, str],
    router_address: str = TRADER_JOE_V2_ROUTER,
    from_block: int = 0,
    chunk_size: int = 50_000,
) -> list[dict]:
    borrow_addresses = {Web3.to_checksum_address(address) for address in borrow_tokens.values()}
    by_token: dict[str, dict] = {}
    for borrow_label, borrow_address in borrow_tokens.items():
        for asset in fetch_usdc_pool_assets(
            rpc_url,
            router_address=router_address,
            usdc_address=borrow_address,
            ignored_addresses=borrow_addresses,
            from_block=from_block,
            chunk_size=chunk_size,
        ):
            key = str(asset["token_address"]).lower()
            existing = by_token.setdefault(key, {**asset, "via_borrows": []})
            if borrow_label not in existing["via_borrows"]:
                existing["via_borrows"].append(borrow_label)
    return sorted(by_token.values(), key=lambda item: item["binance_symbol"])


def load_usdc_pool_assets(
    rpc_urls: str | list[str] | tuple[str, ...],
    *,
    router_address: str = TRADER_JOE_V2_ROUTER,
    usdc_address: str = USDC,
    from_block: int = 0,
    chunk_size: int = 50_000,
    cache_path: Optional[Path] = None,
    refresh_seconds: int = 3600,
) -> list[dict]:
    path = cache_path or default_usdc_cache_path()
    cached = read_cache(path)
    if cached and cache_is_fresh(cached, refresh_seconds):
        assets = cached.get("assets")
        if isinstance(assets, list):
            return assets

    last_error = None
    for rpc_url in parse_rpc_urls(rpc_urls):
        try:
            assets = fetch_usdc_pool_assets(
                rpc_url,
                router_address=router_address,
                usdc_address=usdc_address,
                from_block=from_block,
                chunk_size=chunk_size,
            )
            write_cache(
                path,
                {
                    "refreshed_at": now_iso(),
                    "router_address": Web3.to_checksum_address(router_address),
                    "usdc_address": Web3.to_checksum_address(usdc_address),
                    "asset_count": len(assets),
                    "assets": assets,
                },
            )
            return assets
        except Exception as exc:
            last_error = exc
    if cached and isinstance(cached.get("assets"), list):
        return cached["assets"]
    if last_error:
        raise last_error
    return []


def load_stable_pool_assets(
    rpc_urls: str | list[str] | tuple[str, ...],
    *,
    stable_tokens: dict[str, str] | None = None,
    router_address: str = TRADER_JOE_V2_ROUTER,
    from_block: int = 0,
    chunk_size: int = 50_000,
    cache_path: Optional[Path] = None,
    refresh_seconds: int = 3600,
) -> list[dict]:
    tokens = stable_tokens or parse_stable_tokens()
    path = cache_path or default_cache_path()
    cached = read_cache(path)
    if cached and cache_is_fresh(cached, refresh_seconds):
        assets = cached.get("assets")
        if isinstance(assets, list):
            return assets

    last_error = None
    for rpc_url in parse_rpc_urls(rpc_urls):
        try:
            assets = fetch_stable_pool_assets(
                rpc_url,
                tokens,
                router_address=router_address,
                from_block=from_block,
                chunk_size=chunk_size,
            )
            write_cache(
                path,
                {
                    "refreshed_at": now_iso(),
                    "router_address": Web3.to_checksum_address(router_address),
                    "stable_tokens": tokens,
                    "asset_count": len(assets),
                    "assets": assets,
                },
            )
            return assets
        except Exception as exc:
            last_error = exc
    assets = fetch_dexscreener_stable_pool_assets(tokens)
    if assets:
        write_cache(
            path,
            {
                "refreshed_at": now_iso(),
                "router_address": Web3.to_checksum_address(router_address),
                "stable_tokens": tokens,
                "asset_count": len(assets),
                "assets": assets,
                "source": "dexscreener_fallback",
            },
        )
        return assets
    if cached and isinstance(cached.get("assets"), list):
        return cached["assets"]
    if last_error:
        raise last_error
    return []


def load_borrow_pool_assets(
    rpc_urls: str | list[str] | tuple[str, ...],
    borrow_assets: list[dict],
    *,
    router_address: str = TRADER_JOE_V2_ROUTER,
    from_block: int = 0,
    chunk_size: int = 50_000,
    cache_path: Optional[Path] = None,
    refresh_seconds: int = 3600,
) -> list[dict]:
    tokens = borrow_tokens_from_assets(borrow_assets)
    path = cache_path or default_borrow_cache_path()
    cached = read_cache(path)
    if cached and cache_is_fresh(cached, refresh_seconds):
        assets = cached.get("assets")
        if isinstance(assets, list):
            return assets

    discovery_source = os.getenv("DEX_BORROW_TARGET_SOURCE", "dexscreener").strip().lower()
    if discovery_source in {"api", "dexscreener", "dexscreener_first"}:
        assets = fetch_dexscreener_borrow_pool_assets(tokens)
        if assets:
            write_cache(
                path,
                {
                    "refreshed_at": now_iso(),
                    "router_address": Web3.to_checksum_address(router_address),
                    "borrow_tokens": tokens,
                    "asset_count": len(assets),
                    "assets": assets,
                    "source": "dexscreener",
                },
            )
            return assets

    last_error = None
    for rpc_url in parse_rpc_urls(rpc_urls):
        try:
            assets = fetch_borrow_pool_assets(
                rpc_url,
                tokens,
                router_address=router_address,
                from_block=from_block,
                chunk_size=chunk_size,
            )
            write_cache(
                path,
                {
                    "refreshed_at": now_iso(),
                    "router_address": Web3.to_checksum_address(router_address),
                    "borrow_tokens": tokens,
                    "asset_count": len(assets),
                    "assets": assets,
                },
            )
            return assets
        except Exception as exc:
            last_error = exc
    assets = fetch_dexscreener_borrow_pool_assets(tokens)
    if assets:
        write_cache(
            path,
            {
                "refreshed_at": now_iso(),
                "router_address": Web3.to_checksum_address(router_address),
                "borrow_tokens": tokens,
                "asset_count": len(assets),
                "assets": assets,
                "source": "dexscreener_fallback",
            },
        )
        return assets
    if cached and isinstance(cached.get("assets"), list):
        return cached["assets"]
    if last_error:
        raise last_error
    return []


def load_usdc_pool_binance_symbols(
    rpc_urls: str | list[str] | tuple[str, ...],
    *,
    supported_symbols: set[str] | None = None,
    limit: int = 0,
) -> list[str]:
    assets = load_usdc_pool_assets(
        rpc_urls,
        from_block=parse_env_int("DEX_USDC_POOL_FROM_BLOCK", 0, minimum=0)[0],
        chunk_size=parse_env_int("DEX_USDC_POOL_SCAN_CHUNK_SIZE", 50000, minimum=1)[0],
        refresh_seconds=parse_env_int("DEX_USDC_TARGET_CACHE_SECONDS", 3600, minimum=0)[0],
    )
    symbols = list(dict.fromkeys(str(asset.get("binance_symbol", "")).upper() for asset in assets if asset.get("binance_symbol")))
    if supported_symbols:
        symbols = [symbol for symbol in symbols if symbol in supported_symbols]
    return symbols if limit <= 0 else symbols[:limit]


def load_stable_pool_binance_symbols(
    rpc_urls: str | list[str] | tuple[str, ...],
    *,
    supported_symbols: set[str] | None = None,
    limit: int = 0,
) -> list[str]:
    assets = load_stable_pool_assets(
        rpc_urls,
        from_block=parse_env_int("DEX_STABLE_POOL_FROM_BLOCK", os.getenv("DEX_USDC_POOL_FROM_BLOCK", "0"), minimum=0)[0],
        chunk_size=parse_env_int("DEX_STABLE_POOL_SCAN_CHUNK_SIZE", os.getenv("DEX_USDC_POOL_SCAN_CHUNK_SIZE", "50000"), minimum=1)[0],
        refresh_seconds=parse_env_int("DEX_STABLE_TARGET_CACHE_SECONDS", os.getenv("DEX_USDC_TARGET_CACHE_SECONDS", "3600"), minimum=0)[0],
    )
    symbols = list(dict.fromkeys(str(asset.get("binance_symbol", "")).upper() for asset in assets if asset.get("binance_symbol")))
    if supported_symbols:
        symbols = [symbol for symbol in symbols if symbol in supported_symbols]
    return symbols if limit <= 0 else symbols[:limit]


def load_borrow_pool_binance_symbols(
    rpc_urls: str | list[str] | tuple[str, ...],
    borrow_assets: list[dict],
    *,
    supported_symbols: set[str] | None = None,
    limit: int = 0,
) -> list[str]:
    assets = load_borrow_pool_assets(
        rpc_urls,
        borrow_assets,
        from_block=parse_env_int("DEX_BORROW_POOL_FROM_BLOCK", os.getenv("DEX_STABLE_POOL_FROM_BLOCK", "0"), minimum=0)[0],
        chunk_size=parse_env_int("DEX_BORROW_POOL_SCAN_CHUNK_SIZE", os.getenv("DEX_STABLE_POOL_SCAN_CHUNK_SIZE", "50000"), minimum=1)[0],
        refresh_seconds=parse_env_int("DEX_BORROW_TARGET_CACHE_SECONDS", os.getenv("DEX_STABLE_TARGET_CACHE_SECONDS", "3600"), minimum=0)[0],
    )
    symbols = list(dict.fromkeys(str(asset.get("binance_symbol", "")).upper() for asset in assets if asset.get("binance_symbol")))
    if supported_symbols:
        symbols = [symbol for symbol in symbols if symbol in supported_symbols]
    return symbols if limit <= 0 else symbols[:limit]
