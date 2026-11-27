"""Measure the live fee-adjusted spot product of every published closed loop.

The arb contract reads a pool's ``sqrtPriceX96`` from the V4 pool manager
with ``extsload`` and refuses a route whose fee-adjusted spot product is not
strictly above ``1e18``.  This script reads the same slot on the same
contract and applies the same arithmetic, so it reports the contract's own
gate verdict for every closed loop **at the current block** instead of the
stored snapshot the pool tables carry.

The candidate pool set of a hop is every **active** pool of that token pair,
which is the same set the route configuration hands to the contract.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from Crypto.Hash import keccak

ONE = 10**18
POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951"


def keccak256(data: bytes) -> bytes:
    digest = keccak.new(digest_bits=256)
    digest.update(data)
    return digest.digest()


def selector(signature: str) -> str:
    return keccak256(signature.encode()).hex()[:8]


def rpc_call(rpc_url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        rpc_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def live_sqrt_price(rpc_url: str, pool_id: str) -> int:
    """The contract's `_sqrtPriceX96`: pool state slot 6, low 160 bits."""
    raw_id = bytes.fromhex(pool_id.lower().removeprefix("0x"))
    slot = keccak256(raw_id + (6).to_bytes(32, "big"))
    data = "0x" + selector("extsload(bytes32)") + slot.hex()
    response = rpc_call(
        rpc_url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": POOL_MANAGER, "data": data}, "latest"],
        },
    )
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    word = bytes.fromhex(response["result"][2:].zfill(64))
    return int.from_bytes(word, "big") & ((1 << 160) - 1)


def fee_adjusted_rate(sqrt_price_x96: int, fee: int, zero_for_one: bool) -> int:
    """The contract's `_feeAdjustedRate`."""
    if fee > 1_000_000:
        return 0
    price18 = (sqrt_price_x96 * sqrt_price_x96) * ONE // (1 << 192)
    if price18 == 0:
        return 0
    rate = price18 if zero_for_one else ONE * ONE // price18
    return rate * (ONE - fee * 10**12) // ONE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--out", required=True)
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
    addresses: dict[str, str] = {}
    for address, symbol in symbols.items():
        addresses.setdefault(symbol.upper(), address)

    active: dict[tuple[str, str], list[dict]] = {}
    for (
        pool_id,
        token0,
        token1,
        fee,
        liquidity,
        depth_status,
    ) in cur.execute(
        """
        SELECT pool_id, token0, token1, fee, liquidity, depth_status
          FROM v4_exchange_pools_candidate_exchange
         WHERE pool_status = 'active'
        """
    ):
        key = tuple(sorted((token0.lower(), token1.lower())))
        active.setdefault(key, []).append(
            {
                "pool_id": pool_id,
                "token0": token0.lower(),
                "token1": token1.lower(),
                "fee": int(fee),
                "liquidity": liquidity,
                "depth_status": depth_status,
            }
        )

    loops = cur.execute(
        """
        SELECT path_symbols_json FROM manager_v4_closed_loops
         WHERE status = 'active' ORDER BY path_symbols_json
        """
    ).fetchall()

    # one live read per distinct pool
    all_pools = {pool["pool_id"]: pool for group in active.values() for pool in group}
    with ThreadPoolExecutor(max_workers=8) as pool_exec:
        prices = dict(
            zip(
                list(all_pools.keys()),
                pool_exec.map(
                    lambda pool: live_sqrt_price(args.rpc_url, pool["pool_id"]),
                    list(all_pools.values()),
                ),
            )
        )

    results = []
    for (path_json,) in loops:
        path = json.loads(path_json)
        tokens = [
            addresses.get(str(symbol).upper(), "")
            for symbol in path[:3]
        ]
        if not all(tokens):
            results.append(
                {
                    "path": path,
                    "usable": False,
                    "net_product": 0,
                    "edge_ppm": 0,
                    "gate_passes": False,
                    "hops": [],
                }
            )
            continue
        hop_pairs = [(tokens[0], tokens[1]), (tokens[1], tokens[2]), (tokens[2], tokens[0])]
        hop_detail = []
        product = ONE
        usable = True
        for source, target in hop_pairs:
            key = tuple(sorted((source.lower(), target.lower())))
            candidates = active.get(key, [])
            best_rate = 0
            best = None
            for candidate in candidates:
                zero_for_one = candidate["token0"] == source.lower()
                rate = fee_adjusted_rate(
                    prices[candidate["pool_id"]], candidate["fee"], zero_for_one
                )
                if rate > best_rate:
                    best_rate = rate
                    best = {
                        "pool_id": candidate["pool_id"],
                        "fee": candidate["fee"],
                        "zero_for_one": zero_for_one,
                        "liquidity": candidate["liquidity"],
                        "depth_status": candidate["depth_status"],
                        "candidate_count": len(candidates),
                    }
            if best is None:
                usable = False
                hop_detail.append({"hop": "->".join((source, target)), "candidates": 0})
                break
            product = product * best_rate // ONE
            hop_detail.append(
                {
                    "hop": "->".join((source, target)),
                    "candidates": len(candidates),
                    "chosen": best,
                }
            )
        results.append(
            {
                "path": path,
                "usable": usable,
                "net_product": product,
                "edge_ppm": (product - ONE) * 1_000_000 // ONE,
                "gate_passes": product > ONE,
                "hops": hop_detail,
            }
        )

    results.sort(key=lambda item: item["net_product"], reverse=True)
    payload = {
        "chainId": args.chain_id,
        "rpcUrl": args.rpc_url,
        "poolManager": POOL_MANAGER,
        "loopCount": len(results),
        "gatePassingCount": sum(1 for item in results if item["gate_passes"]),
        "loops": results,
        "notes": [
            "net_product is the contract's fee-adjusted spot product at the latest block",
            "gate_passes is the contract's own profitability gate (net_product > 1e18)",
            "a passing gate still has to survive price impact and gas at execution",
        ],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(json.dumps({"loopCount": len(results), "gatePassingCount": payload["gatePassingCount"]}))
    for item in results:
        print(
            " ".join(item["path"]),
            item["edge_ppm"],
            "ppm",
            "GATE" if item["gate_passes"] else "-",
            [hop.get("chosen", {}).get("fee") for hop in item["hops"]],
            [hop.get("candidates") for hop in item["hops"]],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
