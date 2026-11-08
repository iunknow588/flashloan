"""Read-only Robinhood mainnet executor gate evidence.

The mainnet paused-executor is not deployed or configured yet.  This tool
records that fail-closed gate as evidence: it never builds transactions, never
broadcasts, never writes SQLite, and never attempts a fork/static-call against
a missing executor.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.chain_data_registry import chain_data_by_network  # noqa: E402
from dex.intent_trade.chains.robinhood import config as robinhood  # noqa: E402
from web3 import Web3  # noqa: E402


EXECUTOR_CONTRACT = "RobinhoodPausedFlashLoanExecutor"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _chain_row(network: str) -> dict[str, Any]:
    row = chain_data_by_network(network) or {}
    if not isinstance(row, dict):
        raise ValueError(f"chain data for {network} is not an object")
    return row


def _rpc_urls(network: str) -> tuple[str, ...]:
    row = _chain_row(network)
    urls: list[str] = []
    rpc_block = row.get("rpc") if isinstance(row.get("rpc"), dict) else {}
    primary = str(rpc_block.get("primary") or "").strip()
    if primary:
        urls.append(primary)
    for url in rpc_block.get("fallbacks") or []:
        text = str(url or "").strip()
        if text and text not in urls:
            urls.append(text)
    urls.extend(
        str(url).strip()
        for url in robinhood.rpc_urls_from_env(default=())
        if str(url).strip() and str(url).strip() not in urls
    )
    return tuple(urls)


def _connect(wanted_chain_id: int, urls: tuple[str, ...]) -> tuple[Web3, str]:
    for url in urls:
        if not url.startswith(("https://", "http://")):
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))
            if int(w3.eth.chain_id) == wanted_chain_id:
                return w3, url
        except Exception:
            continue
    raise RuntimeError(f"no reachable RPC for chainId {wanted_chain_id}")


def _code_evidence(w3: Web3, address: str, block_number: int) -> dict[str, Any]:
    if not address:
        return {"configured": False, "address": "", "hasCode": False, "codeBytes": 0}
    checksum = Web3.to_checksum_address(address)
    raw_code = bytes(w3.eth.get_code(checksum, block_identifier="latest") or b"")
    return {
        "configured": True,
        "address": checksum,
        "hasCode": bool(raw_code),
        "codeBytes": len(raw_code),
        "blockNumber": block_number,
    }


def build_mainnet_readonly_gate(rpc_url: str = "") -> dict[str, Any]:
    network = robinhood.NETWORK
    row = _chain_row(network)
    chain_id = int(row.get("chain_id") or 0)
    if chain_id != 4663:
        raise ValueError(f"expected mainnet chainId 4663, got {chain_id}")
    contracts = row.get("contracts") if isinstance(row.get("contracts"), dict) else {}
    executor_address = str(contracts.get("unified_executor") or "").strip()
    urls = _rpc_urls(network)
    if rpc_url:
        urls = (rpc_url, *[url for url in urls if url != rpc_url])
    w3, resolved_rpc = _connect(chain_id, urls)
    block_number = int(w3.eth.block_number)
    v4_contracts = {
        label: _code_evidence(w3, str(contracts.get(key) or ""), block_number)
        for label, key in (
            ("permit2", "permit2"),
            ("v4_pool_manager", "v4_pool_manager"),
            ("v4_quoter", "v4_quoter"),
            ("position_manager", "position_manager"),
            ("universal_router", "universal_router"),
        )
    }
    executor = _code_evidence(w3, executor_address, block_number)
    executor["contract"] = EXECUTOR_CONTRACT
    static_call = {
        "attempted": False,
        "ok": False,
        "reason": "mainnet_executor_not_deployed_or_configured",
        "detail": "static-call requires a deployed paused executor on mainnet; none is configured",
    }
    readback = {
        "attempted": True,
        "ok": bool(executor["configured"] and executor["hasCode"]),
        "result": (
            "mainnet_executor_configured_with_code"
            if executor["configured"] and executor["hasCode"]
            else "mainnet_executor_not_configured_or_no_code"
        ),
        "executor": executor,
    }
    blocking_reasons = []
    if not executor["configured"]:
        blocking_reasons.append("mainnet_executor_not_configured")
    if executor["configured"] and not executor["hasCode"]:
        blocking_reasons.append("mainnet_executor_has_no_code")
    blocking_reasons.append("fork_staticcall_not_attempted")
    return {
        "schemaVersion": "robinhood-mainnet-readonly-gate-v1",
        "generatedAt": _utc_now_iso(),
        "network": network,
        "chainId": chain_id,
        "blockNumber": block_number,
        "rpcUrl": resolved_rpc,
        "executor": executor,
        "v4Contracts": v4_contracts,
        "staticCall": static_call,
        "readback": readback,
        "blockingReasons": blocking_reasons,
        "broadcastEnabled": False,
        "buildsTransaction": False,
        "writesSqlite": False,
        "observationOnly": True,
        "readyForBroadcast": False,
        "executionGate": "mainnet_shadow_readonly",
        "evidenceType": "readonly_mainnet_executor_gate",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-url", default="", help="Override mainnet RPC URL.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    try:
        report = build_mainnet_readonly_gate(rpc_url=args.rpc_url)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    if not args.no_write:
        output_dir = args.output_dir or (
            SRC_ROOT / "runtime" / "evidence" / robinhood.NETWORK / "mainnet_readonly_gate"
        )
        evidence_dir = output_dir / _stamp()
        report["reportPath"] = str(evidence_dir / "report.json")
        _write_json(evidence_dir / "report.json", report)
        _write_json(output_dir / "latest.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
