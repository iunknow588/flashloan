"""Pure strategy helpers extracted from market.strategy.arbitrage."""

from __future__ import annotations

from typing import Optional


def choose_signed_strategy(m1: float, m2: float) -> dict:
    values = [
        ("strategy_1", float(m1)),
        ("strategy_2", float(m2)),
    ]
    base_strategy, signed_profit = max(values, key=lambda item: abs(item[1]))
    direction = "forward" if signed_profit >= 0 else "reverse"
    strategy = f"{base_strategy}_{direction}"
    return {
        "base_strategy": base_strategy,
        "direction": direction,
        "strategy": strategy,
        "signed_profit_usd": signed_profit,
        "selection_score_usd": abs(signed_profit),
        "should_execute": signed_profit != 0,
    }


def valid_leg(row: dict) -> bool:
    try:
        return (
            float(row["start_price"]) > 0
            and float(row["end_price"]) > 0
            and "symbol" in row
            and "change_percent" in row
        )
    except (KeyError, TypeError, ValueError):
        return False


def add_legacy_pair_fields(pair: dict, up: dict, down: dict) -> dict:
    pair = dict(pair)
    pair.update(
        {
            "a_symbol": up["symbol"],
            "b_symbol": down["symbol"],
            "a_change_percent": float(up["change_percent"]),
            "b_change_percent": float(down["change_percent"]),
            "a_start_price": float(up["start_price"]),
            "a_end_price": float(up["end_price"]),
            "b_start_price": float(down["start_price"]),
            "b_end_price": float(down["end_price"]),
            "borrowed_b": pair["borrowed_amount"],
            "a_bought": pair["swap_bought"],
            "usdc_after_selling_a": pair["usdc_after_selling_swap"],
            "usdt_after_selling_a": pair["usdt_after_selling_swap"],
            "b_rebought": pair["borrow_rebought"],
            "b_to_repay": pair["borrow_to_repay"],
            "profit_b": pair["profit_borrow"],
        }
    )
    return pair


def select_cross_pair_rows(top: list[dict], bottom: list[dict]) -> list[tuple[dict, dict]]:
    pairs: list[tuple[dict, dict]] = []
    for top_row in top:
        top_symbol = str(top_row["symbol"]).upper()
        for bottom_row in bottom:
            bottom_symbol = str(bottom_row["symbol"]).upper()
            if bottom_symbol != top_symbol:
                pairs.append((top_row, bottom_row))
    return pairs


def pair_window_spread_percent(up: dict, down: dict) -> float:
    return float(up["change_percent"]) - float(down["change_percent"])


def select_disjoint_pair_rows(
    top: list[dict],
    bottom: list[dict],
    limit: int,
) -> list[tuple[dict, dict]]:
    selected: list[tuple[dict, dict]] = []
    used_symbols: set[str] = set()

    for top_row in top:
        top_symbol = str(top_row["symbol"]).upper()
        if top_symbol in used_symbols:
            continue
        for bottom_row in bottom:
            bottom_symbol = str(bottom_row["symbol"]).upper()
            if bottom_symbol == top_symbol or bottom_symbol in used_symbols:
                continue
            selected.append((top_row, bottom_row))
            used_symbols.add(top_symbol)
            used_symbols.add(bottom_symbol)
            break
        if len(selected) >= limit:
            break

    return selected


def ensure_closed_borrow_routes(pairs: list[dict]) -> None:
    for pair in pairs:
        route_symbols = pair.get("route_symbols") or []
        if len(route_symbols) < 2 or route_symbols[0] != route_symbols[-1]:
            raise ValueError(f"route does not end in borrowed asset: {route_symbols}")
        if pair.get("borrow_symbol") != route_symbols[0]:
            raise ValueError(f"borrow symbol does not match route start: {pair.get('borrow_symbol')} {route_symbols}")


def build_execution_plan(pairs: list[dict], config) -> dict:
    ensure_closed_borrow_routes(pairs)
    buy_steps = []
    sell_steps = []
    repay_steps = []

    for pair in pairs:
        rank = int(pair["rank"])
        buy_steps.append(
            {
                "rank": rank,
                "action": "swap_borrow_to_target",
                "strategy": pair["best_strategy"],
                "route_symbols": pair["route_symbols"],
                "from_symbol": pair["route_steps"][0]["from_symbol"],
                "to_symbol": pair["route_steps"][0]["to_symbol"],
                "input_amount": pair["route_steps"][0]["input_amount"],
                "input_price_usd": pair["route_steps"][0]["input_price_usd"],
                "output_amount": pair["route_steps"][0]["output_amount"],
                "output_price_usd": pair["route_steps"][0]["output_price_usd"],
                "notional_usd": pair["notional_usd"],
            }
        )
        sell_steps.append(
            {
                "rank": rank,
                "action": "swap_mid_leg",
                "strategy": pair["best_strategy"],
                "route_symbols": pair["route_symbols"],
                "from_symbol": pair["route_steps"][1]["from_symbol"],
                "to_symbol": pair["route_steps"][1]["to_symbol"],
                "input_amount": pair["route_steps"][1]["input_amount"],
                "input_price_usd": pair["route_steps"][1]["input_price_usd"],
                "output_amount": pair["route_steps"][1]["output_amount"],
                "output_price_usd": pair["route_steps"][1]["output_price_usd"],
            }
        )
        repay_steps.append(
            {
                "rank": rank,
                "action": "buy_repay_asset",
                "strategy": pair["best_strategy"],
                "route_symbols": pair["route_symbols"],
                "from_symbol": pair["route_steps"][2]["from_symbol"],
                "to_symbol": pair["route_steps"][2]["to_symbol"],
                "input_amount": pair["route_steps"][2]["input_amount"],
                "input_price_usd": pair["route_steps"][2]["input_price_usd"],
                "output_amount": pair["borrow_to_repay"],
                "output_price_usd": pair["route_steps"][2]["output_price_usd"],
                "flashloan_fee_percent": config.flashloan_fee_percent,
            }
        )

    buy_steps.sort(key=lambda step: step["rank"])
    sell_steps.sort(key=lambda step: step["rank"])
    repay_steps.sort(key=lambda step: step["rank"])

    return {
        "version": 1,
        "mode": "paper_sequential",
        "description": "borrow an Aave asset, swap through selected target/intermediate tokens, and end in the same borrowed asset for in-contract repay",
        "strategy_model": "m_by_n_grid_usdc_flashloan_stable_borrow_cycle",
        "route_invariant": "first route symbol must equal final route symbol; repay step must output the borrowed asset",
        "borrow_symbols": sorted({pair["borrow_symbol"] for pair in pairs}),
        "repay_symbols": sorted({pair["route_symbols"][-1] for pair in pairs}),
        "assumption_model": "static_pool_state_at_transaction_start",
        "execution_trigger": "pre_existing_dex_quote_edge_or_backrun_after_prior_transaction",
        "requires_static_dex_edge": True,
        "forbidden_assumptions": [
            "external users can modify involved pools during this transaction",
            "profit depends on pool prices changing while this transaction is executing",
        ],
        "buy_steps": buy_steps,
        "sell_steps": sell_steps,
        "repay_steps": repay_steps,
        "requires_contract": True,
        "requires_live_dex_quotes": True,
        "executable_symbols": list(config.executable_symbols),
    }


__all__ = [
    "add_legacy_pair_fields",
    "build_execution_plan",
    "choose_signed_strategy",
    "ensure_closed_borrow_routes",
    "pair_window_spread_percent",
    "select_cross_pair_rows",
    "select_disjoint_pair_rows",
    "valid_leg",
]
