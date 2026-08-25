from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.robinhood_chain_test_pack import write_robinhood_chain_test_package
from db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl


def _attempt_score(row: dict) -> tuple[int, str]:
    evidence = row.get("quote", {}).get("live_onchain_evidence") if isinstance(row.get("quote"), dict) else {}
    precheck = row.get("precheck") if isinstance(row.get("precheck"), dict) else {}
    status = str(evidence.get("status") or precheck.get("status") or row.get("state") or "").strip().lower()
    if bool(evidence.get("trustedForExecution") or precheck.get("trustedForExecution")):
        return 50, str(row.get("created_at") or row.get("observed_at") or "")
    if bool(evidence.get("readyForTransactionPreflight") or precheck.get("ready_for_transaction_preflight")):
        return 40, str(row.get("created_at") or row.get("observed_at") or "")
    if status == "live_verified":
        return 35, str(row.get("created_at") or row.get("observed_at") or "")
    if status in {"not_live_verified", "live_quote_failed"}:
        return 30, str(row.get("created_at") or row.get("observed_at") or "")
    if status == "live_evidence_deferred":
        return 10, str(row.get("created_at") or row.get("observed_at") or "")
    return 0, str(row.get("created_at") or row.get("observed_at") or "")


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _block_number(row: dict) -> int | None:
    evidence = row.get("quote", {}).get("live_onchain_evidence") if isinstance(row.get("quote"), dict) else {}
    market_state = row.get("market_state") if isinstance(row.get("market_state"), dict) else {}
    for value in (
        evidence.get("triggerBlockNumber"),
        evidence.get("blockNumber"),
        market_state.get("trigger_block_number"),
        row.get("trigger_block_number"),
    ):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _select_attempt(rows: list[dict], pair: str | None, *, trigger_block: int | None = None) -> dict:
    wanted = str(pair or "").strip().lower()
    candidates: list[dict] = []
    for row in rows:
        row_pair = str(row.get("pair") or row.get("route_key") or "").strip().lower()
        route_path = row.get("route_path") if isinstance(row.get("route_path"), list) else []
        route_pair = " -> ".join(str(item).strip() for item in route_path if str(item).strip()).lower()
        if not wanted or row_pair == wanted or route_pair == wanted:
            if trigger_block is not None and _block_number(row) != trigger_block:
                continue
            candidates.append(row)
    if candidates:
        candidates.sort(key=_attempt_score, reverse=True)
        return candidates[0]
    raise ValueError("no matching Robinhood attempt row was found")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Robinhood chain-test package from a verified attempt row.")
    parser.add_argument("--attempt-json", type=Path, help="One attempt row as JSON.")
    parser.add_argument("--log-path", type=Path, default=SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts.jsonl")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--pair", default="USDG -> WETH -> VLAD -> WETH -> USDG")
    parser.add_argument("--trigger-block", type=int, default=None)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--output-dir", type=Path, default=SRC_ROOT / "runtime" / "evidence" / "robinhood" / "chain_test" / "latest")
    parser.add_argument("--contract-dir", type=Path, default=SRC_ROOT.parent.parent / "contract" / "robinhood")
    args = parser.parse_args()

    if args.attempt_json:
        attempt = _read_json(args.attempt_json)
    else:
        rows = load_recent_cow_execution_attempts_jsonl(args.log_path, limit=args.limit, networks=[args.network])
        attempt = _select_attempt(rows, args.pair, trigger_block=args.trigger_block)
    plan = write_robinhood_chain_test_package(args.output_dir, attempt, contract_dir=args.contract_dir)
    print(
        json.dumps(
            {
                "ok": True,
                "outputDir": str(args.output_dir),
                "contractDir": str(args.contract_dir),
                "route": plan["route"]["pathText"],
                "readyForTransactionPreflight": plan["decision"]["readyForTransactionPreflight"],
                "trustedForExecution": plan["decision"]["trustedForExecution"],
                "checklistCount": len(plan.get("checklist") or []),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
