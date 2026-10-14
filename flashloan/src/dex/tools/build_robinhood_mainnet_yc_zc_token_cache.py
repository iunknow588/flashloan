from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.chain_data_registry import chain_data_file_path  # noqa: E402
from dex.intent_trade.chains.robinhood_common.stock_token_registry import (  # noqa: E402
    merge_stock_registry_into_metadata,
)


NETWORK = "robinhood"
CHAIN_ID = 4663
ANCHOR_SYMBOL = "ETH"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _default_snapshot_path() -> Path:
    return SRC_ROOT / "runtime" / "cache" / "candidate_pools" / NETWORK / f"chain-{CHAIN_ID}" / "snapshot.json"


def _default_metadata_path() -> Path:
    try:
        path = chain_data_file_path(NETWORK, "v4_token_metadata", chain_id=CHAIN_ID)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return SRC_ROOT / "runtime" / "evidence" / NETWORK / "v4_token_metadata.json"


def _default_output_path() -> Path:
    try:
        path = chain_data_file_path(NETWORK, "yc_zc_tokens", chain_id=CHAIN_ID)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return SRC_ROOT / "runtime" / "evidence" / NETWORK / "yc_zc_tokens.json"


def _text(value: object) -> str:
    return str(value or "").strip()


def _address(value: object) -> str:
    text = _text(value)
    return text if text.lower().startswith("0x") and len(text) == 42 else ""


def _collect_shared_tokens(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for group in snapshot.get("anchorGroups") or []:
        if not isinstance(group, dict):
            continue
        if group.get("anchorSymbol") != ANCHOR_SYMBOL or not group.get("ycZcSameSet"):
            continue
        for token in group.get("sharedTokens") or []:
            if not isinstance(token, dict):
                continue
            symbol = _text(token.get("symbol")).upper()
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            rows.append(token)
    return rows


def _snapshot_from_database(database: str) -> dict[str, Any]:
    from core.db.database_path import absolute_database_path
    from core.db.storage_business_io import query

    resolved = absolute_database_path(database)
    token_rows = query(
        str(resolved),
        """
        SELECT a.token_address, c.token_symbol, a.pool_count,
               a.yc_optimal_pool_id, a.zc_optimal_pool_id
        FROM dex_candidate_anchor_tokens a
        LEFT JOIN chain_tokens c
          ON c.network = a.network AND c.chain_id = a.chain_id
         AND c.token_address = a.token_address
        WHERE a.network = ? AND a.chain_id = ? AND a.protocol = ?
          AND a.anchor_symbol = ?
        ORDER BY a.token_address
        """,
        (NETWORK, CHAIN_ID, "uniswap_v4", ANCHOR_SYMBOL),
    )
    shared_tokens: list[dict[str, Any]] = []
    for row in token_rows:
        token_address = _address(row.get("token_address"))
        if not token_address:
            continue
        symbol = _text(row.get("token_symbol") or "").upper()
        if not symbol or symbol.startswith("0X"):
            continue
        shared_tokens.append(
            {
                "symbol": symbol,
                "address": token_address,
                "decimals": 18,
                "poolCount": str(row.get("pool_count") or 0),
                "source": "sqlite_candidate_anchor_tokens",
                "pools": [],
            }
        )
    return {
        "schemaVersion": "robinhood-candidate-pool-snapshot-v1",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "anchorGroups": [
            {
                "anchorSymbol": ANCHOR_SYMBOL,
                "ycZcSameSet": True,
                "sharedTokens": shared_tokens,
                "sharedTokenCount": str(len(shared_tokens)),
            }
        ],
    }


def _build_cache(
    snapshot: dict[str, Any],
    metadata_payload: dict[str, Any],
) -> dict[str, Any]:
    metadata_payload = merge_stock_registry_into_metadata(metadata_payload)
    metadata = metadata_payload.get("tokens") if isinstance(metadata_payload.get("tokens"), dict) else {}
    shared = _collect_shared_tokens(snapshot)
    tokens: list[dict[str, Any]] = []
    for token in shared:
        address = _address(token.get("address"))
        normalized = address.lower()
        meta = metadata.get(normalized) if isinstance(metadata.get(normalized), dict) else {}
        symbol = _text(meta.get("symbol") or token.get("symbol")).upper()
        decimals = meta.get("decimals")
        if decimals is None:
            decimals = token.get("decimals")
        pool_count = _text(token.get("poolCount"))
        if not pool_count:
            pool_count = str(len(token.get("pools") or []))
        if not address:
            continue
        tokens.append(
            {
                "symbol": symbol or address[:10].upper(),
                "address": address,
                "decimals": decimals,
                "poolCount": pool_count,
                "source": "v4_token_metadata" if meta else "shared_pool_index",
            }
        )
    tokens.sort(key=lambda item: (-int(item["poolCount"] or 0), item["symbol"]))
    return {
        "schemaVersion": "robinhood-yc-zc-token-cache-v1",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "anchorSymbol": ANCHOR_SYMBOL,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tokenCount": len(tokens),
        "tokens": tokens,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a stable Robinhood mainnet YC/ZC token cache from the shared Uniswap V4 candidate set."
    )
    parser.add_argument("--snapshot", default="", help="Optional candidate pool snapshot path; default is the chain-4663 snapshot.")
    parser.add_argument("--metadata", default="", help="Optional v4 token metadata path; default uses the chain registry.")
    parser.add_argument("--output", default="", help="Optional output path; default uses the yc_zc_tokens data file.")
    parser.add_argument(
        "--database",
        default="",
        help="Optional SQLite path used to generate a candidate snapshot when the cache snapshot is missing.",
    )
    parser.add_argument("--no-write", action="store_true", help="Print JSON without writing the generated payload.")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot) if args.snapshot else _default_snapshot_path()
    metadata_path = Path(args.metadata) if args.metadata else _default_metadata_path()
    output_path = Path(args.output) if args.output else _default_output_path()
    snapshot = _read_json(snapshot_path)
    metadata = _read_json(metadata_path)
    if not snapshot and args.database:
        snapshot = _snapshot_from_database(args.database)
        if not args.no_write:
            _write_json(snapshot_path, snapshot)
    if not snapshot:
        raise SystemExit(f"candidate snapshot missing: {snapshot_path}")
    payload = _build_cache(snapshot, metadata)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.no_write:
        print(text)
    else:
        _write_json(output_path, payload)
        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "snapshot": str(snapshot_path),
                    "metadata": str(metadata_path),
                    "tokenCount": payload["tokenCount"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
