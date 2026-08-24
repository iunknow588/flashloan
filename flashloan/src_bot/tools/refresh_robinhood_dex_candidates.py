"""Refresh Robinhood read-only pool evidence and current candidate cache."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.robinhood_dex_discovery import scan_uniswap_v3_pool_created
from core.robinhood_dex_evidence import (
    build_current_robinhood_candidates,
    build_robinhood_pool_records,
    write_robinhood_evidence_package,
)
from execution.uniswap_v3 import snapshot_v3_pool
from intent_trade.chains.robinhood import config as robinhood
from web3 import Web3


ERC20_METADATA_ABI = [
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "type": "function"},
]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _token_metadata(w3: Web3, address: str) -> dict[str, object]:
    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_METADATA_ABI)
    try:
        symbol = str(contract.functions.symbol().call()).strip().upper()
    except Exception:
        symbol = ""
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        decimals = 18
    return {"symbol": symbol, "decimals": decimals, "source": "robinhood_chain_observation"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--factory",
        default="0x1f7d7550b1b028f7571e69a784071f0205fd2efa",
        help="Robinhood Uniswap V3 factory address.",
    )
    parser.add_argument(
        "--source-url",
        default="https://developers.uniswap.org/docs/protocols/v3/deployments/v3-robinhood-chain-deployments",
    )
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--output-dir", type=Path, default=SRC_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery")
    parser.add_argument("--cache", type=Path, default=SRC_ROOT / "runtime" / "cache" / "robinhood_dex_candidates.json")
    args = parser.parse_args()

    rpc_url = args.rpc_url.strip() or robinhood.DEFAULT_RPC_URLS[0]
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    latest_block = int(w3.eth.block_number)
    from_block = _int_env("ROBINHOOD_DEX_DISCOVERY_FROM_BLOCK", 0)
    to_block = _int_env("ROBINHOOD_DEX_DISCOVERY_TO_BLOCK", latest_block)
    report = scan_uniswap_v3_pool_created(
        rpc_url,
        factory_address=args.factory,
        source_url=args.source_url,
        from_block=from_block,
        to_block=min(to_block, latest_block),
        chunk_size=_int_env("ROBINHOOD_DEX_DISCOVERY_CHUNK_SIZE", 2000),
    )

    registry: dict[str, dict[str, object]] = {}
    for item in report.get("pools") or []:
        for field in ("token0", "token1"):
            address = str(item.get(field) or "")
            if address and address not in registry:
                registry[address] = _token_metadata(w3, address)
        pool = str(item.get("pool") or "")
        if pool:
            try:
                snapshot = snapshot_v3_pool(w3, pool, block_identifier=latest_block)
                item.update(
                    {
                        "token0": snapshot.token0,
                        "token1": snapshot.token1,
                        "fee": snapshot.fee,
                        "liquidity": str(snapshot.liquidity),
                        "sqrtPriceX96": str(snapshot.sqrt_price_x96),
                        "tick": snapshot.tick,
                        "poolStateVerified": True,
                    }
                )
            except Exception as exc:
                item["poolStateVerified"] = False
                item["poolStateError"] = str(exc)
        item["eventVerified"] = True
    report["discoveryMethod"] = "factory_PoolCreated_event_scan"
    report["evidenceType"] = "readonly_factory_event_scan"

    allowlist = set(registry)
    pool_rows = build_robinhood_pool_records(
        report,
        token_registry=registry,
        token_allowlist=allowlist,
        token_blacklist=set(),
    )
    candidates = build_current_robinhood_candidates(pool_rows)
    run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = args.output_dir / run_name
    write_robinhood_evidence_package(
        evidence_dir,
        discovery_report=report,
        pool_rows=pool_rows,
        candidates=candidates,
        token_registry=registry,
        token_allowlist=allowlist,
        token_blacklist=set(),
    )
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.cache.write_text(
        json.dumps(
            {
                "schemaVersion": "robinhood-candidate-cache-v1",
                "generatedAt": report["generatedAt"],
                "source": "factory_PoolCreated_event_scan",
                "network": robinhood.NETWORK,
                "chainId": robinhood.CHAIN_ID,
                "factory": report.get("factory"),
                "pools": pool_rows,
                "candidates": candidates,
                "summary": {
                    "poolCount": len(pool_rows),
                    "candidateRouteCount": len(candidates),
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "blockNumber": report["blockNumber"],
                "poolCount": len(pool_rows),
                "candidateCount": len(candidates),
                "evidenceDirectory": str(evidence_dir),
                "candidateCache": str(args.cache),
                "executionEnabled": False,
                "broadcastEnabled": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


