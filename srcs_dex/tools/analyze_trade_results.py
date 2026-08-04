import argparse
import json
from collections import Counter
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def estimate_gas_usdc(row: dict, gas_price_gwei: float, native_price_usdc: float) -> float:
    gas_used = as_float(row.get("gasUsed") or row.get("gas_used"))
    if gas_used <= 0 or gas_price_gwei <= 0 or native_price_usdc <= 0:
        return 0.0
    native_amount = gas_used * gas_price_gwei / 1_000_000_000
    return native_amount * native_price_usdc


def row_profit_usdc(row: dict) -> float:
    for key in ("profitUsdc", "profit_usdc", "netProfitUsdc", "net_profit_usdc"):
        if key in row:
            return as_float(row.get(key))
    return 0.0


def summarize_trades(rows: list[dict], gas_price_gwei: float, native_price_usdc: float) -> dict:
    total = len(rows)
    successes = [row for row in rows if bool(row.get("success"))]
    failures = [row for row in rows if not bool(row.get("success"))]
    failure_reasons = Counter(str(row.get("error") or "unknown") for row in failures)

    gross_profit_usdc = sum(row_profit_usdc(row) for row in successes)
    gas_cost_usdc = sum(estimate_gas_usdc(row, gas_price_gwei, native_price_usdc) for row in rows)
    external_net_profit_usdc = gross_profit_usdc - gas_cost_usdc

    return {
        "trade_count": total,
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_rate": (len(successes) / total) if total else 0.0,
        "gross_profit_usdc": gross_profit_usdc,
        "gas_cost_usdc": gas_cost_usdc,
        "external_net_profit_usdc": external_net_profit_usdc,
        "failure_reasons": dict(failure_reasons),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-trade external analysis with gas cost and success rate.")
    parser.add_argument("--log", default="contracts/deployments/fuji-trades.jsonl")
    parser.add_argument("--gas-price-gwei", type=float, default=0.0)
    parser.add_argument("--native-price-usdc", type=float, default=0.0)
    args = parser.parse_args()

    summary = summarize_trades(
        read_jsonl(Path(args.log)),
        gas_price_gwei=args.gas_price_gwei,
        native_price_usdc=args.native_price_usdc,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
