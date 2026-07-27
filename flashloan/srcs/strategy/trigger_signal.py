from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TriggerConfig:
    min_up_change_percent: float = 1.0
    min_down_change_percent: float = 1.0
    executable_symbols: tuple[str, ...] = ()


def build_trigger_signal(extremes: dict, config: TriggerConfig) -> Optional[dict]:
    top = _filter_rows(extremes.get("top", []), config)
    bottom = _filter_rows(extremes.get("bottom", []), config)
    if not top or not bottom:
        return None

    up, down = top[0], bottom[0]
    if str(up["symbol"]).upper() == str(down["symbol"]).upper():
        return None

    up_change = float(up["change_percent"])
    down_change = float(down["change_percent"])
    up_ok = up_change >= max(0.0, config.min_up_change_percent)
    down_ok = abs(down_change) >= max(0.0, config.min_down_change_percent) and down_change < 0
    signal = up_ok and down_ok
    blocked_reasons = []
    if not up_ok:
        blocked_reasons.append("top_gainer_below_threshold")
    if not down_ok:
        blocked_reasons.append("top_loser_below_threshold")

    spread = up_change - down_change
    return {
        "observed_at": extremes["observed_at"],
        "window_seconds": extremes["window_seconds"],
        "sample_count": extremes["sample_count"],
        "price_source": extremes.get("price_source", "unknown"),
        "strategy": "onchain_dynamic_trigger",
        "best_strategy": "onchain_dynamic_decision",
        "basket_size": 1,
        "candidate_pair_count": 1,
        "evaluated_strategy_count": 4,
        "a_symbol": up["symbol"],
        "b_symbol": down["symbol"],
        "x_symbol": up["symbol"],
        "y_symbol": down["symbol"],
        "borrow_symbol": up["symbol"],
        "swap_symbol": down["symbol"],
        "route_symbols": [up["symbol"], "ONCHAIN_DYNAMIC", down["symbol"]],
        "a_change_percent": up_change,
        "b_change_percent": down_change,
        "x_change_percent": up_change,
        "y_change_percent": down_change,
        "a_start_price": float(up["start_price"]),
        "a_end_price": float(up["end_price"]),
        "b_start_price": float(down["start_price"]),
        "b_end_price": float(down["end_price"]),
        "window_spread_percent": spread,
        "min_window_spread_percent": config.min_up_change_percent + config.min_down_change_percent,
        "notional_usd": 0.0,
        "per_leg_notional_usd": 0.0,
        "trade_fee_percent": 0.0,
        "flashloan_fee_percent": 0.0,
        "fee_reserve_percent": 0.0,
        "fee_reserve_usd": 0.0,
        "borrowed_b": 0.0,
        "a_bought": 0.0,
        "usdc_after_selling_a": 0.0,
        "usdt_after_selling_a": 0.0,
        "b_rebought": 0.0,
        "b_to_repay": 0.0,
        "profit_b": 0.0,
        "profit_usd": 0.0,
        "paper_route_profit_usd": 0.0,
        "candidate_score_usd": spread,
        "net_signal_profit_usd": spread,
        "profit_percent": 0.0,
        "candidate_score_percent": spread,
        "gross_relative_edge_percent": spread,
        "m1_profit_usd": 0.0,
        "m2_profit_usd": 0.0,
        "selected_signed_profit_usd": 0.0,
        "selected_direction_score_usd": spread,
        "selected_expected_profit_usd": 0.0,
        "total_repay_usd": 0.0,
        "total_buyback_usd": 0.0,
        "remaining_usd": 0.0,
        "min_paper_profit_usd": 0.0,
        "profitable": signal,
        "signal": signal,
        "trigger_signal": signal,
        "trigger_model": "top_gainer_top_loser_200ms_1pct",
        "onchain_decision_required": True,
        "blocked_reasons": blocked_reasons,
        "pairs": [],
        "execution_plan": None,
    }


def _filter_rows(rows: list[dict], config: TriggerConfig) -> list[dict]:
    executable = {symbol.upper() for symbol in config.executable_symbols}
    return [
        row
        for row in rows
        if _valid_row(row) and (not executable or str(row["symbol"]).upper() in executable)
    ]


def _valid_row(row: dict) -> bool:
    try:
        return (
            "symbol" in row
            and float(row["start_price"]) > 0
            and float(row["end_price"]) > 0
            and "change_percent" in row
        )
    except (TypeError, ValueError):
        return False
