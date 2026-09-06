from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.chain_test_audit import audit_robinhood_chain_test_plan
from dex.intent_trade.chains.robinhood.chain_test_package import chain_test_package_flow
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK
from dex.intent_trade.chains.robinhood_testnet.predeploy_readiness import summarize_robinhood_predeploy_readiness
from core.db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _missing_chain_test_plan(path: Path, chain_metadata: dict) -> dict:
    chain_id = chain_metadata.get("chainId") or 46630
    chain_key = chain_metadata.get("chainKey") or "chain:46630"
    return {
        "schemaVersion": "robinhood-chain-test-plan-v1",
        "network": "robinhood-testnet",
        "chainId": chain_id,
        "chainKey": chain_key,
        "chainTestScope": {
            "enabled": True,
            "network": "robinhood-testnet",
            "chainId": chain_id,
            "canBroadcast": False,
            "missingPlanPath": str(path),
        },
        "route": {
            "pathText": None,
            "closed": False,
            "tokenContinuous": False,
            "hopCount": 0,
            "path": [],
        },
        "evidence": {
            "status": "chain_test_plan_missing",
            "routeClosed": False,
            "tokenContinuity": False,
            "allHopsQuoted": False,
            "netPositiveAfterCosts": False,
            "meetsMinProfitThreshold": False,
        },
        "decision": {
            "readyForTransactionPreflight": False,
            "canRunForkPreflight": False,
            "trustedForExecution": False,
            "canDeployTestnet": False,
            "canBroadcast": False,
        },
        "preflightSpec": {
            "status": "chain_test_plan_missing",
            "broadcastEnabled": False,
            "routeHops": [],
            "repaymentConstraint": {},
        },
    }


def _mark_missing_chain_test_plan(report: dict, path: Path) -> dict:
    blockers = [item for item in list(report.get("blockingReasons") or []) if item != "chain_test_plan_missing"]
    gates = dict(report.get("gates") or {})
    gates["chainTestPlanPresent"] = False
    return report | {
        "missingChainTestPlanPath": str(path),
        "gates": gates,
        "blockingReasons": blockers,
    }


def _build_chain_test_plan_from_local_attempts(plan_path: Path, chain_json: Path) -> dict:
    chain_metadata = _read_json(chain_json)
    rows = load_recent_cow_execution_attempts_jsonl(
        SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts",
        limit=5000,
        networks=[ROBINHOOD_TESTNET_NETWORK],
        chain_ids=[ROBINHOOD_TESTNET_CHAIN_ID],
    )
    if not rows:
        raise FileNotFoundError("no robinhood-testnet attempts were available to build a chain-test plan")
    candidates = [
        row
        for row in rows
        if (
            ((row.get("quote") or {}).get("live_onchain_evidence") or {}).get("readyForTransactionPreflight")
            or ((row.get("precheck") or {}).get("ready_for_transaction_preflight"))
        )
        and (
            ((row.get("quote") or {}).get("live_onchain_evidence") or {}).get("trustedForExecution")
            or ((row.get("precheck") or {}).get("trustedForExecution"))
            or str((((row.get("quote") or {}).get("live_onchain_evidence") or {}).get("status") or "")).strip().lower()
            == "live_verified"
        )
    ]
    candidates.sort(
        key=lambda row: (
            int((((row.get("quote") or {}).get("live_onchain_evidence") or {}).get("triggerBlockNumber") or 0)),
            str(row.get("created_at") or row.get("observed_at") or ""),
        ),
        reverse=True,
    )
    attempt = candidates[0] if candidates else None
    if attempt is None:
        raise FileNotFoundError("no live-ready robinhood-testnet attempts were available to build a chain-test plan")
    result = chain_test_package_flow(
        attempt=attempt,
        output_dir=plan_path.parent,
        contract_dir=REPO_ROOT / "contract" / "robinhood" / "testnet" / "chain-test-package",
    )
    return result["plan"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check Robinhood predeploy readiness without RPC calls or broadcasts."
    )
    parser.add_argument(
        "--chain-json",
        type=Path,
        default=REPO_ROOT / "contract" / "robinhood" / "testnet" / "chain.json",
    )
    parser.add_argument(
        "--contracts-json",
        type=Path,
        default=REPO_ROOT / "contract" / "robinhood" / "testnet" / "contracts.json",
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "chain_test" / "latest" / "plan.json",
    )
    parser.add_argument(
        "--strict-fork",
        action="store_true",
        help="Return exit code 2 unless the latest chain-test package can enter fork/static-call.",
    )
    parser.add_argument(
        "--strict-deploy",
        action="store_true",
        help="Return exit code 2 unless deployment discussion and stage-freeze gates are both ready.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "predeploy_readiness",
        help="Directory for timestamped and latest readiness evidence.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print only; do not write readiness evidence files.",
    )
    args = parser.parse_args()

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
        if plan_missing and not args.no_write:
            _write_json(args.plan_json, plan)
        audit = audit_robinhood_chain_test_plan(plan)
        report = summarize_robinhood_predeploy_readiness(
            chain_metadata=chain_metadata,
            contracts_metadata=contracts_metadata,
            chain_test_plan=plan,
            chain_test_audit=audit,
        )
        if plan_missing and plan.get("evidence", {}).get("status") == "chain_test_plan_missing":
            report = _mark_missing_chain_test_plan(report, args.plan_json)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    if not args.no_write:
        evidence_dir = args.output_dir / _stamp()
        _write_json(evidence_dir / "report.json", report)
        _write_json(args.output_dir / "latest.json", report | {"reportPath": str(evidence_dir / "report.json")})
        report = report | {"reportPath": str(evidence_dir / "report.json")}

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict_deploy and not report["readyForPausedRedeploy"]:
        return 2
    if args.strict_fork and not report["readyForForkStaticcall"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
