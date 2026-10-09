"""Find every Uniswap V4 pool for an arbitrary two-token pair.

The script is intentionally small and read-only: it loads the static V4
PoolKey snapshot and the separate dynamic liquidity snapshot, normalizes both
token inputs, then returns every pool whose currency0/currency1 pair matches.
It does not call RPC and never rewrites evidence files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.v4_pool_snapshot import (  # noqa: E402
    default_v4_pool_liquidity_path,
    default_v4_pool_snapshot_path,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _address(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text.startswith("0x") and len(text) == 42 else ""


def _pool_id(pool: dict[str, Any]) -> str:
    return str(
        pool.get("pool") or pool.get("poolId") or pool.get("pool_id") or pool.get("id") or ""
    ).strip()


def _pool_tokens(pool: dict[str, Any]) -> tuple[str, str]:
    token0 = _address(pool.get("token0") or pool.get("currency0"))
    token1 = _address(pool.get("token1") or pool.get("currency1"))
    return token0, token1


def _merge_liquidity(snapshot: dict[str, Any], liquidity: dict[str, Any]) -> list[dict[str, Any]]:
    liquidity_by_pool_id = {
        str(item.get("poolId") or item.get("pool") or "").strip().lower(): item
        for item in liquidity.get("pools") or []
        if isinstance(item, dict)
    }
    merged: list[dict[str, Any]] = []
    for pool in snapshot.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        copied = dict(pool)
        live = liquidity_by_pool_id.get(_pool_id(pool).lower()) or {}
        copied.update(live)
        copied["liquiditySnapshotAt"] = (
            liquidity.get("generatedAt") or liquidity.get("liquidityScanAt")
        )
        merged.append(copied)
    return merged


def _liquidity_number(pool: dict[str, Any]) -> float:
    try:
        return float(str(pool.get("liquidity") or pool.get("liquidityIndex") or "0"))
    except (TypeError, ValueError):
        return 0.0


def _has_depth(pool: dict[str, Any]) -> bool:
    has_liquidity = pool.get("hasLiquidity")
    if has_liquidity is not None:
        return bool(has_liquidity) or _liquidity_number(pool) > 0
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    try:
        tvl = float(balances.get("tvlUsd") or pool.get("estimatedTvlUsd") or 0)
    except (TypeError, ValueError):
        tvl = 0.0
    return _liquidity_number(pool) > 0 or tvl > 0


def _is_fixed_fee(pool: dict[str, Any]) -> bool:
    try:
        fee = int(str(pool.get("fee") or "").strip() or 0)
    except (TypeError, ValueError):
        return False
    dynamic_fee_flag = 8388608
    return fee > 0 and not bool(fee & dynamic_fee_flag) and fee <= 100_000


def _resolve_token(input_value: str, pools: list[dict[str, Any]]) -> list[str]:
    needle = str(input_value or "").strip()
    if not needle:
        return []
    direct = _address(needle)
    if direct:
        return [direct]
    lower = needle.lower()
    seen: dict[str, str] = {}
    for pool in pools:
        token0, token1 = _pool_tokens(pool)
        symbols = (
            str(pool.get("token0Symbol") or "").lower(),
            str(pool.get("token1Symbol") or "").lower(),
        )
        for address, symbol in ((token0, symbols[0]), (token1, symbols[1])):
            if address and symbol == lower:
                seen[address] = address
    return sorted(seen)


def _find_pools(
    token_a: str,
    token_b: str,
    pools: list[dict[str, Any]],
    *,
    strict_addresses: bool,
) -> dict[str, Any]:
    addresses_a = _resolve_token(token_a, pools)
    addresses_b = _resolve_token(token_b, pools)
    if strict_addresses:
        addresses_a = [item for item in addresses_a if item == _address(token_a)]
        addresses_b = [item for item in addresses_b if item == _address(token_b)]
    result_pools: list[dict[str, Any]] = []
    matched_edges: list[list[str]] = []
    for address_a in addresses_a:
        for address_b in addresses_b:
            if address_a == address_b:
                continue
            edge = tuple(sorted((address_a, address_b)))
            for pool in pools:
                token0, token1 = _pool_tokens(pool)
                if tuple(sorted((token0, token1))) == edge:
                    result_pools.append(pool)
                    if list(edge) not in matched_edges:
                        matched_edges.append(list(edge))
    return {
        "tokenA": {
            "input": token_a,
            "addresses": addresses_a,
            "addressCount": len(addresses_a),
        },
        "tokenB": {
            "input": token_b,
            "addresses": addresses_b,
            "addressCount": len(addresses_b),
        },
        "matchedAddressPairCount": len(matched_edges),
        "matchedAddressPairs": matched_edges,
        "poolCount": len(result_pools),
        "fixedFeePoolCount": sum(1 for pool in result_pools if _is_fixed_fee(pool)),
        "depthPoolCount": sum(1 for pool in result_pools if _has_depth(pool)),
        "hasLiquidityPoolCount": sum(
            1 for pool in result_pools if bool(pool.get("hasLiquidity"))
        ),
        "pools": result_pools,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("token_a", help="Token A address or symbol, e.g. TA")
    parser.add_argument("token_b", help="Token B address or symbol, e.g. TB")
    parser.add_argument(
        "--snapshot",
        default="",
        help="Static V4 PoolKey snapshot path; defaults to chain evidence.",
    )
    parser.add_argument(
        "--liquidity",
        default="",
        help="Dynamic V4 liquidity snapshot path; defaults to chain evidence.",
    )
    parser.add_argument(
        "--strict-addresses",
        action="store_true",
        help="Require both inputs to be 0x addresses instead of resolving by symbol.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print counts without embedding every pool row.",
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot or default_v4_pool_snapshot_path() or "")
    liquidity_path = Path(args.liquidity or default_v4_pool_liquidity_path() or "")
    if not snapshot_path.exists():
        print(json.dumps({"ok": False, "reason": "snapshot_missing", "path": str(snapshot_path)}))
        return 1
    if not liquidity_path.exists():
        print(json.dumps({"ok": False, "reason": "liquidity_missing", "path": str(liquidity_path)}))
        return 1

    pools = _merge_liquidity(_read_json(snapshot_path), _read_json(liquidity_path))
    result = _find_pools(
        args.token_a,
        args.token_b,
        pools,
        strict_addresses=args.strict_addresses,
    )
    payload = {
        "ok": True,
        "snapshotPath": str(snapshot_path),
        "liquidityPath": str(liquidity_path),
        "sourcePoolCount": len(pools),
        **result,
    }
    if args.summary_only:
        payload.pop("pools", None)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
