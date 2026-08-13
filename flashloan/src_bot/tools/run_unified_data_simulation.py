"""Create a local-only paper-profit report from the latest market observer snapshot.

This tool never connects to a chain, uses no signer, and cannot deploy or broadcast.
It turns observer data into a reproducible paper route plus an intent_trade draft for
later fork/static-call verification.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATH = SRC_ROOT / "runtime" / "state" / "latest_extremes.json"
DEFAULT_OUTPUT_PATH = SRC_ROOT / "runtime" / "state" / "latest_unified_data_simulation.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.config_schema import parse_env_float, parse_env_int
from intent_trade.direct import build_triangular_onchain_intent_trade
from market.velocity_candidates import top_bottom_from_extremes
from strategy.arbitrage import ArbitrageConfig, simulate_basket
from strategy.limits import (
    DEFAULT_ARBITRAGE_BASKET_SIZE,
    DEFAULT_ARBITRAGE_FEE_RESERVE_PERCENT,
    DEFAULT_ARBITRAGE_FLASHLOAN_FEE_PERCENT,
    DEFAULT_ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT,
    DEFAULT_ARBITRAGE_NOTIONAL_USD,
    DEFAULT_ARBITRAGE_TRADE_FEE_PERCENT,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"input is unavailable or invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def simulation_defaults() -> dict[str, float | int]:
    return {
        "notional_usd": parse_env_float(
            "ARBITRAGE_NOTIONAL_USD", DEFAULT_ARBITRAGE_NOTIONAL_USD, minimum=0
        )[0],
        "trade_fee_percent": parse_env_float(
            "ARBITRAGE_TRADE_FEE_PERCENT",
            DEFAULT_ARBITRAGE_TRADE_FEE_PERCENT,
            minimum=0,
        )[0],
        "flashloan_fee_percent": parse_env_float(
            "ARBITRAGE_FLASHLOAN_FEE_PERCENT",
            DEFAULT_ARBITRAGE_FLASHLOAN_FEE_PERCENT,
            minimum=0,
        )[0],
        "fee_reserve_percent": parse_env_float(
            "ARBITRAGE_FEE_RESERVE_PERCENT",
            DEFAULT_ARBITRAGE_FEE_RESERVE_PERCENT,
            minimum=0,
        )[0],
        "min_window_spread_percent": parse_env_float(
            "ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT",
            DEFAULT_ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT,
            minimum=0,
        )[0],
        "basket_size": parse_env_int(
            "ARBITRAGE_BASKET_SIZE", DEFAULT_ARBITRAGE_BASKET_SIZE, minimum=1
        )[0],
        "gas_reserve_usdc": parse_env_float(
            "UNIFIED_PAPER_GAS_RESERVE_USDC", 0, minimum=0
        )[0],
        "public_mempool_penalty_usdc": parse_env_float(
            "UNIFIED_PAPER_PUBLIC_MEMPOOL_PENALTY_USDC", 0, minimum=0
        )[0],
        "slippage_penalty_usdc": parse_env_float(
            "UNIFIED_PAPER_SLIPPAGE_PENALTY_USDC", 0, minimum=0
        )[0],
        "other_known_costs_usdc": parse_env_float(
            "UNIFIED_PAPER_OTHER_KNOWN_COSTS_USDC", 0, minimum=0
        )[0],
    }


def _paper_profit_estimate(
    simulation: dict[str, Any] | None,
    *,
    gas_reserve_usdc: float,
    public_mempool_penalty_usdc: float,
    slippage_penalty_usdc: float,
    other_known_costs_usdc: float,
) -> dict[str, Any]:
    paper_profit = float((simulation or {}).get("net_signal_profit_usd") or 0.0)
    total_external_costs = (
        max(0.0, gas_reserve_usdc)
        + max(0.0, public_mempool_penalty_usdc)
        + max(0.0, slippage_penalty_usdc)
        + max(0.0, other_known_costs_usdc)
    )
    net_profit = paper_profit - total_external_costs
    return {
        "paperRouteProfitUsdc": round(paper_profit, 8),
        "gasReserveUsdc": round(max(0.0, gas_reserve_usdc), 8),
        "publicMempoolRiskPenaltyUsdc": round(
            max(0.0, public_mempool_penalty_usdc), 8
        ),
        "slippageRiskPenaltyUsdc": round(max(0.0, slippage_penalty_usdc), 8),
        "otherKnownCostsUsdc": round(max(0.0, other_known_costs_usdc), 8),
        "estimatedNetProfitUsdc": round(net_profit, 8),
        "profitableOnPaper": net_profit > 0,
        "semantics": "local_price_path_estimate_not_onchain_quote_or_profit_proof",
    }


def build_data_simulation(
    extremes: dict[str, Any],
    *,
    notional_usd: float,
    trade_fee_percent: float,
    flashloan_fee_percent: float,
    fee_reserve_percent: float,
    min_window_spread_percent: float,
    basket_size: int,
    gas_reserve_usdc: float = 0,
    public_mempool_penalty_usdc: float = 0,
    slippage_penalty_usdc: float = 0,
    other_known_costs_usdc: float = 0,
) -> dict[str, Any]:
    top, bottom = top_bottom_from_extremes(extremes, side_limit=max(1, basket_size))
    normalized_extremes = {
        **extremes,
        "top": top,
        "bottom": bottom,
    }
    config = ArbitrageConfig(
        notional_usd=max(0.0, notional_usd),
        trade_fee_percent=max(0.0, trade_fee_percent),
        flashloan_fee_percent=max(0.0, flashloan_fee_percent),
        fee_reserve_percent=max(0.0, fee_reserve_percent),
        min_window_spread_percent=max(0.0, min_window_spread_percent),
        min_paper_profit_usd=0,
        basket_size=max(1, basket_size),
    )
    simulation = simulate_basket(normalized_extremes, config)
    estimate = _paper_profit_estimate(
        simulation,
        gas_reserve_usdc=gas_reserve_usdc,
        public_mempool_penalty_usdc=public_mempool_penalty_usdc,
        slippage_penalty_usdc=slippage_penalty_usdc,
        other_known_costs_usdc=other_known_costs_usdc,
    )
    intent_draft = None
    if simulation:
        intent_draft = build_triangular_onchain_intent_trade(
            f"{simulation['x_symbol']}->{simulation['y_symbol']}",
            str(estimate["estimatedNetProfitUsdc"]),
            top,
            bottom,
        )
    reasons = [
        "local_data_simulation_only",
        "dex_quotes_not_verified",
        "runtime_trade_specs_not_exported",
        "fork_static_call_not_verified",
        "mainnet_contract_not_deployed",
        "broadcast_forbidden",
    ]
    if not simulation:
        reasons.append("no_paper_route_from_current_snapshot")
    elif not estimate["profitableOnPaper"]:
        reasons.append("estimated_net_profit_not_positive")
    return {
        "generatedAt": _utc_now_iso(),
        "mode": "local_data_simulation",
        "inputObservedAt": extremes.get("observed_at") or extremes.get("observedAt"),
        "inputSummary": {
            "windowSeconds": extremes.get("window_seconds"),
            "sampleCount": extremes.get("sample_count"),
            "priceSource": extremes.get("price_source"),
            "topCount": len(top),
            "bottomCount": len(bottom),
        },
        "simulationConfig": {
            "notionalUsd": config.notional_usd,
            "tradeFeePercent": config.trade_fee_percent,
            "flashloanFeePercent": config.flashloan_fee_percent,
            "feeReservePercent": config.fee_reserve_percent,
            "minWindowSpreadPercent": config.min_window_spread_percent,
            "basketSize": config.basket_size,
        },
        "paperSimulation": simulation,
        "profitEstimate": estimate,
        "intentTradeDraft": intent_draft,
        "deploymentEligible": False,
        "broadcastEligible": False,
        "nextRequiredStage": (
            "export_runtime_trade_specs_then_run_avalanche_fork_static_call"
            if estimate["profitableOnPaper"]
            else "continue_observer_data_collection_and_quote_verification"
        ),
        "blockedReasons": reasons,
    }


def parse_args() -> argparse.Namespace:
    defaults = simulation_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--notional-usd", type=float, default=defaults["notional_usd"])
    parser.add_argument("--trade-fee-percent", type=float, default=defaults["trade_fee_percent"])
    parser.add_argument(
        "--flashloan-fee-percent", type=float, default=defaults["flashloan_fee_percent"]
    )
    parser.add_argument(
        "--fee-reserve-percent", type=float, default=defaults["fee_reserve_percent"]
    )
    parser.add_argument(
        "--min-window-spread-percent",
        type=float,
        default=defaults["min_window_spread_percent"],
    )
    parser.add_argument("--basket-size", type=int, default=defaults["basket_size"])
    parser.add_argument("--gas-reserve-usdc", type=float, default=defaults["gas_reserve_usdc"])
    parser.add_argument(
        "--public-mempool-penalty-usdc",
        type=float,
        default=defaults["public_mempool_penalty_usdc"],
    )
    parser.add_argument(
        "--slippage-penalty-usdc",
        type=float,
        default=defaults["slippage_penalty_usdc"],
    )
    parser.add_argument(
        "--other-known-costs-usdc",
        type=float,
        default=defaults["other_known_costs_usdc"],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input if args.input.is_absolute() else SRC_ROOT / args.input
    output_path = args.output if args.output.is_absolute() else SRC_ROOT / args.output
    report = build_data_simulation(
        _read_json_object(input_path),
        notional_usd=args.notional_usd,
        trade_fee_percent=args.trade_fee_percent,
        flashloan_fee_percent=args.flashloan_fee_percent,
        fee_reserve_percent=args.fee_reserve_percent,
        min_window_spread_percent=args.min_window_spread_percent,
        basket_size=args.basket_size,
        gas_reserve_usdc=args.gas_reserve_usdc,
        public_mempool_penalty_usdc=args.public_mempool_penalty_usdc,
        slippage_penalty_usdc=args.slippage_penalty_usdc,
        other_known_costs_usdc=args.other_known_costs_usdc,
    )
    _write_json_atomic(output_path, report)
    print(json.dumps({"ok": True, "output": str(output_path), **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
