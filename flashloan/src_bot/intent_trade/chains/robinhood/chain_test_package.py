from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.chain_flow_dispatcher import ChainFlowRequest
from intent_trade.chains.robinhood.chain_test_pack import write_robinhood_chain_test_package
from intent_trade.chains.robinhood.chain_test_policy import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    normalize_robinhood_chain_test_network,
)
from core.evidence_scope import evidence_scope_mismatch_reasons
from db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl


def _default_output_dir(network: str) -> Path:
    return SRC_ROOT / "runtime" / "evidence" / network / "chain_test" / "latest"


def _default_contract_dir(network: str) -> Path:
    if network == ROBINHOOD_TESTNET_NETWORK:
        return SRC_ROOT.parent.parent / "contract" / "robinhood" / "testnet" / "chain-test-package"
    return SRC_ROOT.parent.parent / "contract" / "robinhood"


def _expected_chain_id(network: str) -> int:
    if network == ROBINHOOD_TESTNET_NETWORK:
        return ROBINHOOD_TESTNET_CHAIN_ID
    if network == ROBINHOOD_NETWORK:
        return ROBINHOOD_CHAIN_ID
    raise ValueError(f"unsupported Robinhood chain-test network: {network}")


def _assert_scoped_path(path: Path, *, network: str, kind: str) -> None:
    normalized = str(path).replace("\\", "/").lower()
    if network == ROBINHOOD_TESTNET_NETWORK:
        if kind == "output" and "/robinhood-testnet/" not in f"/{normalized}/":
            raise ValueError("Robinhood testnet chain-test output must stay under a robinhood-testnet evidence path")
        if kind == "contract" and "/contract/robinhood/testnet/" not in f"/{normalized}/":
            raise ValueError("Robinhood testnet contract handoff must stay under contract/robinhood/testnet")
    if network == ROBINHOOD_NETWORK and "/robinhood-testnet/" in f"/{normalized}/":
        raise ValueError("Robinhood mainnet chain-test package must not write into robinhood-testnet paths")


def _assert_attempt_scope(attempt: dict, *, network: str, chain_id: int) -> None:
    reasons = evidence_scope_mismatch_reasons(
        attempt,
        expected_network=network,
        expected_chain_id=chain_id,
    )
    if reasons:
        raise ValueError(
            "selected attempt does not match requested chain-test scope: "
            + ", ".join(reasons)
        )


def _attempt_network(attempt: dict[str, Any] | None) -> str:
    if not isinstance(attempt, dict):
        return ""
    quote = _as_dict(attempt.get("quote"))
    evidence = _as_dict(quote.get("live_onchain_evidence"))
    precheck = _as_dict(attempt.get("precheck"))
    for candidate in (
        evidence.get("network"),
        quote.get("network"),
        precheck.get("network"),
        attempt.get("network"),
    ):
        network = _text(candidate).lower()
        if network:
            return network
    return ""


def _attempt_chain_id(attempt: dict[str, Any] | None) -> int | None:
    if not isinstance(attempt, dict):
        return None
    quote = _as_dict(attempt.get("quote"))
    evidence = _as_dict(quote.get("live_onchain_evidence"))
    precheck = _as_dict(attempt.get("precheck"))
    for candidate in (
        evidence.get("chainId"),
        quote.get("chain_id"),
        quote.get("chainId"),
        precheck.get("chain_id"),
        precheck.get("chainId"),
        attempt.get("chain_id"),
    ):
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


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


def _text(value: object) -> str:
    return str(value or "").strip()


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def chain_test_package_flow(
    *,
    request: ChainFlowRequest | None = None,
    attempt: dict | None = None,
    attempt_json: Path | None = None,
    log_path: Path | None = None,
    limit: int = 5000,
    pair: str | None = "USDG -> WETH -> VLAD -> WETH -> USDG",
    trigger_block: int | None = None,
    output_dir: Path | None = None,
    contract_dir: Path | None = None,
    **_: object,
) -> dict:
    requested_network = _text(request.network if request is not None else "").lower()
    if not requested_network:
        requested_network = ROBINHOOD_NETWORK
    requested_chain_id = (
        int(request.chain_id)
        if request is not None and request.chain_id
        else _expected_chain_id(requested_network)
    )
    selected_attempt = attempt
    if selected_attempt is None and attempt_json is not None:
        selected_attempt = _read_json(attempt_json)
    if selected_attempt is None:
        rows = load_recent_cow_execution_attempts_jsonl(
            log_path or SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts.jsonl",
            limit=limit,
            networks=[requested_network],
            chain_ids=[requested_chain_id],
        )
        selected_attempt = _select_attempt(rows, pair, trigger_block=trigger_block)
    network = _text(
        _attempt_network(selected_attempt)
        or (request.network if request is not None else "")
        or ROBINHOOD_NETWORK
    ).lower()
    chain_id = _attempt_chain_id(selected_attempt) or (
        int(request.chain_id) if request is not None and request.chain_id else None
    ) or _expected_chain_id(network)
    output_dir = output_dir or _default_output_dir(network)
    contract_dir = contract_dir or _default_contract_dir(network)
    _assert_scoped_path(output_dir, network=network, kind="output")
    _assert_scoped_path(contract_dir, network=network, kind="contract")
    _assert_attempt_scope(selected_attempt, network=network, chain_id=chain_id)
    plan = write_robinhood_chain_test_package(output_dir, selected_attempt, contract_dir=contract_dir)
    return {
        "ok": True,
        "network": network,
        "chainId": chain_id,
        "outputDir": str(output_dir),
        "contractDir": str(contract_dir),
        "route": plan["route"]["pathText"],
        "readyForTransactionPreflight": plan["decision"]["readyForTransactionPreflight"],
        "trustedForExecution": plan["decision"]["trustedForExecution"],
        "checklistCount": len(plan.get("checklist") or []),
        "plan": plan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Robinhood chain-test package from a verified attempt row.")
    parser.add_argument("--attempt-json", type=Path, help="One attempt row as JSON.")
    parser.add_argument("--log-path", type=Path, default=SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts.jsonl")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--pair", default="USDG -> WETH -> VLAD -> WETH -> USDG")
    parser.add_argument("--trigger-block", type=int, default=None)
    parser.add_argument("--network", default=ROBINHOOD_NETWORK)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--contract-dir", type=Path, default=None)
    args = parser.parse_args()
    network = normalize_robinhood_chain_test_network(args.network)
    if network not in {ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_NETWORK}:
        raise ValueError(f"unsupported Robinhood chain-test network: {args.network}")
    chain_id = _expected_chain_id(network)
    output_dir = args.output_dir or _default_output_dir(network)
    contract_dir = args.contract_dir or _default_contract_dir(network)
    _assert_scoped_path(output_dir, network=network, kind="output")
    _assert_scoped_path(contract_dir, network=network, kind="contract")

    if args.attempt_json:
        attempt = _read_json(args.attempt_json)
    else:
        rows = load_recent_cow_execution_attempts_jsonl(
            args.log_path,
            limit=args.limit,
            networks=[network],
            chain_ids=[chain_id],
        )
        attempt = _select_attempt(rows, args.pair, trigger_block=args.trigger_block)
    _assert_attempt_scope(attempt, network=network, chain_id=chain_id)
    plan = write_robinhood_chain_test_package(output_dir, attempt, contract_dir=contract_dir)
    print(
        json.dumps(
            {
                "ok": True,
                "network": network,
                "outputDir": str(output_dir),
                "contractDir": str(contract_dir),
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
