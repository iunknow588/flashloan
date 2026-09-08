"""Service payload builders for market-analysis routes."""

from __future__ import annotations

import os

from core.dex_routing import dex_quote_rpc_urls_for, dex_router_context_for, dex_router_name_for
from bot.execution.dex_costs import estimate_symbol_cost, parse_trade_usd_amounts
from bot.execution.execution_payload import PayloadConfig, build_execution_payload
from bot.execution.plan_quotes import quote_execution_plan
from market.observer import ASSETS
from market.strategy.arbitrage import build_pair_strategy_analysis, simulate_four_route_cycles
from web.control_panel import (
    REPO_ROOT,
    displayed_symbols,
    is_observer_running,
    observer_starting,
    quick_observer_running,
    restrict_extremes_to_symbols,
)
from market.binance_market.control_panel_config import unified_sampling_profile
from market.binance_market.strategy_runtime import (
    arbitrage_config_from_strategy as build_arbitrage_config,
    assert_fresh_execution_plan as build_assert_fresh_execution_plan,
    configured_fee_slippage_percent as read_configured_fee_slippage_percent,
    read_slippage_bps as read_market_slippage_bps,
)
from market.binance_market.opportunity_health import opportunity_health_rows, opportunity_health_summary
from web.control_panel_liquidation_base import aave_rpc_urls, database_url_or_none
from web.control_panel_market import (
    latest_arbitrage_simulation,
    latest_binance_extremes_file,
    latest_candidate_price_rows,
    latest_reference_price,
    recent_observations,
    safe_latest,
    strategy_config,
)
from core.control_panel_stats import (
    testnet_trade_stats as read_testnet_trade_stats,
    trade_stats as read_trade_stats,
)


def opportunity_health_payload(chain_selection) -> dict:
    running = quick_observer_running()
    symbols = displayed_symbols(running or observer_starting)
    binance_extremes = restrict_extremes_to_symbols(
        safe_latest(latest_binance_extremes_file),
        symbols,
    )
    config = strategy_config()
    rows = opportunity_health_rows(binance_extremes, config)
    return {
        "rows": rows,
        "summary": opportunity_health_summary(rows, config),
        "sampling_profile": unified_sampling_profile(config),
        "binance_extremes": binance_extremes,
        "chain_selection": chain_selection,
    }


def strategy_config_payload() -> dict:
    config = strategy_config()
    return {
        "config": config,
        "sampling_profile": unified_sampling_profile(config),
        "running": is_observer_running(),
    }


def trade_stats_payload() -> dict:
    return {"stats": safe_latest(lambda: read_trade_stats(database_url_or_none()))}


def testnet_trade_stats_payload() -> dict:
    return {"stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT))}


def observations_payload(symbol: str, limit: int, *, recent_binance_price_history) -> dict:
    rows = recent_observations(symbol, limit) if symbol in ASSETS else []
    mode = "aave_observations" if rows else "binance_price_history"
    if not rows:
        rows = recent_binance_price_history(symbol, limit)
    return {
        "symbol": symbol,
        "limit": limit,
        "mode": mode,
        "supports_aave": symbol in ASSETS and mode == "aave_observations",
        "supports_dex_costs": symbol in ASSETS and mode == "aave_observations",
        "fee_slippage_percent": read_configured_fee_slippage_percent(),
        "rows": rows,
    }


def pair_route_profits_payload(
    x_symbol: str,
    y_symbol: str,
    initial_amount: float,
    *,
    chain_selection,
) -> dict:
    rows = latest_candidate_price_rows([x_symbol, y_symbol])
    if x_symbol not in rows or y_symbol not in rows:
        return {
            "x_symbol": x_symbol,
            "y_symbol": y_symbol,
            "initial_amount": initial_amount,
            "routes": [],
            "error": "route profit needs both symbols in binance_candidate_price_history",
        }
    config = build_arbitrage_config(strategy_config())
    routes = simulate_four_route_cycles(
        rows[x_symbol],
        rows[y_symbol],
        config,
        initial_amount,
    )
    analysis = build_pair_strategy_analysis(
        rows[x_symbol],
        rows[y_symbol],
        config,
        initial_amount=initial_amount,
        routes=routes,
    )
    router_context = dex_router_context_for(chain_selection)
    return {
        "x_symbol": x_symbol,
        "y_symbol": y_symbol,
        "initial_amount": initial_amount,
        "prices": {"x": rows[x_symbol], "y": rows[y_symbol]},
        "routes": routes,
        "analysis": analysis,
        "chain_selection": chain_selection,
        "router_context": router_context,
    }


def execution_plan_quote_payload(chain_selection) -> dict:
    simulation = latest_arbitrage_simulation()
    if not simulation or not simulation.get("execution_plan"):
        raise LookupError("latest arbitrage result has no execution_plan")
    build_assert_fresh_execution_plan(simulation, strategy_config())
    router_context = dex_router_context_for(chain_selection)
    router = router_context.get("router_address")
    quote = _quote_from_any_rpc(simulation, router, chain_selection)
    return {"quote": quote, "chain_selection": chain_selection, "router_context": router_context}


def execution_plan_payload_payload(
    chain_selection,
    *,
    min_profit_usdc: float,
    deadline_seconds: int,
) -> dict:
    simulation = latest_arbitrage_simulation()
    if not simulation or not simulation.get("execution_plan"):
        raise LookupError("latest arbitrage result has no execution_plan")
    build_assert_fresh_execution_plan(simulation, strategy_config())
    router_context = dex_router_context_for(chain_selection)
    router = router_context.get("router_address")
    quote = _quote_from_any_rpc(simulation, router, chain_selection)
    payload = build_execution_payload(
        simulation["execution_plan"],
        quote,
        PayloadConfig(
            min_profit_usdc=min_profit_usdc,
            deadline_seconds=deadline_seconds,
        ),
    )
    return {"payload": payload, "chain_selection": chain_selection, "router_context": router_context}


def dex_costs_payload(symbol: str, *, chain_selection) -> dict:
    amounts = parse_trade_usd_amounts(os.getenv("DEX_COST_USD_AMOUNTS"))
    reference_price = latest_reference_price(symbol)
    router_context = dex_router_context_for(chain_selection)
    router = router_context.get("router_address")
    costs = None
    last_error = None
    for rpc_url in dex_quote_rpc_urls_for(router_context, aave_rpc_urls()):
        try:
            costs = [estimate_symbol_cost(rpc_url, symbol, amount, reference_price, router) for amount in amounts]
            break
        except Exception as exc:  # noqa: BLE001 - route-level RPC fallback
            last_error = exc
    if costs is None:
        raise last_error or RuntimeError("all AAVE RPC candidates failed")
    payload = [
        {
            "amount_usd": quote.amount_usd,
            "buy_cost_percent": quote.buy_cost_percent,
            "sell_cost_percent": quote.sell_cost_percent,
            "roundtrip_cost_percent": quote.roundtrip_cost_percent,
            "buy_price_usd": quote.buy_price_usd,
            "sell_price_usd": quote.sell_price_usd,
            "token_amount": quote.token_amount,
        }
        for quote in costs
        if quote is not None
    ]
    return {
        "symbol": symbol,
        "dex_name": dex_router_name_for(router_context),
        "reference_price_usd": reference_price,
        "costs": payload,
        "chain_selection": chain_selection,
        "router_context": router_context,
    }


def _quote_from_any_rpc(simulation, router, chain_selection):
    last_error = None
    for rpc_url in dex_quote_rpc_urls_for(dex_router_context_for(chain_selection), aave_rpc_urls()):
        try:
            return quote_execution_plan(
                simulation["execution_plan"],
                rpc_url=rpc_url,
                router_address=router,
                slippage_bps=read_market_slippage_bps(strategy_config()),
            )
        except Exception as exc:  # noqa: BLE001 - route-level RPC fallback
            last_error = exc
    raise last_error or RuntimeError("all AAVE RPC candidates failed")


__all__ = [
    "dex_costs_payload",
    "execution_plan_payload_payload",
    "execution_plan_quote_payload",
    "observations_payload",
    "opportunity_health_payload",
    "pair_route_profits_payload",
    "strategy_config_payload",
    "testnet_trade_stats_payload",
    "trade_stats_payload",
]
