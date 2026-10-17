"""Read-only Robinhood mainnet Morpho Blue provider probe.

The probe records chain identity, contract code, ABI selector presence and,
when supplied, market aggregates and user positions. It never broadcasts
transactions and never writes business tables.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_chain_env_files  # noqa: E402
from dex.intent_trade.chains.robinhood.morpho_blue_readonly import (  # noqa: E402
    summarize_robinhood_morpho_blue_readonly,
)


DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "evidence" / "robinhood" / "morpho_blue_readonly"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _csv_items(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        for item in str(value or "").replace(";", ",").replace("|", ",").split(","):
            text = item.strip()
            if text and text not in items:
                items.append(text)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    parser.add_argument("--market-id", action="append", default=[])
    parser.add_argument("--accounts", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--strict-readonly",
        action="store_true",
        help="Return exit code 2 unless chain id, code and required read-only selectors are verified.",
    )
    args = parser.parse_args()

    load_chain_env_files(Path(__file__).resolve())
    try:
        report = summarize_robinhood_morpho_blue_readonly(
            rpc_url=args.rpc_url,
            timeout_seconds=args.timeout_seconds,
            market_ids=_csv_items(args.market_id),
            accounts=_csv_items(args.accounts),
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
    if args.strict_readonly and not report["rpcOk"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
