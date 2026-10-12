"""Reconcile RobinScreener high-liquidity V4 pools with local evidence.

RobinScreener is a verification reference only; production data continues to
come from on-chain PoolManager Initialize logs. This script normalizes the
screener items to pool id / token pair / fee / hook / liquidity, then compares
them against the local V4 pool index and SQLite candidate/liquidity tables.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib import request as urllib_request

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.storage_v4_exchange import (  # noqa: E402
    load_v4_exchange_pools,
    load_v4_pool_liquidity,
)


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
SCREENER_API = "https://robinscreener.io/api/v1/screener"


def _text(value: object) -> str:
    return str(value or "").strip()


def _lower(value: object) -> str:
    return _text(value).lower()


def _number(value: object, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pool_id(item: dict) -> str:
    return _lower(item.get("poolKey") or item.get("poolAddress"))


def _normalize_hooks(value: object) -> str:
    text = _lower(value)
    if not text or text == ZERO_ADDRESS:
        return ZERO_ADDRESS
    return text


def _token_address(item: dict, key: str) -> str:
    token = item.get(key)
    if isinstance(token, dict):
        return _lower(token.get("address"))
    return _lower(token)


def _screener_v4_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if _lower(item.get("protocolVersion")) != "uniswap_v4":
            continue
        pool_id = _pool_id(item)
        if not pool_id.startswith("0x") or len(pool_id) != 66:
            continue
        rows.append(
            {
                "poolId": pool_id,
                "token0": _token_address(item, "token0"),
                "token1": _token_address(item, "token1"),
                "fee": item.get("feeTier"),
                "dynamicFee": bool(item.get("dynamicFee")),
                "hooks": _normalize_hooks(item.get("hooksAddress")),
                "tickSpacing": item.get("tickSpacing"),
                "liquidityUsd": _number(item.get("liquidityUsd")),
                "volume24hUsd": _number(item.get("volume24hUsd")),
                "createdBlock": item.get("createdBlock"),
                "lastSwapBlock": item.get("lastSwapBlock"),
                "screenerRank": item.get("rank"),
            }
        )
    return rows


def _fetch_screener(limit: int) -> dict:
    query = f"?q=ETH&preset=high-liquidity&sort=relevance&limit={int(limit)}"
    req = urllib_request.Request(
        SCREENER_API + query,
        headers={"User-Agent": "flashloan-robinhood-mainnet-v4-audit/1.0"},
    )
    with urllib_request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"items": []}


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _index_rows(index_path: Path) -> list[dict]:
    payload = _read_json(index_path)
    return [row for row in payload.get("pools") or [] if isinstance(row, dict)]


def _liquidity_rows(liquidity_path: Path) -> list[dict]:
    payload = _read_json(liquidity_path)
    return [row for row in payload.get("pools") or [] if isinstance(row, dict)]


def _candidate_rows(candidate_path: Path) -> list[dict]:
    payload = _read_json(candidate_path)
    return [row for row in payload.get("pools") or [] if isinstance(row, dict)]


def _candidate_rows_from_sqlite(database_url: str) -> list[dict]:
    from core.db.storage_business_io import query

    rows = query(
        database_url,
        """
        SELECT pool_id, token0, token1, fee, dynamic_fee, special_fee, hooks
        FROM dex_candidate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY pool_id
        """,
        ("robinhood", 4663, "uniswap_v4"),
    )
    return [
        {
            "poolId": str(row.get("pool_id") or "").strip().lower(),
            "token0": str(row.get("token0") or "").strip().lower(),
            "token1": str(row.get("token1") or "").strip().lower(),
            "fee": row.get("fee"),
            "dynamicFee": bool(row.get("dynamic_fee")),
            "hooks": _normalize_hooks(row.get("hooks")),
        }
        for row in rows
        if str(row.get("pool_id") or "").strip()
    ]


def _fee_mismatch(screener: dict, local: dict) -> bool:
    screener_fee = screener.get("fee")
    local_fee = local.get("fee")
    if screener.get("dynamicFee"):
        return not local.get("dynamicFee")
    try:
        return int(screener_fee) != int(local_fee)
    except (TypeError, ValueError):
        return True


def _hook_mismatch(screener: dict, local: dict) -> bool:
    return _normalize_hooks(local.get("hooks")) != screener["hooks"]


def _has_liquidity(row: dict) -> bool:
    raw = row.get("hasLiquidity") or row.get("has_liquidity")
    if raw is not None:
        return bool(raw)
    liquidity_index = _number(row.get("liquidityIndex") or row.get("liquidity_index"))
    estimated_tvl = _number(row.get("estimatedTvlUsd") or row.get("estimated_tvl_usd"))
    return liquidity_index > 0 or estimated_tvl > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--database", default="")
    parser.add_argument("--index", default="")
    parser.add_argument("--liquidity", default="")
    parser.add_argument("--candidates", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    cache_path = Path(args.cache or os.path.join(os.environ.get("TEMP", ""), "robinscreener_eth_high_liquidity_page1.json"))
    if args.fetch or not cache_path.exists():
        payload = _fetch_screener(args.limit)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        payload = _read_json(cache_path)
    items = payload.get("items") or []
    screener_rows = _screener_v4_rows(items)

    index_path = Path(args.index or SRC_ROOT / "runtime" / "evidence" / "robinhood" / "v4_pool_index" / "latest.json")
    liquidity_path = Path(args.liquidity or SRC_ROOT / "runtime" / "evidence" / "robinhood" / "v4_pool_liquidity" / "latest.json")
    index_rows = _index_rows(index_path)
    liquidity_rows = _liquidity_rows(liquidity_path)

    index_by_id = {_lower(row.get("poolId") or row.get("pool")): row for row in index_rows}
    liquidity_by_id = {_lower(row.get("poolId") or row.get("pool")): row for row in liquidity_rows}

    database = absolute_database_path(args.database)
    sqlite_pools = load_v4_exchange_pools(
        str(database),
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
    )
    sqlite_liquidity = load_v4_pool_liquidity(
        str(database),
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
    )
    sqlite_by_id = {_lower(row.get("pool_id") or row.get("poolId")): row for row in sqlite_pools}
    sqlite_liquidity_by_id = {_lower(row.get("pool_id") or row.get("poolId")): row for row in sqlite_liquidity}
    candidate_path = Path(args.candidates) if args.candidates else None
    candidate_rows = _candidate_rows(candidate_path) if candidate_path is not None else _candidate_rows_from_sqlite(str(database))
    candidate_by_id = {_lower(row.get("poolId") or row.get("pool")): row for row in candidate_rows}

    missing_index: list[dict] = []
    missing_sqlite: list[dict] = []
    missing_candidates: list[dict] = []
    missing_liquidity: list[dict] = []
    fee_mismatches: list[dict] = []
    hook_mismatches: list[dict] = []
    liquidity_mismatches: list[dict] = []
    normal_eligible = 0
    for screener in screener_rows:
        pool_id = screener["poolId"]
        if pool_id not in index_by_id:
            missing_index.append(screener)
        else:
            index_row = index_by_id[pool_id]
            if _fee_mismatch(screener, index_row):
                fee_mismatches.append({"poolId": pool_id, "screener": screener, "local": index_row})
            if _hook_mismatch(screener, index_row):
                hook_mismatches.append({"poolId": pool_id, "screener": screener, "local": index_row})
        if pool_id not in sqlite_by_id:
            missing_sqlite.append(screener)
        elif pool_id not in sqlite_liquidity_by_id or not _has_liquidity(sqlite_liquidity_by_id[pool_id]):
            missing_liquidity.append(screener)
        if pool_id not in candidate_by_id:
            missing_candidates.append(screener)
        if screener["dynamicFee"]:
            continue
        try:
            fee = int(screener.get("fee"))
        except (TypeError, ValueError):
            continue
        if 0 < fee <= 3000 and screener["hooks"] == ZERO_ADDRESS:
            normal_eligible += 1

    for pool_id in set(sqlite_liquidity_by_id) & set(screener_rows and {row["poolId"] for row in screener_rows}):
        screener = next(row for row in screener_rows if row["poolId"] == pool_id)
        local_liquidity = sqlite_liquidity_by_id[pool_id]
        if bool(screener.get("liquidityUsd") or 0) > 0 != _has_liquidity(local_liquidity):
            liquidity_mismatches.append(
                {
                    "poolId": pool_id,
                    "screenerLiquidityUsd": screener.get("liquidityUsd"),
                    "localLiquidity": local_liquidity.get("liquidity") or local_liquidity.get("liquidity_index"),
                    "localHasLiquidity": _has_liquidity(local_liquidity),
                }
            )

    report = {
        "ok": not (missing_index or missing_sqlite or missing_liquidity),
        "generatedAt": None,
        "cachePath": str(cache_path),
        "database": str(database),
        "counts": {
            "screenerItems": len(items),
            "screenerV4Pools": len(screener_rows),
            "screenerV4PositiveLiquidity": sum(1 for row in screener_rows if row.get("liquidityUsd") > 0),
            "screenerV4NormalEligible": normal_eligible,
            "localIndexPools": len(index_rows),
            "localLiquidityRows": len(liquidity_rows),
            "localSqlitePools": len(sqlite_pools),
            "localStaticCandidates": len(candidate_rows),
        },
        "missingFromIndex": missing_index,
        "missingFromSqlite": missing_sqlite,
        "missingFromStaticCandidates": missing_candidates,
        "missingLiquidityInSqlite": missing_liquidity,
        "feeMismatches": fee_mismatches,
        "hookMismatches": hook_mismatches,
        "liquidityMismatches": liquidity_mismatches,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
