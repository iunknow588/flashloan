from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from intent_trade.chains.robinhood.dex_evidence import (
    build_current_robinhood_candidates,
    build_robinhood_pool_records,
    write_robinhood_evidence_package,
)
from core.market_config import DEFAULT_ROBINHOOD_DEX_MARKET_ID
from db.storage_chain_markets import replace_current_dex_arbitrage_candidates


def _json_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _address_set(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("addresses") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError(f"{path} must contain an address list or an object with addresses")
    return {str(item).strip() for item in values if str(item).strip()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Robinhood DEX evidence package and observation-only current candidates."
    )
    parser.add_argument("--discovery-report", required=True, type=Path)
    parser.add_argument("--token-registry", required=True, type=Path)
    parser.add_argument("--token-allowlist", type=Path)
    parser.add_argument("--token-blacklist", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--database-url", default="", help="Optional database for replacing current observed candidates.")
    args = parser.parse_args()

    report = _json_object(args.discovery_report)
    registry = _json_object(args.token_registry)
    allowlist = _address_set(args.token_allowlist)
    blacklist = _address_set(args.token_blacklist)
    pool_rows = build_robinhood_pool_records(
        report,
        token_registry=registry,
        token_allowlist=allowlist,
        token_blacklist=blacklist,
    )
    candidates = build_current_robinhood_candidates(pool_rows)
    write_robinhood_evidence_package(
        args.output_dir,
        discovery_report=report,
        pool_rows=pool_rows,
        candidates=candidates,
        token_registry=registry,
        token_allowlist=allowlist,
        token_blacklist=blacklist,
    )
    recorded = 0
    if args.database_url.strip():
        recorded = replace_current_dex_arbitrage_candidates(
            args.database_url.strip(),
            market_id=DEFAULT_ROBINHOOD_DEX_MARKET_ID,
            chain_id=4663,
            network="robinhood",
            candidates=candidates,
        )
    print(
        json.dumps(
            {
                "outputDir": str(args.output_dir),
                "poolCount": len(pool_rows),
                "candidateCount": len(candidates),
                "databaseRecorded": recorded,
                "executionEnabled": False,
                "broadcastEnabled": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
