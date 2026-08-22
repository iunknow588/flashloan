from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from web.control_panel_robinhood_dex import robinhood_dex_readiness_payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only Robinhood deployment readiness check."
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Evidence package root; defaults to runtime/evidence/robinhood/dex_discovery.",
    )
    parser.add_argument(
        "--no-rpc",
        action="store_true",
        help="Skip the RPC probe and report configuration/evidence state only.",
    )
    parser.add_argument(
        "--strict-execution",
        action="store_true",
        help="Return exit code 2 unless DEX execution gates are ready.",
    )
    parser.add_argument(
        "--strict-production",
        action="store_true",
        help="Return exit code 2 unless the complete production gate is ready.",
    )
    args = parser.parse_args()

    kwargs = {"run_rpc_probe": not args.no_rpc}
    if args.evidence_root is not None:
        kwargs["evidence_root"] = args.evidence_root
    try:
        payload = robinhood_dex_readiness_payload(**kwargs)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    status = payload["deploymentStatus"]
    if args.strict_production and not status["productionReady"]:
        return 2
    if args.strict_execution and not status["dexExecutionReady"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
