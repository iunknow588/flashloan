"""Backfill chained per-pool quotes into captured UI signal evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from web3 import Web3


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "flashloan" / "src_bot"
DEFAULT_EVIDENCE_ROOT = SRC_ROOT / "runtime" / "evidence" / "ui_signal_pool_quotes"
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from core.sensitive_data import redact_sensitive_text
from market.observer_common import DEFAULT_RPC, parse_rpc_urls
from tools.probe_signal_pool_quotes import _chained_route_quotes, _contract_path_profit


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _rpc_candidates() -> list[str]:
    candidates = [
        os.getenv(name, "").strip()
        for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC")
        if os.getenv(name, "").strip()
    ]
    candidates.extend(parse_rpc_urls(os.getenv("AVALANCHE_RPCS", "")))
    candidates.append(DEFAULT_RPC)
    return list(dict.fromkeys(item for item in candidates if item))


def _connect() -> tuple[Web3, str]:
    errors: list[str] = []
    for rpc_url in _rpc_candidates():
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
            if int(w3.eth.chain_id) == 43114:
                return w3, rpc_url
            errors.append("not_avalanche_chain")
        except Exception as exc:
            errors.append(redact_sensitive_text(exc))
    raise RuntimeError("; ".join(errors[:3]) or "rpc_unavailable")


def _latest_collection_index(root: Path) -> Path:
    indexes = [
        path
        for path in root.glob("*/index.json")
        if path.is_file() and "_test" not in str(path)
    ]
    if not indexes:
        raise FileNotFoundError(f"no collection index under {root}")
    return max(indexes, key=lambda path: path.stat().st_mtime)


def backfill(
    *,
    collection_index: Path,
    pool_cache: Path,
) -> dict[str, Any]:
    collection = _read_json(collection_index)
    cache = _read_json(pool_cache)
    w3, rpc_url = _connect()
    quoter = Web3.to_checksum_address(
        str(cache.get("quoter") or "0xbe0F5544EC67e9B3b2D979aaA43f18Fd87E6257F")
    )
    updated = 0
    updated_contracts = 0
    blocked = 0
    failures: list[dict[str, str]] = []

    for sample in collection.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        evidence_path = Path(str(sample.get("evidencePath") or ""))
        if not evidence_path.is_file():
            continue
        evidence = _read_json(evidence_path)
        evidence_routes = [
            route
            for candidate in evidence.get("candidates") or []
            if isinstance(candidate, dict)
            for route in candidate.get("routes") or []
            if isinstance(route, dict)
        ]
        try:
            block_number = int((evidence.get("quoteContext") or {}).get("blockNumber") or 0)
        except (TypeError, ValueError):
            block_number = 0
        if block_number <= 0:
            failures.append({"file": evidence_path.name, "reason": "missing_block_number"})
            continue

        changed = False
        for candidate in evidence.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            for route in candidate.get("routes") or []:
                if not isinstance(route, dict):
                    continue
                matrix = route.get("poolQuoteMatrix")
                route_path = route.get("routePath")
                if not isinstance(matrix, dict) or not isinstance(route_path, list):
                    continue
                trades = matrix.get("trades")
                if not isinstance(trades, list):
                    continue
                chained = matrix.get("chainedRoute") if isinstance(matrix.get("chainedRoute"), dict) else {}
                if chained.get("status") != "quoted":
                    try:
                        chained = _chained_route_quotes(
                            w3,
                            quoter,
                            trades,
                            route_path=[str(item) for item in route_path],
                            usdc_amount=int((evidence.get("quoteContext") or {}).get("usdcAmount") or 1_000_000_000),
                            block_identifier=block_number,
                        )
                    except Exception as exc:
                        failures.append(
                            {
                                "file": evidence_path.name,
                                "route": "->".join(str(item) for item in route_path),
                                "reason": redact_sensitive_text(exc),
                            }
                        )
                        continue
                matrix["chainedRoute"] = chained
                route["chainedRoute"] = chained
                changed = True
                if chained.get("status") == "quoted":
                    updated += 1
                    preferred_pools = {
                        "usdcToX": (chained.get("uX") or {}).get("recommendedPool"),
                        "xToY": (chained.get("xY") or {}).get("recommendedPool"),
                        "yToUsdc": (chained.get("yC") or {}).get("recommendedPool"),
                    }
                    matrix["contractPathProfit"] = _contract_path_profit(
                        trades,
                        quoter,
                        w3,
                        int((evidence.get("quoteContext") or {}).get("usdcAmount") or 1_000_000_000),
                        int((evidence.get("quoteContext") or {}).get("minProfitUsdc") or 1),
                        int((evidence.get("quoteContext") or {}).get("premiumBps") or 5),
                        preferred_pools=preferred_pools,
                        block_identifier=block_number,
                    )
                    updated_contracts += 1
                else:
                    blocked += 1
        if changed:
            _write_json(evidence_path, evidence)

    return {
        "ok": not failures,
        "rpcUrl": rpc_url,
        "collectionIndex": str(collection_index),
        "updatedRoutes": updated,
        "updatedContractRoutes": updated_contracts,
        "blockedRoutes": blocked,
        "failures": failures,
    }


def main() -> int:
    load_env_files(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-index", type=Path)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--pool-cache", type=Path, default=DEFAULT_POOL_CACHE)
    args = parser.parse_args()
    collection_index = args.collection_index or _latest_collection_index(args.evidence_root)
    result = backfill(
        collection_index=collection_index if collection_index.is_absolute() else REPO_ROOT / collection_index,
        pool_cache=args.pool_cache if args.pool_cache.is_absolute() else REPO_ROOT / args.pool_cache,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
