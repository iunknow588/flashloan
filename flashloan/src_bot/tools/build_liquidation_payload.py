from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from execution.liquidation_payload import LiquidationExecutionPayloadConfig, build_liquidation_execution_payload


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Aave liquidation executor payload from account report JSON.")
    parser.add_argument("--report", required=True, help="Path to /api/liquidation/account JSON output.")
    parser.add_argument("--executor", required=True, help="Deployed AaveV3LiquidationExecutor address.")
    parser.add_argument("--router", required=True, help="DEX router address.")
    parser.add_argument("--deadline-seconds", type=int, default=300, help="Deadline offset from now.")
    parser.add_argument("--allow-zero-min-out", action="store_true", help="Allow zero minCollateralSwapOut for local tests only.")
    args = parser.parse_args()

    payload = build_liquidation_execution_payload(
        load_json(args.report),
        executor_address=args.executor,
        router_address=args.router,
        deadline=int(time.time()) + max(30, int(args.deadline_seconds)),
        config=LiquidationExecutionPayloadConfig(allow_zero_min_collateral_out=args.allow_zero_min_out),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
