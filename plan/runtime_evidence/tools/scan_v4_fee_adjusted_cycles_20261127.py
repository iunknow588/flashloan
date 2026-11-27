"""Scan every V4 three-hop cycle for a fee-adjusted spot-product arbitrage.

The arb contract (``OfficialV4FlashArb``) refuses a route unless the
product of the three fee-adjusted spot rates is strictly above ``1e18``.
This script reproduces exactly that arithmetic over the whole pool table,
so the market can be triaged without waiting for a review round.

Arithmetic copied from the contract:

* ``price18 = sqrtPriceX96 ** 2 * 1e18 // 2 ** 192``
* ``zeroForOne`` -> ``price18``; the other direction -> ``1e36 // price18``
* fee factor -> ``0`` when ``fee > 1_000_000`` else ``1e18 - fee * 1e12``
* ``netProduct = rate(entry) * rate(bridge) * rate(exit)`` scaled by 1e18

The scan covers **every** pool in the table, not only the ones the current
scope admits, so a cycle that the pool policy currently excludes can still
be reported. Each hit carries the reason it is not in the active scope.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from itertools import permutations

Q96 = 1 << 96
ONE = 10**18
MAX_FEE_FACTOR = 1_000_000


def symbol_map(cur: sqlite3.Cursor) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for table in ("token_core", "token_stocks", "token_extension", "token_test"):
        try:
            rows = cur.execute(
                f"SELECT lower(token_address), token_symbol FROM {table}"
            ).fetchall()
        except sqlite3.Error:
            continue
        for address, symbol in rows:
            symbols.setdefault(address, symbol)
    symbols.setdefault("0x" + "0" * 40, "ETH")
    return symbols


def load_pools(cur: sqlite3.Cursor) -> list[dict]:
    cur.execute(
        """
        SELECT pool_id, token0, token1, fee, tick_spacing, hooks, sqrt_price_x96,
               liquidity, depth_status, pool_status, status_reason,
               dynamic_fee, special_fee, liquidity_read_at
          FROM v4_exchange_pools_candidate_exchange
         WHERE sqrt_price_x96 IS NOT NULL AND sqrt_price_x96 > 0
        """
    )
    pools = []
    for row in cur.fetchall():
        (
            pool_id,
            token0,
            token1,
            fee,
            tick_spacing,
            hooks,
            sqrt_price_x96,
            liquidity,
            depth_status,
            pool_status,
            status_reason,
            dynamic_fee,
            special_fee,
            liquidity_read_at,
        ) = row
        hooks = (hooks or "").lower()
        if hooks in ("", "0x" + "0" * 40):
            hooks = "0x" + "0" * 40
        pools.append(
            {
                "pool_id": pool_id,
                "token0": token0.lower(),
                "token1": token1.lower(),
                "fee": int(fee or 0),
                "tick_spacing": tick_spacing,
                "hooks": hooks,
                "sqrt_price_x96": int(sqrt_price_x96),
                "liquidity": liquidity,
                "depth_status": depth_status,
                "pool_status": pool_status,
                "status_reason": status_reason,
                "dynamic_fee": dynamic_fee,
                "special_fee": special_fee,
                "liquidity_read_at": liquidity_read_at,
            }
        )
    return pools


def fee_adjusted_rate(pool: dict, zero_for_one: bool) -> int:
    """The contract's `_feeAdjustedRate`, on the stored sqrt price."""
    fee = pool["fee"]
    if fee > MAX_FEE_FACTOR:
        return 0
    square = pool["sqrt_price_x96"] * pool["sqrt_price_x96"]
    price18 = square * ONE // (1 << 192)
    if price18 == 0:
        return 0
    rate = price18 if zero_for_one else ONE * ONE // price18
    factor = ONE - fee * 10**12
    return rate * factor // ONE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.store}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()
    symbols = symbol_map(cur)
    pools = load_pools(cur)

    # best fee-adjusted rate per ordered token pair
    best: dict[tuple[str, str], dict] = {}
    for pool in pools:
        if pool["hooks"] != "0x" + "0" * 40:
            continue
        for zero_for_one in (True, False):
            rate = fee_adjusted_rate(pool, zero_for_one)
            if rate == 0:
                continue
            source = pool["token0"] if zero_for_one else pool["token1"]
            target = pool["token1"] if zero_for_one else pool["token0"]
            key = (source, target)
            current = best.get(key)
            if current is None or rate > current["rate"]:
                best[key] = {"rate": rate, "pool": pool, "zero_for_one": zero_for_one}

    def sym(address: str) -> str:
        return symbols.get(address, address[:10])

    tokens = sorted({t for pair in best for t in pair})
    cycles = []
    for a, b, c in permutations(tokens, 3):
        hop1 = best.get((a, b))
        hop2 = best.get((b, c))
        hop3 = best.get((c, a))
        if not hop1 or not hop2 or not hop3:
            continue
        product = hop1["rate"] * hop2["rate"] // ONE * hop3["rate"] // ONE
        cycles.append(
            {
                "path": [sym(a), sym(b), sym(c)],
                "addresses": [a, b, c],
                "net_product": product,
                "edge_ratio_ppm": (product - ONE) * 1_000_000 // ONE,
                "hops": [
                    {
                        "pool_id": hop["pool"]["pool_id"],
                        "fee": hop["pool"]["fee"],
                        "zero_for_one": hop["zero_for_one"],
                        "pool_status": hop["pool"]["pool_status"],
                        "status_reason": hop["pool"]["status_reason"],
                        "depth_status": hop["pool"]["depth_status"],
                        "liquidity": hop["pool"]["liquidity"],
                        "liquidity_read_at": hop["pool"]["liquidity_read_at"],
                    }
                    for hop in (hop1, hop2, hop3)
                ],
            }
        )

    cycles.sort(key=lambda item: item["net_product"], reverse=True)
    profitable = [item for item in cycles if item["net_product"] > ONE]
    anchor = "0x" + "0" * 40
    anchored = [
        item
        for item in profitable
        if item["addresses"][0] == anchor and item["addresses"][2] == anchor
    ]

    payload = {
        "chainId": args.chain_id,
        "poolCount": len(pools),
        "orderedPairCount": len(best),
        "cycleCount": len(cycles),
        "profitableCycleCount": len(profitable),
        "profitableAnchoredCycleCount": len(anchored),
        "bestCycle": cycles[0] if cycles else None,
        "profitableCycles": profitable[: args.top],
        "topCycles": cycles[: args.top],
        "notes": [
            "net_product is the contract's fee-adjusted spot product; >1e18 passes the gate",
            "edge_ratio_ppm is the gross edge in parts per million, before price impact",
            "a cycle here can still fail at execution because of price impact",
            "sqrt prices are the stored reads, the contract re-reads them live",
        ],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "poolCount": len(pools),
                "orderedPairCount": len(best),
                "cycleCount": len(cycles),
                "profitableCycleCount": len(profitable),
                "profitableAnchoredCycleCount": len(anchored),
            },
            ensure_ascii=False,
        )
    )
    for item in cycles[:12]:
        print(
            " ".join(item["path"]),
            item["edge_ratio_ppm"],
            "ppm",
            [hop["fee"] for hop in item["hops"]],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
