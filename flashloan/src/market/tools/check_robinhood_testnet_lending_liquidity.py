from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood_testnet.lending_liquidity import summarize_robinhood_testnet_lending_liquidity


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether Robinhood Testnet has verified Aave/Morpho lending liquidity."
    )
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "lending_liquidity",
    )
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--strict-lending",
        action="store_true",
        help="Return exit code 2 unless Aave/Morpho lending liquidity is verified on chain 46630.",
    )
    args = parser.parse_args()

    try:
        report = summarize_robinhood_testnet_lending_liquidity(
            rpc_url=args.rpc_url,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if not args.no_write:
        evidence_dir = args.output_dir / _stamp()
        report = report | {"reportPath": str(evidence_dir / "report.json")}
        _write_json(evidence_dir / "report.json", report)
        _write_json(args.output_dir / "latest.json", report)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict_lending and not report["hasVerifiedBorrowableLiquidity"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
