from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SRC_ROOT.parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.chain_test_audit import audit_robinhood_chain_test_plan
from dex.intent_trade.chains.robinhood_testnet.live_testing_readiness import (
    summarize_robinhood_testnet_live_testing_readiness,
)
from core.env_loader import load_chain_env_files
from dex.tools.check_robinhood_predeploy_readiness import (
    _build_chain_test_plan_from_local_attempts,
    _missing_chain_test_plan,
    _read_json,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Robinhood Testnet live-testing readiness.")
    parser.add_argument(
        "--chain-json",
        type=Path,
        default=REPO_ROOT / "contract" / "robinhood-testnet" / "DEX" / "config" / "chain.json",
    )
    parser.add_argument(
        "--contracts-json",
        type=Path,
        default=REPO_ROOT / "contract" / "robinhood-testnet" / "DEX" / "config" / "contracts.json",
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "chain_test" / "latest" / "plan.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "live_testing_readiness",
    )
    parser.add_argument(
        "--snapshot-json",
        type=Path,
        default=SRC_ROOT / "dex" / "runtime" / "cache" / "dex_opportunity_snapshot.json",
        help="Current opportunity snapshot used to reject stale chain-test plans.",
    )
    parser.add_argument(
        "--route-capture-json",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "route_capture" / "latest.json",
        help="Route capture evidence used as a live-profitable-route fallback.",
    )
    parser.add_argument(
        "--liquidity-json",
        type=Path,
        default=None,
        help="Optional realtime V4 pool liquidity payload. When absent, normal probe readiness stays blocked instead of using cached liquidity.",
    )
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--strict-live",
        action="store_true",
        help="Return exit code 2 unless Robinhood Testnet is ready for a live on-chain test transaction.",
    )
    args = parser.parse_args()
    load_chain_env_files(
        __file__,
        [REPO_ROOT / "contract" / "robinhood-testnet" / "DEX" / ".env"],
        override=False,
    )

    try:
        chain_metadata = _read_json(args.chain_json)
        contracts_metadata = _read_json(args.contracts_json)
        plan_missing = not args.plan_json.exists()
        if plan_missing:
            try:
                plan = _build_chain_test_plan_from_local_attempts(args.plan_json, args.chain_json)
            except Exception:
                plan = _missing_chain_test_plan(args.plan_json, chain_metadata)
        else:
            plan = _read_json(args.plan_json)
        current_opportunity_snapshot = _read_json(args.snapshot_json) if args.snapshot_json.exists() else None
        route_capture = _read_json(args.route_capture_json) if args.route_capture_json.exists() else None
        v4_pool_liquidity = _read_json(args.liquidity_json) if args.liquidity_json and args.liquidity_json.exists() else None
        if plan_missing and not args.no_write:
            _write_json(args.plan_json, plan)
        audit = audit_robinhood_chain_test_plan(plan)
        report = summarize_robinhood_testnet_live_testing_readiness(
            chain_metadata=chain_metadata,
            contracts_metadata=contracts_metadata,
            chain_test_plan=plan,
            v4_pool_liquidity=v4_pool_liquidity,
            chain_test_audit=audit,
            current_opportunity_snapshot=current_opportunity_snapshot,
            route_capture=route_capture,
        )
        if plan_missing and plan.get("evidence", {}).get("status") == "chain_test_plan_missing":
            report = report | {"missingChainTestPlanPath": str(args.plan_json)}
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if not args.no_write:
        evidence_dir = args.output_dir / _stamp()
        _write_json(evidence_dir / "report.json", report)
        _write_json(args.output_dir / "latest.json", report | {"reportPath": str(evidence_dir / "report.json")})
        report = report | {"reportPath": str(evidence_dir / "report.json")}

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict_live and not report["readyForLiveTest"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
