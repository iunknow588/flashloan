"""Re-price every three-hop cycle from the chain, not from the stored read.

The offline scan (``scan_v4_fee_adjusted_cycles_20261127.py``) multiplies the
fee-adjusted spot rates of the **stored** ``sqrt_price_x96``.  A pool that the
scope does not admit is never refreshed, so its stored price is the price it
had when the pool was last measured.  A cycle built on such a price can look
profitable long after the market moved, which is exactly the "old price"
effect: the arbitrage is an artefact of the stored read, not a route the
chain would take today.

This probe removes the artefact.  It reads the **live** ``Slot0`` of every
pool through the pool manager's ``extsload`` and repeats the contract's own
arithmetic on the live values:

* ``price18 = sqrtPriceX96 ** 2 * 1e18 // 2 ** 192``
* ``zeroForOne`` -> ``price18``; the other direction -> ``1e36 // price18``
* fee factor -> ``0`` when ``fee > 1_000_000`` else ``1e18 - fee * 1e12``
* ``netProduct = rate(entry) * rate(bridge) * rate(exit)`` scaled by 1e18

The live ``Slot0`` also carries the **current** ``lpFee``.  For a pool with a
dynamic fee the stored fee column is only the "dynamic" flag, so the stored
read cannot price such a pool at all; the live ``lpFee`` can.  The cycles are
therefore reported in two groups: the pools the strategy accepts today
(fixed fee, no hook) and the pools the current policy excludes, so a policy
decision can be taken on measured numbers instead of on a guess.

The probe is read-only: it never writes to the store and it never sends a
transaction.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from itertools import permutations

from eth_utils import keccak

ONE = 10**18
MAX_FEE_FACTOR = 1_000_000
DYNAMIC_FEE_FLAG = 8388608
ZERO_HOOK = "0x" + "0" * 40
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
DEFAULT_RPCS = (
    "https://rpc.mainnet.chain.robinhood.com",
    "https://robinhood-rpc.publicnode.com",
)
EXTsLOAD_SELECTOR = keccak(text="extsload(bytes32)")[:4]


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
    """Every pool that carries a stored price, newest read wins."""
    rows = cur.execute(
        """
        SELECT pool_id, token0, token1, fee, hooks, sqrt_price_x96, liquidity,
               depth_status, pool_status, status_reason, dynamic_fee,
               special_fee, liquidity_read_at, liquidity_block_number
          FROM v4_exchange_pools_candidate_exchange
         WHERE sqrt_price_x96 IS NOT NULL AND sqrt_price_x96 > 0
        """
    ).fetchall()
    pools: dict[str, dict] = {}
    for row in rows:
        (
            pool_id,
            token0,
            token1,
            fee,
            hooks,
            sqrt_price_x96,
            liquidity,
            depth_status,
            pool_status,
            status_reason,
            dynamic_fee,
            special_fee,
            liquidity_read_at,
            liquidity_block_number,
        ) = row
        hooks = (hooks or "").lower()
        if hooks in ("", ZERO_HOOK):
            hooks = ZERO_HOOK
        pool = {
            "pool_id": (pool_id or "").lower(),
            "token0": (token0 or "").lower(),
            "token1": (token1 or "").lower(),
            "fee": int(fee or 0),
            "hooks": hooks,
            "sqrt_price_x96": int(sqrt_price_x96),
            "liquidity": liquidity,
            "depth_status": depth_status,
            "pool_status": pool_status,
            "status_reason": status_reason,
            "dynamic_fee": dynamic_fee,
            "special_fee": special_fee,
            "liquidity_read_at": liquidity_read_at,
            "liquidity_block_number": liquidity_block_number,
        }
        known = pools.get(pool["pool_id"])
        if known is None or str(pool["liquidity_read_at"] or "") > str(
            known["liquidity_read_at"] or ""
        ):
            pools[pool["pool_id"]] = pool
    return list(pools.values())


def state_slot(pool_id: str) -> bytes:
    """The ``Pool.State`` storage slot of a pool id.

    ``Pool.State`` is the sixth word of the pool's state mapping, so the
    slot is ``keccak256(poolId ++ 0x00 * 31 ++ 0x06)``.
    """
    raw = bytes.fromhex(pool_id[2:])
    return keccak(raw + b"\x00" * 31 + b"\x06")


def rpc_batch(url: str, requests: list[dict], timeout: int = 30) -> list[dict]:
    body = json.dumps(requests).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def eth_call(url: str, data: str, timeout: int = 30) -> str:
    rows = rpc_batch(
        url,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{"to": POOL_MANAGER, "data": data}, "latest"],
            }
        ],
        timeout,
    )
    row = rows[0]
    if "error" in row:
        raise RuntimeError(str(row["error"]))
    return row["result"]


def block_number(url: str, timeout: int = 30) -> int:
    rows = rpc_batch(
        url,
        [{"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}],
        timeout,
    )
    return int(rows[0]["result"], 16)


def parse_slot0(raw: str) -> dict:
    """``sqrtPriceX96`` (low 160 bits), ``tick``, ``protocolFee``, ``lpFee``."""
    data = bytes.fromhex(raw[2:] if raw.startswith("0x") else raw)
    word = data[:32].ljust(32, b"\x00")
    sqrt = int.from_bytes(word[12:32], "big")
    tick_bits = int.from_bytes(word[9:12], "big")
    tick = tick_bits - (1 << 24) if tick_bits & (1 << 23) else tick_bits
    return {
        "sqrt_price_x96": sqrt,
        "tick": tick,
        "protocol_fee": int.from_bytes(word[6:9], "big"),
        "lp_fee": int.from_bytes(word[3:6], "big"),
    }


def live_states(pools: list[dict], rpc: str, timeout: int = 30) -> dict:
    states: dict[str, dict] = {}
    batch_size = 40
    for start in range(0, len(pools), batch_size):
        chunk = pools[start : start + batch_size]
        requests = []
        for index, pool in enumerate(chunk):
            data = EXTsLOAD_SELECTOR + state_slot(pool["pool_id"])
            requests.append(
                {
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "eth_call",
                    "params": [{"to": POOL_MANAGER, "data": "0x" + data.hex()}, "latest"],
                }
            )
        try:
            rows = rpc_batch(rpc, requests, timeout)
        except (urllib.error.URLError, OSError, RuntimeError, json.JSONDecodeError):
            continue
        for row in rows:
            index = row.get("id")
            if not isinstance(index, int) or index >= len(chunk):
                continue
            if "error" in row:
                continue
            try:
                states[chunk[index]["pool_id"]] = parse_slot0(row["result"])
            except (KeyError, ValueError):
                continue
    return states


def fee_adjusted_rate(sqrt_price_x96: int, fee: int, zero_for_one: bool) -> int:
    if fee > MAX_FEE_FACTOR:
        return 0
    square = sqrt_price_x96 * sqrt_price_x96
    price18 = square * ONE // (1 << 192)
    if price18 == 0:
        return 0
    rate = price18 if zero_for_one else ONE * ONE // price18
    factor = ONE - fee * 10**12
    return rate * factor // ONE


def best_rates(pools: list[dict], live: dict, use_live: bool) -> dict:
    """The best fee-adjusted rate per ordered token pair."""
    best: dict[tuple[str, str], dict] = {}
    for pool in pools:
        if pool["hooks"] != ZERO_HOOK:
            continue
        state = live.get(pool["pool_id"])
        if use_live:
            if state is None:
                continue
            sqrt_price = state["sqrt_price_x96"]
            fee = state["lp_fee"]
        else:
            sqrt_price = pool["sqrt_price_x96"]
            fee = pool["fee"]
        for zero_for_one in (True, False):
            rate = fee_adjusted_rate(sqrt_price, fee, zero_for_one)
            if rate == 0:
                continue
            source = pool["token0"] if zero_for_one else pool["token1"]
            target = pool["token1"] if zero_for_one else pool["token0"]
            key = (source, target)
            current = best.get(key)
            if current is None or rate > current["rate"]:
                best[key] = {
                    "rate": rate,
                    "pool": pool,
                    "zero_for_one": zero_for_one,
                    "fee": fee,
                    "sqrt_price_x96": sqrt_price,
                }
    return best


def cycles_of(best: dict, symbols: dict) -> list[dict]:
    tokens = sorted({token for pair in best for token in pair})
    cycles: list[dict] = []
    for a, b, c in permutations(tokens, 3):
        hop1 = best.get((a, b))
        hop2 = best.get((b, c))
        hop3 = best.get((c, a))
        if not hop1 or not hop2 or not hop3:
            continue
        product = hop1["rate"] * hop2["rate"] // ONE * hop3["rate"] // ONE
        cycles.append(
            {
                "path": [symbols.get(a, a[:10]), symbols.get(b, b[:10]), symbols.get(c, c[:10])],
                "addresses": [a, b, c],
                "net_product": product,
                "edge_ratio_ppm": (product - ONE) * 1_000_000 // ONE,
                "hops": [
                    {
                        "pool_id": hop["pool"]["pool_id"],
                        "fee": hop["fee"],
                        "zero_for_one": hop["zero_for_one"],
                        "pool_status": hop["pool"]["pool_status"],
                        "status_reason": hop["pool"]["status_reason"],
                        "depth_status": hop["pool"]["depth_status"],
                        "liquidity_read_at": hop["pool"]["liquidity_read_at"],
                        "liquidity_block_number": hop["pool"]["liquidity_block_number"],
                    }
                    for hop in (hop1, hop2, hop3)
                ],
            }
        )
    cycles.sort(key=lambda item: item["net_product"], reverse=True)
    return cycles


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rpc", default="")
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.store}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()
    symbols = symbol_map(cur)
    pools = load_pools(cur)

    rpc = args.rpc.strip() or DEFAULT_RPCS[0]
    live: dict[str, dict] = {}
    head = 0
    errors: list[str] = []
    for candidate in ([rpc] if args.rpc.strip() else list(DEFAULT_RPCS)):
        try:
            head = block_number(candidate)
            live = live_states(pools, candidate)
        except (urllib.error.URLError, OSError, RuntimeError, json.JSONDecodeError) as error:
            errors.append(f"{candidate}: {error}")
            continue
        if live:
            rpc = candidate
            break
        errors.append(f"{candidate}: no live state returned")

    stored_best = best_rates(pools, live, use_live=False)
    live_best = best_rates(pools, live, use_live=True)
    stored_cycles = cycles_of(stored_best, symbols)
    live_cycles = cycles_of(live_best, symbols)

    anchor = "0x" + "0" * 40

    def summarise(cycles: list[dict]) -> dict:
        profitable = [item for item in cycles if item["net_product"] > ONE]
        anchored = [
            item
            for item in profitable
            if item["addresses"][0] == anchor and item["addresses"][2] == anchor
        ]
        return {
            "cycleCount": len(cycles),
            "profitableCycleCount": len(profitable),
            "profitableAnchoredCycleCount": len(anchored),
            "bestCycle": cycles[0] if cycles else None,
            "profitableCycles": profitable[: args.top],
        }

    stale = []
    for pool in pools:
        state = live.get(pool["pool_id"])
        if state is None:
            continue
        if state["sqrt_price_x96"] != pool["sqrt_price_x96"]:
            stale.append(
                {
                    "pool_id": pool["pool_id"],
                    "token0": symbols.get(pool["token0"], pool["token0"]),
                    "token1": symbols.get(pool["token1"], pool["token1"]),
                    "fee": pool["fee"],
                    "pool_status": pool["pool_status"],
                    "status_reason": pool["status_reason"],
                    "stored_sqrt_price_x96": str(pool["sqrt_price_x96"]),
                    "live_sqrt_price_x96": str(state["sqrt_price_x96"]),
                    "liquidity_read_at": pool["liquidity_read_at"],
                    "liquidity_block_number": pool["liquidity_block_number"],
                }
            )

    payload = {
        "chainId": args.chain_id,
        "rpcUrl": rpc,
        "blockNumber": head,
        "poolCount": len(pools),
        "livePoolCount": len(live),
        "stored": summarise(stored_cycles),
        "live": summarise(live_cycles),
        "changedPricePoolCount": len(stale),
        "changedPricePools": stale[: args.top * 4],
        "notes": [
            "the stored group multiplies the stored reads, the live group multiplies the chain's current values",
            "the live group uses the chain's current lpFee, so a dynamic-fee pool can be priced at all",
            "a cycle is a spot product: it still has to survive price impact and the gates",
        ],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "blockNumber": head,
                "poolCount": len(pools),
                "livePoolCount": len(live),
                "storedProfitable": payload["stored"]["profitableCycleCount"],
                "storedAnchored": payload["stored"]["profitableAnchoredCycleCount"],
                "liveProfitable": payload["live"]["profitableCycleCount"],
                "liveAnchored": payload["live"]["profitableAnchoredCycleCount"],
                "changedPricePoolCount": len(stale),
            },
            ensure_ascii=False,
        )
    )
    for item in live_cycles[: args.top]:
        print(
            " ".join(item["path"]),
            item["edge_ratio_ppm"],
            "ppm",
            [hop["fee"] for hop in item["hops"]],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
