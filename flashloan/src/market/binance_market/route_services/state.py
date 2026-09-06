"""Service payload builders for Binance market state routes."""

from __future__ import annotations

from market.binance_market.service import (
    DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    build_binance_market_state,
    build_cow_network_market_claims,
    build_cow_supported_market_overview,
    cow_network_options,
    load_cow_supported_token_registry,
    select_binance_market_extremes,
)


def _market_state(
    extremes: dict | None,
    *,
    assets: list[str],
    arbitrage_config,
    slippage_bps: int,
    thresholds: dict,
    token_cache: dict,
    side_limit: int,
    pair_side_limit: int,
    record_pool_evidence,
) -> dict:
    market_state = build_binance_market_state(
        extremes,
        aave_symbols=assets,
        arbitrage_config=arbitrage_config,
        top_limit=side_limit,
        bottom_limit=side_limit,
        pair_side_limit=pair_side_limit,
        cow_display_limit=DEFAULT_BINANCE_RAW_SIDE_LIMIT,
        slippage_bps=slippage_bps,
        cow_network=token_cache["network"],
        min_spread_percent=float(thresholds["adjusted_min_spread_percent"]),
        min_side_change_percent=float(thresholds["min_side_change_percent"]),
        min_token_price_usd=float(thresholds["min_token_price_usd"]),
        threshold_detail=thresholds,
        registry=token_cache["registry"],
    )
    market_state["cow_filter"]["token_cache_source"] = token_cache["source"]
    market_state["cow_filter"]["token_cache_count"] = token_cache["token_count"]
    market_state["cow_filter"]["cow_display_limit"] = DEFAULT_BINANCE_RAW_SIDE_LIMIT
    market_state["cow_top"] = list(market_state.get("top") or [])[:DEFAULT_BINANCE_RAW_SIDE_LIMIT]
    market_state["cow_bottom"] = list(market_state.get("bottom") or [])[:DEFAULT_BINANCE_RAW_SIDE_LIMIT]
    market_state["pool_evidence_recording"] = record_pool_evidence(
        market_state,
        extremes=extremes,
    )
    return market_state


def build_market_state_payload(
    extremes: dict | None,
    *,
    assets: list[str],
    arbitrage_config,
    slippage_bps: int,
    thresholds: dict,
    cow_network: str,
    side_limit: int,
    pair_side_limit: int,
    database_url,
    chain_selection,
    select_extremes=select_binance_market_extremes,
    record_market_candidates=None,
    record_pool_evidence=None,
    filter_network_claims=None,
) -> dict:
    token_cache = load_cow_supported_token_registry(
        cow_network=cow_network,
        database_url=database_url,
    )
    selected_extremes = select_extremes(extremes, side_limit=side_limit)
    network_token_caches = {}
    for item in cow_network_options()["networks"]:
        if item.get("testnet"):
            continue
        network = item["network"]
        network_token_caches[network] = (
            token_cache
            if network == token_cache["network"]
            else load_cow_supported_token_registry(
                cow_network=network,
                database_url=database_url,
                allow_live_fallback=False,
            )
        )
    market_state = _market_state(
        selected_extremes,
        assets=assets,
        arbitrage_config=arbitrage_config,
        slippage_bps=slippage_bps,
        thresholds=thresholds,
        token_cache=token_cache,
        side_limit=side_limit,
        pair_side_limit=pair_side_limit,
        record_pool_evidence=record_pool_evidence,
    )
    claims = build_cow_network_market_claims(
        selected_extremes,
        network_token_caches,
        limit=5,
        min_spread_percent=float(thresholds["adjusted_min_spread_percent"]),
        min_side_change_percent=float(thresholds["min_side_change_percent"]),
        min_token_price_usd=float(thresholds["min_token_price_usd"]),
        threshold_detail=thresholds,
    )
    for claim in claims:
        claim["top"] = list(claim.get("top") or [])[:5]
        claim["bottom"] = list(claim.get("bottom") or [])[:5]
    market_state["cow_network_claims"] = claims
    market_state["cow_supported_overview"] = build_cow_supported_market_overview(
        selected_extremes,
        network_token_caches,
        limit=50,
        min_side_change_percent=float(thresholds["min_side_change_percent"]),
        min_token_price_usd=float(thresholds["min_token_price_usd"]),
        threshold_detail=thresholds,
    )
    market_state["history_recording"] = record_market_candidates(
        [market_state],
        network_claims=filter_network_claims(claims, [token_cache["network"]]),
        observed_at=market_state.get("observed_at"),
        window_seconds=market_state.get("window_seconds"),
        price_source=market_state.get("price_source"),
        market_state_source=market_state.get("market_state_source"),
        fallback_reason=market_state.get("fallback_reason"),
        database_url=database_url,
    )
    market_state["chain_selection"] = chain_selection
    return market_state


def build_market_states_payload(
    extremes: dict | None,
    *,
    assets: list[str],
    arbitrage_config,
    slippage_bps: int,
    thresholds: dict,
    side_limit: int,
    pair_side_limit: int,
    requested: list[str],
    database_url,
    chain_selection,
    select_extremes=select_binance_market_extremes,
    record_market_candidates=None,
    record_pool_evidence=None,
    filter_network_claims=None,
    retain_networks=None,
) -> dict:
    mainnet_networks = [
        item["network"]
        for item in cow_network_options()["networks"]
        if not item.get("testnet")
    ]
    selected_extremes = select_extremes(extremes, side_limit=side_limit)
    network_token_caches = {
        network: load_cow_supported_token_registry(
            cow_network=network,
            database_url=database_url,
            allow_live_fallback=network in requested,
        )
        for network in mainnet_networks
    }
    claims = build_cow_network_market_claims(
        selected_extremes,
        network_token_caches,
        limit=5,
        min_spread_percent=float(thresholds["adjusted_min_spread_percent"]),
        min_side_change_percent=float(thresholds["min_side_change_percent"]),
        min_token_price_usd=float(thresholds["min_token_price_usd"]),
        threshold_detail=thresholds,
    )
    for claim in claims:
        claim["top"] = list(claim.get("top") or [])[:5]
        claim["bottom"] = list(claim.get("bottom") or [])[:5]
    overview = build_cow_supported_market_overview(
        selected_extremes,
        network_token_caches,
        limit=50,
        min_side_change_percent=float(thresholds["min_side_change_percent"]),
        min_token_price_usd=float(thresholds["min_token_price_usd"]),
        threshold_detail=thresholds,
    )
    states = {}
    history_states = []
    for network in requested:
        state = _market_state(
            selected_extremes,
            assets=assets,
            arbitrage_config=arbitrage_config,
            slippage_bps=slippage_bps,
            thresholds=thresholds,
            token_cache=network_token_caches[network],
            side_limit=side_limit,
            pair_side_limit=pair_side_limit,
            record_pool_evidence=record_pool_evidence,
        )
        states[network] = state
        history_states.append(state)
    history = record_market_candidates(
        history_states,
        network_claims=filter_network_claims(claims, requested),
        observed_at=selected_extremes.get("observed_at") if isinstance(selected_extremes, dict) else None,
        window_seconds=selected_extremes.get("window_seconds") if isinstance(selected_extremes, dict) else None,
        price_source=selected_extremes.get("price_source") if isinstance(selected_extremes, dict) else None,
        market_state_source=selected_extremes.get("market_state_source") if isinstance(selected_extremes, dict) else None,
        fallback_reason=selected_extremes.get("fallback_reason") if isinstance(selected_extremes, dict) else None,
        database_url=database_url,
    )
    return {
        "networks": requested,
        "states": states,
        "history_recording": history,
        "queue_retention": retain_networks(requested),
        "cow_network_claims": claims,
        "cow_supported_overview": overview,
        "observed_at": selected_extremes.get("observed_at") if isinstance(selected_extremes, dict) else None,
        "window_seconds": selected_extremes.get("window_seconds") if isinstance(selected_extremes, dict) else None,
        "price_source": selected_extremes.get("price_source") if isinstance(selected_extremes, dict) else None,
        "market_state_source": selected_extremes.get("market_state_source") if isinstance(selected_extremes, dict) else None,
        "fallback_reason": selected_extremes.get("fallback_reason") if isinstance(selected_extremes, dict) else None,
        "threshold_detail": thresholds,
        "chain_selection": chain_selection,
    }


__all__ = ["build_market_state_payload", "build_market_states_payload"]
