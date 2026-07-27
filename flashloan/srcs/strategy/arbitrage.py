from dataclasses import dataclass
from typing import Optional


STABLE_SYMBOL = "USDC"


@dataclass(frozen=True)
class ArbitrageConfig:
    notional_usd: float
    trade_fee_percent: float
    flashloan_fee_percent: float
    min_window_spread_percent: float = 0.30
    min_paper_profit_usd: float = 0.0
    fee_reserve_percent: float = 0.0
    basket_size: int = 5
    executable_symbols: tuple[str, ...] = ()


STRATEGY_ROUTES = {
    "strategy_1_forward_x_to_usdc_to_y_to_x": {
        "base_strategy": "strategy_1",
        "direction": "forward",
        "route": ("x", STABLE_SYMBOL, "y", "x"),
        "price_phases": ("end", "end", "start"),
    },
    "strategy_1_reverse_y_to_x_to_usdc_to_y": {
        "base_strategy": "strategy_1",
        "direction": "reverse",
        "route": ("y", "x", STABLE_SYMBOL, "y"),
        "price_phases": ("start", "end", "end"),
    },
    "strategy_2_forward_x_to_y_to_usdc_to_x": {
        "base_strategy": "strategy_2",
        "direction": "forward",
        "route": ("x", "y", STABLE_SYMBOL, "x"),
        "price_phases": ("start", "end", "end"),
    },
    "strategy_2_reverse_y_to_usdc_to_x_to_y": {
        "base_strategy": "strategy_2",
        "direction": "reverse",
        "route": ("y", STABLE_SYMBOL, "x", "y"),
        "price_phases": ("end", "end", "start"),
    },
}


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


def _valid_leg(row: dict) -> bool:
    try:
        return (
            float(row["start_price"]) > 0
            and float(row["end_price"]) > 0
            and "symbol" in row
            and "change_percent" in row
        )
    except (KeyError, TypeError, ValueError):
        return False


def _simulate_route_cycle(
    up: dict,
    down: dict,
    config: ArbitrageConfig,
    notional_usd: float,
    strategy: str,
) -> Optional[dict]:
    route_config = STRATEGY_ROUTES[strategy]
    if (
        notional_usd <= 0
        or up.get("symbol") == down.get("symbol")
        or not _valid_leg(up)
        or not _valid_leg(down)
    ):
        return None

    rows = {"x": up, "y": down}
    route_symbols = [
        STABLE_SYMBOL if item == STABLE_SYMBOL else rows[item]["symbol"]
        for item in route_config["route"]
    ]

    def price(symbol: str, phase: str) -> float:
        if symbol == STABLE_SYMBOL:
            return 1.0
        row = up if symbol == up["symbol"] else down
        return float(row[f"{phase}_price"])

    trade_fee_rate = max(0.0, config.trade_fee_percent) / 100
    flashloan_fee_rate = max(0.0, config.flashloan_fee_percent) / 100
    fee_factor = max(0.0, 1 - trade_fee_rate)

    borrow_symbol = route_symbols[0]
    borrow_start = price(borrow_symbol, "start")
    borrow_end = price(borrow_symbol, "end")
    borrowed_amount = notional_usd / borrow_start
    current_amount = borrowed_amount
    route_steps = []
    for index, phase in enumerate(route_config["price_phases"]):
        from_symbol = route_symbols[index]
        to_symbol = route_symbols[index + 1]
        input_amount = current_amount
        output_amount = input_amount * price(from_symbol, phase) / price(to_symbol, phase) * fee_factor
        route_steps.append(
            {
                "from_symbol": from_symbol,
                "to_symbol": to_symbol,
                "input_amount": input_amount,
                "output_amount": output_amount,
                "input_price_usd": price(from_symbol, phase),
                "output_price_usd": price(to_symbol, phase),
                "price_phase": phase,
            }
        )
        current_amount = output_amount

    borrow_to_repay = borrowed_amount * (1 + flashloan_fee_rate)
    profit_borrow = current_amount - borrow_to_repay
    profit_usd = profit_borrow * borrow_end
    profit_percent = profit_borrow / borrowed_amount * 100
    gross_relative_edge_percent = profit_percent

    return {
        "strategy": strategy,
        "base_strategy": route_config["base_strategy"],
        "direction": route_config["direction"],
        "route_symbols": route_symbols,
        "route_steps": route_steps,
        "borrow_symbol": borrow_symbol,
        "swap_symbol": next(symbol for symbol in route_symbols[1:-1] if symbol != STABLE_SYMBOL),
        "borrow_change_percent": float((up if borrow_symbol == up["symbol"] else down)["change_percent"]),
        "swap_change_percent": float((down if borrow_symbol == up["symbol"] else up)["change_percent"]),
        "borrow_start_price": borrow_start,
        "borrow_end_price": borrow_end,
        "swap_start_price": price(next(symbol for symbol in route_symbols[1:-1] if symbol != STABLE_SYMBOL), "start"),
        "swap_end_price": price(next(symbol for symbol in route_symbols[1:-1] if symbol != STABLE_SYMBOL), "end"),
        "notional_usd": notional_usd,
        "borrowed_amount": borrowed_amount,
        "swap_bought": route_steps[0]["output_amount"],
        "usdc_after_selling_swap": next(
            (step["output_amount"] for step in route_steps if step["to_symbol"] == STABLE_SYMBOL),
            0.0,
        ),
        "usdt_after_selling_swap": next(
            (step["output_amount"] for step in route_steps if step["to_symbol"] == STABLE_SYMBOL),
            0.0,
        ),
        "borrow_rebought": current_amount,
        "borrow_to_repay": borrow_to_repay,
        "profit_borrow": profit_borrow,
        "profit_usd": profit_usd,
        "profit_percent": profit_percent,
        "gross_relative_edge_percent": gross_relative_edge_percent,
        "profitable": profit_borrow > 0,
    }


def _add_legacy_pair_fields(pair: dict, up: dict, down: dict) -> dict:
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


def simulate_pair(up: dict, down: dict, config: ArbitrageConfig, notional_usd: float) -> Optional[dict]:
    if (
        notional_usd <= 0
        or up.get("symbol") == down.get("symbol")
        or not _valid_leg(up)
        or not _valid_leg(down)
    ):
        return None

    candidates = [
        _simulate_route_cycle(up, down, config, notional_usd, "strategy_1_forward_x_to_usdc_to_y_to_x"),
        _simulate_route_cycle(up, down, config, notional_usd, "strategy_2_forward_x_to_y_to_usdc_to_x"),
    ]
    forward_candidates = [candidate for candidate in candidates if candidate]
    if len(forward_candidates) != 2:
        return None

    decision = choose_signed_strategy(forward_candidates[0]["profit_usd"], forward_candidates[1]["profit_usd"])
    selected_strategy = {
        ("strategy_1", "forward"): "strategy_1_forward_x_to_usdc_to_y_to_x",
        ("strategy_1", "reverse"): "strategy_1_reverse_y_to_x_to_usdc_to_y",
        ("strategy_2", "forward"): "strategy_2_forward_x_to_y_to_usdc_to_x",
        ("strategy_2", "reverse"): "strategy_2_reverse_y_to_usdc_to_x_to_y",
    }[(decision["base_strategy"], decision["direction"])]
    selected = _simulate_route_cycle(up, down, config, notional_usd, selected_strategy)
    if not selected:
        return None

    reverse_candidates = [
        _simulate_route_cycle(up, down, config, notional_usd, "strategy_1_reverse_y_to_x_to_usdc_to_y"),
        _simulate_route_cycle(up, down, config, notional_usd, "strategy_2_reverse_y_to_usdc_to_x_to_y"),
    ]
    viable_candidates = [candidate for candidate in [*forward_candidates, *reverse_candidates] if candidate]
    best = selected
    alternate = min(forward_candidates, key=lambda candidate: candidate["profit_usd"])
    best = _add_legacy_pair_fields(best, up, down)
    best["best_strategy"] = best["strategy"]
    best["alternate_strategy"] = alternate["strategy"]
    best["signed_strategy_decision"] = decision
    best["m1_profit_usd"] = forward_candidates[0]["profit_usd"]
    best["m2_profit_usd"] = forward_candidates[1]["profit_usd"]
    best["selected_signed_profit_usd"] = decision["signed_profit_usd"]
    best["selected_direction_score_usd"] = decision["selection_score_usd"]
    best["selected_expected_profit_usd"] = best["profit_usd"]
    best["candidate_strategies"] = viable_candidates
    best["x_symbol"] = up["symbol"]
    best["y_symbol"] = down["symbol"]
    return best


def simulate_basket(extremes: dict, config: ArbitrageConfig) -> Optional[dict]:
    if config.notional_usd <= 0:
        return None

    executable = {symbol.upper() for symbol in config.executable_symbols}
    top = [
        row
        for row in extremes.get("top", [])
        if _valid_leg(row) and (not executable or str(row["symbol"]).upper() in executable)
    ]
    bottom = [
        row
        for row in extremes.get("bottom", [])
        if _valid_leg(row) and (not executable or str(row["symbol"]).upper() in executable)
    ]
    candidate_rows = select_cross_pair_rows(top, bottom)
    if not candidate_rows:
        return None

    provisional_notional = config.notional_usd / max(1, min(len(candidate_rows), config.basket_size))
    candidates = []
    for top_row, bottom_row in candidate_rows:
        pair = simulate_pair(top_row, bottom_row, config, provisional_notional)
        if pair:
            candidates.append(pair)

    if not candidates:
        return None

    ranked_candidates = sorted(
        candidates,
        key=lambda pair: pair["selected_direction_score_usd"],
        reverse=True,
    )
    pairs = ranked_candidates[: max(1, config.basket_size)]
    per_leg_notional = config.notional_usd / len(pairs)
    normalized_pairs = []
    for index, pair in enumerate(pairs):
        normalized = simulate_pair(
            {"symbol": pair["x_symbol"], "start_price": pair["a_start_price"], "end_price": pair["a_end_price"], "change_percent": pair["a_change_percent"]},
            {"symbol": pair["y_symbol"], "start_price": pair["b_start_price"], "end_price": pair["b_end_price"], "change_percent": pair["b_change_percent"]},
            config,
            per_leg_notional,
        )
        if normalized:
            normalized["rank"] = index + 1
            normalized_pairs.append(normalized)
    pairs = normalized_pairs
    if not pairs:
        return None

    total_profit_usd = sum(pair["selected_expected_profit_usd"] for pair in pairs)
    total_direction_score_usd = sum(pair["selected_direction_score_usd"] for pair in pairs)
    fee_reserve_usd = config.notional_usd * max(0.0, config.fee_reserve_percent) / 100
    net_signal_profit_usd = total_direction_score_usd - fee_reserve_usd
    total_repay_usd = sum(pair["borrow_to_repay"] * pair["borrow_end_price"] for pair in pairs)
    total_buyback_usd = sum(pair["borrow_rebought"] * pair["borrow_end_price"] for pair in pairs)
    primary = pairs[0]
    window_spread_percent = float(primary["a_change_percent"] - primary["b_change_percent"])
    signal = (
        window_spread_percent >= max(0.0, config.min_window_spread_percent)
        and net_signal_profit_usd >= max(0.0, config.min_paper_profit_usd)
    )
    blocked_reasons = []
    if window_spread_percent < max(0.0, config.min_window_spread_percent):
        blocked_reasons.append("window_spread_below_threshold")
    if net_signal_profit_usd < max(0.0, config.min_paper_profit_usd):
        blocked_reasons.append("candidate_score_below_threshold")
    execution_plan = build_execution_plan(pairs, config) if signal else None

    return {
        "observed_at": extremes["observed_at"],
        "window_seconds": extremes["window_seconds"],
        "sample_count": extremes["sample_count"],
        "price_source": extremes.get("price_source", "unknown"),
        "strategy": "cross_grid_best_closed_cycle",
        "basket_size": len(pairs),
        "candidate_pair_count": len(candidate_rows),
        "evaluated_strategy_count": len(candidate_rows) * 4,
        "notional_usd": config.notional_usd,
        "per_leg_notional_usd": per_leg_notional,
        "trade_fee_percent": config.trade_fee_percent,
        "flashloan_fee_percent": config.flashloan_fee_percent,
        "a_symbol": primary["a_symbol"],
        "b_symbol": primary["b_symbol"],
        "a_change_percent": primary["a_change_percent"],
        "b_change_percent": primary["b_change_percent"],
        "borrowed_b": primary["borrowed_b"],
        "a_bought": primary["a_bought"],
        "usdc_after_selling_a": primary["usdc_after_selling_a"],
        "usdt_after_selling_a": primary["usdt_after_selling_a"],
        "b_rebought": primary["b_rebought"],
        "b_to_repay": primary["b_to_repay"],
        "profit_b": primary["profit_b"],
        "profit_usd": total_profit_usd,
        "paper_route_profit_usd": total_profit_usd,
        "candidate_score_usd": total_direction_score_usd,
        "fee_reserve_usd": fee_reserve_usd,
        "net_signal_profit_usd": net_signal_profit_usd,
        "profit_percent": total_profit_usd / config.notional_usd * 100,
        "candidate_score_percent": total_direction_score_usd / config.notional_usd * 100,
        "gross_relative_edge_percent": primary["gross_relative_edge_percent"],
        "best_strategy": primary["best_strategy"],
        "m1_profit_usd": primary["m1_profit_usd"],
        "m2_profit_usd": primary["m2_profit_usd"],
        "selected_signed_profit_usd": primary["selected_signed_profit_usd"],
        "selected_direction_score_usd": primary["selected_direction_score_usd"],
        "selected_expected_profit_usd": primary["selected_expected_profit_usd"],
        "route_symbols": primary["route_symbols"],
        "borrow_symbol": primary["borrow_symbol"],
        "swap_symbol": primary["swap_symbol"],
        "borrowed_amount": primary["borrowed_amount"],
        "swap_bought": primary["swap_bought"],
        "borrow_rebought": primary["borrow_rebought"],
        "borrow_to_repay": primary["borrow_to_repay"],
        "window_spread_percent": window_spread_percent,
        "min_window_spread_percent": config.min_window_spread_percent,
        "min_paper_profit_usd": config.min_paper_profit_usd,
        "fee_reserve_percent": config.fee_reserve_percent,
        "total_repay_usd": total_repay_usd,
        "total_buyback_usd": total_buyback_usd,
        "remaining_usd": total_profit_usd,
        "profitable": signal,
        "signal": signal,
        "blocked_reasons": blocked_reasons,
        "pairs": pairs,
        "execution_plan": execution_plan,
    }


def select_cross_pair_rows(top: list[dict], bottom: list[dict]) -> list[tuple[dict, dict]]:
    pairs: list[tuple[dict, dict]] = []
    for top_row in top:
        top_symbol = str(top_row["symbol"]).upper()
        for bottom_row in bottom:
            bottom_symbol = str(bottom_row["symbol"]).upper()
            if bottom_symbol != top_symbol:
                pairs.append((top_row, bottom_row))
    return pairs


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


def build_execution_plan(pairs: list[dict], config: ArbitrageConfig) -> dict:
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
        "description": "compare every rising/falling token pair across two signed closed-cycle strategies, execute forward when profit is positive and reverse when profit is negative",
        "strategy_model": "m_by_n_grid_signed_best_of_two_closed_cycles",
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
