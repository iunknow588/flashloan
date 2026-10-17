"""Read-only Robinhood V4 contract address provenance evidence.

The probe reads chain identity, contract code, code hash and Blockscout
verification state for the configured V4 contracts.  It never broadcasts
transactions and never writes business tables.
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
from dex.intent_trade.chains.robinhood.blockscout import (  # noqa: E402
    probe_robinhood_blockscout_contract,
)
from web3 import Web3  # noqa: E402


V4_CONTRACT_KEYS = (
    "v4_pool_manager",
    "v4_quoter",
    "position_manager",
    "permit2",
    "universal_router",
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chain_row(network: str) -> dict[str, Any]:
    try:
        return chain_data_by_network(network) or {}
    except Exception:
        return {}


def _chain_id(network: str) -> int:
    return int(_chain_row(network).get("chain_id") or 0)


def _rpc_urls(network: str) -> list[str]:
    row = _chain_row(network)
    rpc_block = row.get("rpc") if isinstance(row.get("rpc"), dict) else {}
    urls: list[str] = []
    primary = str(rpc_block.get("primary") or "").strip()
    if primary:
        urls.append(primary)
    for url in rpc_block.get("fallbacks") or []:
        text = str(url or "").strip()
        if text and text not in urls:
            urls.append(text)
    if network == robinhood.TESTNET_NETWORK:
        urls.extend(robinhood.testnet_rpc_urls_from_env(default=()))
    else:
        urls.extend(robinhood.rpc_urls_from_env(default=()))
    return urls


def _explorer_url(network: str) -> str:
    row = _chain_row(network)
    explorer = row.get("explorer") if isinstance(row.get("explorer"), dict) else {}
    return str(explorer.get("url") or "").strip()


def _connect_rpc(network: str) -> tuple[Web3, str]:
    wanted_chain_id = _chain_id(network)
    for url in _rpc_urls(network):
        if not url.startswith(("https://", "http://")):
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20, "verify": False}))
            if int(w3.eth.chain_id) == wanted_chain_id:
                return w3, url
        except Exception:
            continue
    raise RuntimeError(f"{network} RPC unavailable for chainId {wanted_chain_id}")


def _contract_evidence(
    w3: Web3,
    *,
    network: str,
    chain_id: int,
    label: str,
    address: str,
    explorer: str,
    source_urls: list[str],
    block_number: int,
    explorer_probe: Any = None,
) -> dict[str, Any]:
    checksum = Web3.to_checksum_address(address)
    # Some Robinhood testnet RPCs prune historical state, so contract code is
    # read at the current head even when the report records a block number.
    raw_code = bytes(w3.eth.get_code(checksum, block_identifier="latest") or b"")
    has_code = bool(raw_code)
    code_hex = "0x" + raw_code.hex() if has_code else "0x"
    code_bytes = len(raw_code)
    code_hash = Web3.keccak(raw_code).hex() if has_code else "0x"
    explorer_report = (explorer_probe or probe_robinhood_blockscout_contract)(
        address,
        explorer_url=explorer,
        network=network,
        chain_id=chain_id,
    )
    return {
        "label": label,
        "address": checksum,
        "hasCode": has_code,
        "codeBytes": code_bytes,
        "codeHash": code_hash,
        "blockNumber": block_number,
        "sourceUrls": source_urls,
        "explorer": explorer_report,
        "reviewedAt": _utc_now_iso(),
    }


def build_v4_address_provenance(
    *,
    network: str,
    source_urls: list[str] | None = None,
    rpc_url: str = "",
    w3: Any = None,
    explorer_probe: Any = None,
) -> dict[str, Any]:
    normalized = str(network or "").strip().lower()
    if normalized not in {robinhood.NETWORK, robinhood.TESTNET_NETWORK}:
        raise ValueError(f"unsupported network {network!r}")
    chain_id = _chain_id(normalized)
    contracts = {}
    try:
        from core.chain_data_registry import chain_contracts

        contracts = chain_contracts(normalized) or {}
    except Exception:
        contracts = {}
    wanted = {
        key: str(contracts.get(key) or "").strip()
        for key in V4_CONTRACT_KEYS
        if str(contracts.get(key) or "").strip()
    }
    if not wanted:
        raise RuntimeError(f"{normalized} chain.json has no V4 contract addresses")

    resolved_rpc = rpc_url or ""
    if w3 is None:
        if resolved_rpc:
            w3 = Web3(Web3.HTTPProvider(resolved_rpc, request_kwargs={"timeout": 20, "verify": False}))
            actual_chain_id = int(w3.eth.chain_id)
            if actual_chain_id != chain_id:
                raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {chain_id}")
            block_number = int(w3.eth.block_number)
        else:
            w3, resolved_rpc = _connect_rpc(normalized)
            block_number = int(w3.eth.block_number)
    else:
        block_number = int(getattr(w3, "eth").block_number)

    explorer = _explorer_url(normalized)
    base_sources = [f"{explorer.rstrip('/')}/address/{address}" for address in wanted.values() if explorer]
    all_sources = list(dict.fromkeys((source_urls or []) + base_sources))
    records: dict[str, dict[str, Any]] = {}
    for label, address in wanted.items():
        try:
            records[label] = _contract_evidence(
                w3,
                network=normalized,
                chain_id=chain_id,
                label=label,
                address=address,
                explorer=explorer,
                source_urls=all_sources,
                block_number=block_number,
                explorer_probe=explorer_probe,
            )
        except Exception as exc:
            records[label] = {
                "label": label,
                "address": address,
                "hasCode": False,
                "codeBytes": 0,
                "codeHash": "0x",
                "blockNumber": block_number,
                "sourceUrls": all_sources,
                "error": str(exc),
                "reviewedAt": _utc_now_iso(),
            }
    missing = [label for label, record in records.items() if not record["hasCode"]]
    return {
        "network": normalized,
        "chainId": chain_id,
        "rpcUrl": resolved_rpc,
        "blockNumber": block_number,
        "generatedAt": _utc_now_iso(),
        "sourceUrls": all_sources,
        "contracts": records,
        "contractCount": len(records),
        "missingCodeContracts": missing,
        "allContractsHaveCode": not missing,
        "executionEnabled": False,
        "broadcastEnabled": False,
        "evidenceType": "readonly_v4_address_provenance",
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_summary(path: Path, report: dict) -> None:
    lines = [
        "# Robinhood V4 Address Provenance",
        "",
        f"- Network: `{report['network']}`",
        f"- Chain ID: `{report['chainId']}`",
        f"- Block number: `{report['blockNumber']}`",
        f"- Contract count: `{report['contractCount']}`",
        f"- All contracts have code: `{report['allContractsHaveCode']}`",
        "- Execution: disabled",
        "- Broadcast: disabled",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--network",
        choices=[robinhood.NETWORK, robinhood.TESTNET_NETWORK],
        required=True,
    )
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--source-url", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    try:
        report = build_v4_address_provenance(
            network=args.network,
            source_urls=args.source_url,
            rpc_url=args.rpc_url,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    if not args.no_write:
        output_dir = args.output_dir or (
            SRC_ROOT / "runtime" / "evidence" / args.network / "dex_v4_address_provenance"
        )
        evidence_dir = output_dir / _stamp()
        report["reportPath"] = str(evidence_dir / "report.json")
        _write_json(evidence_dir / "report.json", report)
        _write_json(output_dir / "latest.json", report)
        _write_summary(evidence_dir / "summary.md", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
