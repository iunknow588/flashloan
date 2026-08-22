"""USDC and Aave reserve helpers for unified direct execution."""

from __future__ import annotations

from typing import Any


def _impl():
    from intent_trade import direct_impl

    return direct_impl


def usdc_address_for_network(network: str | None = None, cache: dict[str, Any] | None = None) -> str:
    impl = _impl()
    explicit = impl._normalize_pair_address(
        impl._env(
            "TRIANGULAR_USDC_ADDRESS",
            "ROBINHOOD_USDC_ADDRESS",
            "FUJI_USDC_ADDRESS",
            "FUJI_USDC",
            "USDC_ADDRESS",
        )
    )
    if explicit:
        return explicit
    for entry in (cache or {}).get("pools") or []:
        if not isinstance(entry, dict):
            continue
        left_symbols, right_symbols = impl._cache_entry_symbols(entry)
        entry_x, entry_y = impl._cache_entry_tokens(entry)
        if "USDC" in left_symbols and entry_x:
            return entry_x
        if "USDC" in right_symbols and entry_y:
            return entry_y
    normalized_network = impl._normalize_direct_onchain_network(network)
    if normalized_network == "fuji":
        return impl.FUJI_USDC_ADDRESS
    if normalized_network == "bnb":
        return impl.BNB_USDC_ADDRESS
    if normalized_network in {"robinhood", "robinhood-testnet"}:
        return ""
    return impl.AVALANCHE_USDC_ADDRESS


def aave_reserve_assets(cache: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    impl = _impl()
    payload = cache if isinstance(cache, dict) else impl._load_aave_reserve_cache()
    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else payload.get("selected")
    return list(assets or []) if isinstance(assets, list) else []


def aave_borrowable_token_addresses(
    cache: dict[str, Any] | None = None,
    *,
    min_liquidity: int = 0,
    network: str | None = None,
) -> set[str]:
    impl = _impl()
    payload = cache if isinstance(cache, dict) else impl._load_aave_reserve_cache()
    normalized_network = impl._normalize_direct_onchain_network(network)
    cache_chain_id = payload.get("chain_id")
    expected_chain_id = impl.DIRECT_ONCHAIN_NETWORKS.get(normalized_network, {}).get("chain_id")
    if cache_chain_id is not None and expected_chain_id is not None:
        try:
            if int(cache_chain_id) != int(expected_chain_id):
                return set()
        except (TypeError, ValueError):
            return set()
    cache_rpc = str(payload.get("rpc_url") or "").lower()
    if normalized_network == "fuji" and "avax-test" not in cache_rpc:
        return set()
    if normalized_network == "avalanche" and "avax-test" in cache_rpc:
        return set()
    addresses: set[str] = set()
    for asset in aave_reserve_assets(payload):
        if not isinstance(asset, dict):
            continue
        address = impl._normalize_pair_address(asset.get("token_address") or asset.get("address"))
        if not address:
            continue
        if asset.get("active") is False or asset.get("paused") is True:
            continue
        if asset.get("borrowing_enabled") is False:
            continue
        if "available_liquidity" in asset:
            raw_liquidity = asset.get("available_liquidity")
        elif "reserve_data_liquidity" in asset:
            raw_liquidity = asset.get("reserve_data_liquidity")
        else:
            raw_liquidity = asset.get("a_token_total_supply") or 0
        liquidity = impl._positive_int_value(raw_liquidity)
        if min_liquidity and liquidity < min_liquidity:
            continue
        addresses.add(address)
    return addresses
