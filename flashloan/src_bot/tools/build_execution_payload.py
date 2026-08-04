from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution.execution_payload import PayloadConfig, build_execution_payload, write_payload_file


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Build contract SwapStep payload from execution_plan and DEX quote JSON.")
    parser.add_argument("--plan", required=True, help="Path to JSON containing execution_plan or the plan object itself.")
    parser.add_argument("--quote", required=True, help="Path to JSON containing quote or the quote object itself.")
    parser.add_argument("--out", default="deployments/execution-payload.json", help="Output JSON path.")
    parser.add_argument("--min-profit-usdc", type=float, default=0.0)
    parser.add_argument("--deadline-seconds", type=int, default=600)
    args = parser.parse_args()

    plan_doc = read_json(Path(args.plan))
    quote_doc = read_json(Path(args.quote))
    execution_plan = plan_doc.get("execution_plan") or plan_doc.get("plan") or plan_doc
    quote = quote_doc.get("quote") or quote_doc
    payload = build_execution_payload(
        execution_plan,
        quote,
        PayloadConfig(min_profit_usdc=args.min_profit_usdc, deadline_seconds=args.deadline_seconds),
    )
    output_path = write_payload_file(payload, args.out)
    print(f"payloadFile={output_path}")
    print(f"steps={len(payload['contract']['mockFundedExecutor']['steps'])}")
    print(f"aaveCompatible={payload['contract']['aaveSequentialFlashLoanExecutor']['compatible']}")


if __name__ == "__main__":
    main()
