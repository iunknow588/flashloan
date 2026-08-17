"""Record UI signal evidence with Binance prices and per-pool live quotes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_STATE = SRC_ROOT / "runtime" / "state" / "latest_extremes.json"
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_EVIDENCE_DIR = SRC_ROOT / "runtime" / "evidence" / "ui_signal_pool_quotes"
DEFAULT_LATEST_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_ui_signal_pool_evidence.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from market.binance_market.service import build_binance_market_state, load_cow_supported_token_registry
from runtime.ui_signal_pool_evidence import (
    build_ui_signal_pool_evidence,
    write_ui_signal_pool_evidence,
)
from strategy.arbitrage import ArbitrageConfig


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _base_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    for suffix in ("USDT", "USDC"):
        if text.endswith(suffix) and text not in {"USDT", "USDC"}:
            return text[: -len(suffix)]
    return text


def _synthetic_market_state(route_path: list[str], amount: str) -> dict[str, Any]:
    x = _base_symbol(route_path[1])
    y = _base_symbol(route_path[2])
    return {
        "observed_at": None,
        "window_seconds": None,
        "price_source": "manual_route",
        "market_state_source": "manual_route",
        "cow_filter": {"network": "avalanche", "chain_id": 43114, "source": "manual_route"},
        "pairs": [
            {
                "rank": 1,
                "pair": f"{x} / {y}",
                "x_symbol": x,
                "y_symbol": y,
                "x_base_symbol": x,
                "y_base_symbol": y,
                "candidate_basis": "manual_route",
                "trigger_source": "manual_route",
                "quote_required": True,
                "route_results": [
                    {
                        "route_no": 1,
                        "route": ["USDC", x, y, "USDC"],
                        "initial_amount": amount,
                        "initial_symbol": "USDC",
                        "priority_reason": "manual_forward",
                        "quote_required": True,
                    },
                    {
                        "route_no": 2,
                        "route": ["USDC", y, x, "USDC"],
                        "initial_amount": amount,
                        "initial_symbol": "USDC",
                        "priority_reason": "manual_reverse",
                        "quote_required": True,
                    },
                ],
            }
        ],
    }


def _route_path(raw: str) -> list[str]:
    path = [item for item in raw.replace(",", "->").split("->") if item.strip()]
    normalized = [_base_symbol(item) for item in path]
    if len(normalized) < 4 or normalized[0] != "USDC" or normalized[-1] != "USDC":
        raise ValueError("route path must look like USDC->X->Y->USDC")
    return normalized[:4]


def _build_current_ui_market_state(extremes: dict[str, Any], pair_limit: int) -> dict[str, Any]:
    token_cache = load_cow_supported_token_registry(cow_network="avalanche", database_url=None)
    return build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=ArbitrageConfig(
            notional_usd=1000.0,
            trade_fee_percent=0.1,
            flashloan_fee_percent=0.05,
        ),
        top_limit=50,
        bottom_limit=50,
        pair_side_limit=max(1, min(int(pair_limit), 5)),
        cow_display_limit=50,
        cow_network="avalanche",
        registry=token_cache.get("registry") or {},
        min_spread_percent=0.0,
        min_side_change_percent=0.0,
        min_token_price_usd=0.01,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-state", type=Path, default=DEFAULT_MARKET_STATE)
    parser.add_argument("--pool-cache", type=Path, default=DEFAULT_POOL_CACHE)
    parser.add_argument("--route-path", default="")
    parser.add_argument("--pair-limit", type=int, default=5)
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--usdc-amount", type=int, default=1_000_000_000)
    parser.add_argument("--min-profit-usdc", type=int, default=1)
    parser.add_argument("--premium-bps", type=int, default=5)
    parser.add_argument("--record-empty", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT)
    return parser.parse_args()


def main() -> int:
    load_env_files(__file__)
    args = parse_args()
    pool_cache = args.pool_cache if args.pool_cache.is_absolute() else SRC_ROOT / args.pool_cache
    if args.route_path:
        market_state = _synthetic_market_state(_route_path(args.route_path), str(args.usdc_amount / 1_000_000))
        extremes = {}
    else:
        market_state_path = args.market_state if args.market_state.is_absolute() else SRC_ROOT / args.market_state
        extremes = _read_json(market_state_path)
        market_state = _build_current_ui_market_state(extremes, args.pair_limit)
    evidence = build_ui_signal_pool_evidence(
        market_state=market_state,
        extremes=extremes,
        pool_cache_path=pool_cache,
        rpc_url=args.rpc_url,
        usdc_amount=max(1, args.usdc_amount),
        min_profit_usdc=max(0, args.min_profit_usdc),
        premium_bps=max(0, args.premium_bps),
    )
    if evidence["candidateCount"] == "0" and not args.record_empty:
        print(
            json.dumps(
                {
                    "recorded": False,
                    "reason": "no_ui_signal_candidate",
                    "candidateCount": evidence["candidateCount"],
                    "routeCount": evidence["routeCount"],
                    "blockedReasons": evidence["blockedReasons"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    result = write_ui_signal_pool_evidence(
        evidence,
        evidence_dir=args.evidence_dir,
        latest_output=args.latest_output,
    )
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
