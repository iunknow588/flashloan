import argparse
import json
import os
import sys
from pathlib import Path

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from execution.dex_costs import ROUTER_ABI, TRADER_JOE_V2_ROUTER
from execution.dynamic_quote import (
    DynamicQuoteConfig,
    quote_dynamic_candidate,
    token_amount_units_for_usd,
)
from execution.profit_guard import ProfitGuardConfig, evaluate_profit_guard
from market.observer import DEFAULT_RPC


load_env_files(__file__)


def default_signal_path() -> Path:
    return SRC_ROOT / "runtime" / "state" / "latest_executable_signal.json"


def read_candidate(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidate = payload.get("candidate") or payload.get("signal") or payload
    if not candidate.get("x_symbol") or not candidate.get("y_symbol"):
        raise RuntimeError("candidate must contain x_symbol and y_symbol")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real DEX quote precheck for latest dynamic candidate.")
    parser.add_argument("--input", default=str(default_signal_path()))
    parser.add_argument("--usd-amount", type=float, default=float(os.getenv("DYNAMIC_QUOTE_USD_AMOUNT", "100")))
    parser.add_argument("--slippage-bps", type=int, default=int(os.getenv("DYNAMIC_SLIPPAGE_BPS", "50")))
    parser.add_argument("--gas-cost-usdc", type=float, default=float(os.getenv("DYNAMIC_GAS_COST_USDC", "0")))
    parser.add_argument("--min-net-profit-usdc", type=float, default=float(os.getenv("DYNAMIC_MIN_NET_PROFIT_USDC", "0.01")))
    parser.add_argument("--safety-margin-usdc", type=float, default=float(os.getenv("DYNAMIC_SAFETY_MARGIN_USDC", "0")))
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    rpc_url = os.getenv("AVALANCHE_RPC", os.getenv("FUJI_RPC_URL", DEFAULT_RPC)).strip()
    router_address = os.getenv("DYNAMIC_DEX_ROUTER", os.getenv("FUJI_DEX_ROUTER", TRADER_JOE_V2_ROUTER)).strip()
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    router = w3.eth.contract(address=Web3.to_checksum_address(router_address), abi=ROUTER_ABI)

    candidate = read_candidate(Path(args.input))
    config = DynamicQuoteConfig(
        amount_x_units=token_amount_units_for_usd(router, candidate["x_symbol"], args.usd_amount),
        amount_y_units=token_amount_units_for_usd(router, candidate["y_symbol"], args.usd_amount),
        premium_bps=int(os.getenv("DYNAMIC_AAVE_PREMIUM_BPS", "5")),
        min_profit_usdc_units=int(os.getenv("DYNAMIC_MIN_PROFIT_USDC_UNITS", "1")),
    )
    result = quote_dynamic_candidate(router, candidate, config)
    guard = evaluate_profit_guard(
        result.get("best_quote"),
        ProfitGuardConfig(
            notional_usd=args.usd_amount,
            slippage_bps=args.slippage_bps,
            gas_cost_usdc=args.gas_cost_usdc,
            min_net_profit_usdc=args.min_net_profit_usdc,
            safety_margin_usdc=args.safety_margin_usdc,
        ),
    )
    result["profit_guard"] = guard
    result["net_profit_verified"] = bool(guard.get("net_profit_verified"))
    result["executable_signal"] = False
    result["blocked_reasons"] = [
        *result.get("blocked_reasons", []),
        *guard.get("blocked_reasons", []),
        "fork_or_fuji_simulation_not_verified",
    ]
    result["next_required_stage"] = "fork_or_fuji_static_simulation"

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
