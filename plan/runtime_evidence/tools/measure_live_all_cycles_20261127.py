"""Scan every V4 three-hop cycle on the live chain for a fee-adjusted edge.

This is the wide version of ``measure_live_cycle_net_product``: it reads
the current ``sqrtPriceX96`` of **every** pool the table knows, keeps the
pools the arb contract can actually price (fixed fee, no hook), and reports
every three-hop cycle whose fee-adjusted spot product is above ``1e18``.

The pool policy verdict is carried on every hop, so a cycle that only exists
outside the current scope is visible and is not silently mixed with the
cycles the closed-loop catalog already runs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations

from measure_live_cycle_net_product_20261127 import (  # noqa: E402
    ONE,
    fee_adjusted_rate,
    live_sqrt_price,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.store}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()

    symbols: dict[str, str] = {}
    for table in ("token_core", "token_stocks", "token_extension", "token_test"):
        for address, symbol in cur.execute(
            f"SELECT lower(token_address), token_symbol FROM {table}"
        ):
            symbols.setdefault(address, symbol)
    symbols.setdefault("0x" + "0" * 40, "ETH")

    rows = cur.execute(
        """
        SELECT pool_id, token0, token1, fee, hooks, pool_status, status_reason,
               liquidity, depth_status, liquidity_read_at
          FROM v4_exchange_pools_candidate_exchange
         WHERE sqrt_price_x96 IS NOT NULL AND sqrt_price_x96 > 0
        """
    ).fetchall()
    zero = "0x" + "0" * 40
    pools = []
    for (
        pool_id,
        token0,
        token1,
        fee,
        hooks,
        pool_status,
        status_reason,
        liquidity,
        depth_status,
        liquidity_read_at,
    ) in rows:
        hooks = (hooks or zero).lower()
        pools.append(
            {
                "pool_id": pool_id,
                "token0": token0.lower(),
                "token1": token1.lower(),
                "fee": int(fee),
                "hooks": hooks,
                "pool_status": pool_status,
                "status_reason": status_reason,
                "liquidity": liquidity,
                "depth_status": depth_status,
                "liquidity_read_at": liquidity_read_at,
            }
        )

    # only pools the contract can price: fixed fee the contract accepts, no hook
    priceable = [
        pool
        for pool in pools
        if pool["hooks"] == zero and 0 < pool["fee"] <= 1_000_000
    ]

    with ThreadPoolExecutor(max_workers=10) as pool_exec:
        prices = dict(
            zip(
                [pool["pool_id"] for pool in priceable],
                pool_exec.map(
                    lambda pool: live_sqrt_price(args.rpc_url, pool["pool_id"]),
                    priceable,
                ),
            )
        )

    best: dict[tuple[str, str], dict] = {}
    for pool in priceable:
        for zero_for_one in (True, False):
            rate = fee_adjusted_rate(
                prices[pool["pool_id"]], pool["fee"], zero_for_one
            )
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

    tokens = sorted({token for pair in best for token in pair})
    cycles = []
    for a, b, c in permutations(tokens, 3):
        hop1, hop2, hop3 = best.get((a, b)), best.get((b, c)), best.get((c, a))
        if not hop1 or not hop2 or not hop3:
            continue
        product = hop1["rate"] * hop2["rate"] // ONE * hop3["rate"] // ONE
        cycles.append(
            {
                "path": [sym(a), sym(b), sym(c)],
                "addresses": [a, b, c],
                "net_product": product,
                "edge_ppm": (product - ONE) * 1_000_000 // ONE,
                "anchored_at_funding_token": sym(a) in ("ETH", "TZ", "USDE", "USDG", "WETH"),
                "hops": [
                    {
                        "pool_id": hop["pool"]["pool_id"],
                        "fee": hop["pool"]["fee"],
                        "zero_for_one": hop["zero_for_one"],
                        "pool_status": hop["pool"]["pool_status"],
                        "status_reason": hop["pool"]["status_reason"],
                        "liquidity": hop["pool"]["liquidity"],
                        "depth_status": hop["pool"]["depth_status"],
                        "liquidity_read_at": hop["pool"]["liquidity_read_at"],
                    }
                    for hop in (hop1, hop2, hop3)
                ],
            }
        )

    cycles.sort(key=lambda item: item["net_product"], reverse=True)
    profitable = [item for item in cycles if item["net_product"] > ONE]
    anchored = [item for item in profitable if item["anchored_at_funding_token"]]
    payload = {
        "chainId": args.chain_id,
        "rpcUrl": args.rpc_url,
        "poolCount": len(pools),
        "priceablePoolCount": len(priceable),
        "orderedPairCount": len(best),
        "cycleCount": len(cycles),
        "profitableCycleCount": len(profitable),
        "profitableAnchoredCycleCount": len(anchored),
        "profitableCycles": profitable[: args.top],
        "topCycles": cycles[: args.top],
        "notes": [
            "every price is read live from the V4 pool manager at the latest block",
            "net_product is the contract's fee-adjusted spot product; >1e18 passes the gate",
            "a passing gate still has to survive price impact and gas at execution",
        ],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "poolCount": len(pools),
                "priceablePoolCount": len(priceable),
                "cycleCount": len(cycles),
                "profitableCycleCount": len(profitable),
                "profitableAnchoredCycleCount": len(anchored),
            }
        )
    )
    for item in cycles[:15]:
        print(
            " ".join(item["path"]),
            item["edge_ppm"],
            "ppm",
            [hop["fee"] for hop in item["hops"]],
            [hop["pool_status"] for hop in item["hops"]],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
