"""Read-only dynamic monitor for niche-token X-Y arbitrage cycles."""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from web3 import Web3

from intent_trade.chains.bnb import config as bnb
from intent_trade.chains.bnb.niche_static_table import _connect
from intent_trade.chains.bnb.quote import ROUTER_ABI, V3_QUOTER_ABI
from intent_trade.chains.bnb.scanner import V2_PAIR_ABI, V3_POOL_ABI


SRC_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = SRC_ROOT.parents[1]
DEFAULT_TABLE = REPO_ROOT / "data_info" / "BNB" / "bnb_niche_token_static_table.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data_info" / "BNB"
LATEST_NAME = "bnb_niche_xy_dynamic_monitor_latest.json"
HISTORY_NAME = "bnb_niche_xy_dynamic_monitor_history.jsonl"
SUMMARY_NAME = "bnb_niche_xy_dynamic_monitor_summary.md"
FINAL_NAME = "bnb_niche_xy_dynamic_monitor_final.json"

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _token_ref(token: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "address": str(token.get("address") or "").lower(),
        "decimals": int(token.get("decimals") or 18),
    }


def _amount_to_raw(amount: Decimal, token: dict[str, Any]) -> int:
    return int(amount * Decimal(10 ** int(token.get("decimals") or 18)))


def _raw_to_amount(raw: int, token: dict[str, Any]) -> Decimal:
    return Decimal(int(raw)) / Decimal(10 ** int(token.get("decimals") or 18))


def _fee_multiplier(hop: dict[str, Any]) -> Decimal:
    fee_text = str(hop.get("fee") or "0")
    digits = "".join(char for char in fee_text if char.isdigit())
    fee = Decimal(digits or "0")
    if hop.get("protocol") == "v3":
        return Decimal(1) - fee / Decimal(1_000_000)
    return Decimal(1) - fee / Decimal(10_000)


def _reverse_route(route: dict[str, Any]) -> list[dict[str, Any]]:
    reversed_hops = []
    for hop in reversed(list(route.get("hops") or [])):
        reversed_hops.append(
            {
                **hop,
                "tokenIn": _token_ref(hop["tokenOut"]),
                "tokenOut": _token_ref(hop["tokenIn"]),
            }
        )
    return reversed_hops


def _route_key(hops: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        f"{str(hop.get('pool') or '').lower()}:{hop.get('protocol')}:{hop.get('fee')}"
        for hop in hops
    )


def _select_bridge_routes(token: dict[str, Any], max_routes: int) -> list[dict[str, Any]]:
    routes = []
    seen: set[tuple[str, ...]] = set()
    for route in token.get("routesToStable") or token.get("routesToUsdc") or []:
        hops = list(route.get("hops") or [])
        key = _route_key(hops)
        if not hops or key in seen:
            continue
        seen.add(key)
        routes.append(route)
    routes.sort(key=lambda item: (int(item.get("hopCount") or 9), str(item.get("path"))))
    return routes[:max_routes]


def _stable_pair_key(left: str, right: str) -> str:
    return ":".join(sorted((left.upper(), right.upper())))


def _stable_pool_record(table: dict[str, Any], stable_from: str, stable_to: str) -> dict[str, Any] | None:
    key = _stable_pair_key(stable_from, stable_to)
    record = (table.get("stablePairPools") or {}).get(key)
    if record:
        return record
    legacy = table.get("stableToUsdcPools") or {}
    if stable_to == "USDC" and stable_from in legacy:
        return legacy[stable_from]
    if stable_from == "USDC" and stable_to in legacy:
        return legacy[stable_to]
    return None


def _stable_hops_from_pool(
    table: dict[str, Any],
    stable_from: str,
    stable_to: str,
    pool: dict[str, Any],
) -> dict[str, Any]:
    stable_tokens = table["stableAnchors"]
    return {
        "tokenIn": _token_ref(stable_tokens[stable_from]),
        "tokenOut": _token_ref(stable_tokens[stable_to]),
        "pool": pool["pool"],
        "dex": pool["dex"],
        "protocol": pool["protocol"],
        "fee": pool["fee"],
        "router": pool.get("router"),
        "quoter": pool.get("quoter"),
        "token0": _token_ref(pool["token0"]),
        "token1": _token_ref(pool["token1"]),
        "poolState": pool.get("poolState"),
    }


def _best_stable_settlement_hops(
    table: dict[str, Any],
    stable_from: str,
    stable_to: str,
) -> list[dict[str, Any]]:
    if stable_from == stable_to:
        return []
    record = _stable_pool_record(table, stable_from, stable_to)
    direct_pools = (record or {}).get("usablePools") or []
    if direct_pools:
        return [_stable_hops_from_pool(table, stable_from, stable_to, direct_pools[0])]
    for bridge_stable in table.get("stableAnchors") or {}:
        if bridge_stable in (stable_from, stable_to):
            continue
        first_record = _stable_pool_record(table, stable_from, bridge_stable)
        second_record = _stable_pool_record(table, bridge_stable, stable_to)
        first_pools = (first_record or {}).get("usablePools") or []
        second_pools = (second_record or {}).get("usablePools") or []
        if first_pools and second_pools:
            return [
                _stable_hops_from_pool(table, stable_from, bridge_stable, first_pools[0]),
                _stable_hops_from_pool(table, bridge_stable, stable_to, second_pools[0]),
            ]
    return []


def build_cycles(table: dict[str, Any], max_bridge_routes: int) -> list[dict[str, Any]]:
    token_by_address = {
        str(token["address"]).lower(): token
        for token in table.get("nicheTokens") or []
    }
    cycles: list[dict[str, Any]] = []
    source_pairs = (table.get("PN") or {}).get("pairs") or table.get("nicheDirectPairs") or []
    for pair in source_pairs:
        x = _token_ref(pair["x"])
        y = _token_ref(pair["y"])
        x_record = token_by_address.get(x["address"])
        y_record = token_by_address.get(y["address"])
        if not x_record or not y_record:
            continue
        x_routes = _select_bridge_routes(x_record, max_bridge_routes)
        y_routes = _select_bridge_routes(y_record, max_bridge_routes)
        direct_pools = [
            pool for pool in pair.get("usablePools") or []
            if pool.get("poolState") == "usable"
        ]
        for direct_pool in direct_pools:
            direct_hop_xy = {
                "tokenIn": x,
                "tokenOut": y,
                "pool": direct_pool["pool"],
                "dex": direct_pool["dex"],
                "protocol": direct_pool["protocol"],
                "fee": direct_pool["fee"],
                "router": direct_pool.get("router"),
                "quoter": direct_pool.get("quoter"),
                "token0": _token_ref(direct_pool["token0"]),
                "token1": _token_ref(direct_pool["token1"]),
                "poolState": direct_pool["poolState"],
            }
            direct_hop_yx = {**direct_hop_xy, "tokenIn": y, "tokenOut": x}
            for x_route in x_routes:
                for y_route in y_routes:
                    stable_x = str(x_route.get("stableSymbol") or x_route.get("path", [])[-1])
                    stable_y = str(y_route.get("stableSymbol") or y_route.get("path", [])[-1])
                    settle_y_to_x = _best_stable_settlement_hops(table, stable_y, stable_x)
                    settle_x_to_y = _best_stable_settlement_hops(table, stable_x, stable_y)
                    if stable_x != stable_y and not settle_y_to_x:
                        continue
                    cycles.append(
                        {
                            "pairName": pair.get("pairName") or f"{x['symbol']} / {y['symbol']}",
                            "direction": f"{x['symbol']}->{y['symbol']}",
                            "routeType": "STABLE_X_Y_STABLE",
                            "path": [stable_x, x["symbol"], y["symbol"], stable_y, stable_x],
                            "bridgeRouteX": x_route.get("path"),
                            "bridgeRouteY": y_route.get("path"),
                            "hops": _reverse_route(x_route) + [direct_hop_xy] + list(y_route["hops"]) + settle_y_to_x,
                        }
                    )
                    if stable_x != stable_y and not settle_x_to_y:
                        continue
                    cycles.append(
                        {
                            "pairName": pair.get("pairName") or f"{x['symbol']} / {y['symbol']}",
                            "direction": f"{y['symbol']}->{x['symbol']}",
                            "routeType": "STABLE_Y_X_STABLE",
                            "path": [stable_y, y["symbol"], x["symbol"], stable_x, stable_y],
                            "bridgeRouteX": x_route.get("path"),
                            "bridgeRouteY": y_route.get("path"),
                            "hops": _reverse_route(y_route) + [direct_hop_yx] + list(x_route["hops"]) + settle_x_to_y,
                        }
                    )
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    for cycle in cycles:
        key = (
            cycle["direction"],
            _route_key(cycle["hops"]),
        )
        unique[key] = cycle
    return list(unique.values())


def _unique_hops(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for cycle in cycles:
        for hop in cycle["hops"]:
            result[str(hop["pool"]).lower()] = hop
    return list(result.values())


def _fetch_states(w3: Web3, cycles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    hops = _unique_hops(cycles)
    calls = []
    metadata = []
    for hop in hops:
        address = Web3.to_checksum_address(hop["pool"])
        if hop["protocol"] == "v2":
            contract = w3.eth.contract(address=address, abi=V2_PAIR_ABI)
            calldata = contract.functions.getReserves()._encode_transaction_data()
            types = ["uint112", "uint112", "uint32"]
        else:
            contract = w3.eth.contract(address=address, abi=V3_POOL_ABI)
            calldata = contract.functions.slot0()._encode_transaction_data()
            types = ["uint160", "int24", "uint16", "uint16", "uint16", "uint32", "bool"]
        calls.append((address, True, bytes.fromhex(calldata[2:])))
        metadata.append((hop, types))
    multicall = w3.eth.contract(
        address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
        abi=MULTICALL3_ABI,
    )
    results = multicall.functions.aggregate3(calls).call()
    states: dict[str, dict[str, Any]] = {}
    for result, (hop, types) in zip(results, metadata):
        key = str(hop["pool"]).lower()
        if not result[0]:
            states[key] = {"ok": False, "error": "pool state subcall failed"}
            continue
        decoded = w3.codec.decode(types, result[1])
        if hop["protocol"] == "v2":
            states[key] = {
                "ok": True,
                "protocol": "v2",
                "reserve0Raw": int(decoded[0]),
                "reserve1Raw": int(decoded[1]),
                "blockTimestampLast": int(decoded[2]),
            }
        else:
            states[key] = {
                "ok": True,
                "protocol": "v3",
                "sqrtPriceX96": int(decoded[0]),
                "tick": int(decoded[1]),
                "unlocked": bool(decoded[6]),
            }
    return states


def _spot_hop(amount_in: Decimal, hop: dict[str, Any], state: dict[str, Any]) -> Decimal:
    amount_after_fee = amount_in * _fee_multiplier(hop)
    token_in = hop["tokenIn"]
    token_out = hop["tokenOut"]
    if hop["protocol"] == "v2":
        token0 = hop["token0"]
        token1 = hop["token1"]
        reserve0 = _raw_to_amount(state["reserve0Raw"], token0)
        reserve1 = _raw_to_amount(state["reserve1Raw"], token1)
        if token_in["address"] == token0["address"]:
            reserve_in, reserve_out = reserve0, reserve1
        else:
            reserve_in, reserve_out = reserve1, reserve0
        if reserve_in <= 0 or reserve_out <= 0:
            raise ValueError("empty V2 reserves")
        return amount_after_fee * reserve_out / (reserve_in + amount_after_fee)
    token0 = hop["token0"]
    token1 = hop["token1"]
    sqrt_price = Decimal(state["sqrtPriceX96"])
    raw_price = sqrt_price * sqrt_price / Decimal(2**192)
    human_price = raw_price * Decimal(10 ** int(token0["decimals"])) / Decimal(10 ** int(token1["decimals"]))
    if human_price <= 0 or not state.get("unlocked", True):
        raise ValueError("inactive V3 pool")
    if token_in["address"] == token0["address"]:
        return amount_after_fee * human_price
    return amount_after_fee / human_price


def _spot_cycle(cycle: dict[str, Any], states: dict[str, dict[str, Any]], initial: Decimal) -> dict[str, Any]:
    amount = initial
    hop_reports = []
    try:
        for hop in cycle["hops"]:
            state = states.get(str(hop["pool"]).lower())
            if not state or not state.get("ok"):
                raise ValueError((state or {}).get("error") or "missing pool state")
            amount_out = _spot_hop(amount, hop, state)
            hop_reports.append(
                {
                    "tokenIn": hop["tokenIn"]["symbol"],
                    "tokenOut": hop["tokenOut"]["symbol"],
                    "pool": hop["pool"],
                    "dex": hop["dex"],
                    "protocol": hop["protocol"],
                    "fee": hop["fee"],
                    "amountIn": format(amount, "f"),
                    "amountOut": format(amount_out, "f"),
                }
            )
            amount = amount_out
        profit = amount - initial
        return {
            **{key: cycle[key] for key in ("pairName", "direction", "routeType", "path", "bridgeRouteX", "bridgeRouteY")},
            "ok": True,
            "quoteMode": "spot_pool_state",
            "initialStableAmount": format(initial, "f"),
            "finalStableAmount": format(amount, "f"),
            "grossProfitStable": format(profit, "f"),
            "profitBps": format((amount / initial - 1) * 10000, "f"),
            "spotPotential": amount > initial,
            "hops": hop_reports,
        }
    except Exception as exc:
        return {
            **{key: cycle[key] for key in ("pairName", "direction", "routeType", "path", "bridgeRouteX", "bridgeRouteY")},
            "ok": False,
            "quoteMode": "spot_pool_state",
            "error": str(exc),
        }


def _exact_hop(w3: Web3, hop: dict[str, Any], amount_raw: int) -> int:
    token_in = Web3.to_checksum_address(hop["tokenIn"]["address"])
    token_out = Web3.to_checksum_address(hop["tokenOut"]["address"])
    if hop["protocol"] == "v2":
        router = w3.eth.contract(address=Web3.to_checksum_address(hop["router"]), abi=ROUTER_ABI)
        amounts = router.functions.getAmountsOut(amount_raw, [token_in, token_out]).call()
        return int(amounts[-1])
    quoter = w3.eth.contract(address=Web3.to_checksum_address(hop["quoter"]), abi=V3_QUOTER_ABI)
    result = quoter.functions.quoteExactInputSingle(
        {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amountIn": amount_raw,
            "fee": int(hop["fee"]),
            "sqrtPriceLimitX96": 0,
        }
    ).call()
    return int(result[0])


def _exact_cycle(w3: Web3, cycle: dict[str, Any], initial: Decimal, block_number: int) -> dict[str, Any]:
    amount = initial
    amount_raw = _amount_to_raw(amount, cycle["hops"][0]["tokenIn"])
    try:
        for hop in cycle["hops"]:
            amount_raw = _exact_hop(w3, hop, amount_raw)
            amount = _raw_to_amount(amount_raw, hop["tokenOut"])
        profit = amount - initial
        return {
            "quoteMode": "exact_router_or_quoter",
            "blockNumber": block_number,
            "initialStableAmount": format(initial, "f"),
            "finalStableAmount": format(amount, "f"),
            "grossProfitStable": format(profit, "f"),
            "profitBps": format((amount / initial - 1) * 10000, "f"),
            "profitableBeforeCosts": amount > initial,
        }
    except Exception as exc:
        return {"quoteMode": "exact_router_or_quoter", "blockNumber": block_number, "error": str(exc)}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _summary(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# BNB Niche X-Y Dynamic Monitor",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- RPC: `{payload['rpcUrl']}`",
        f"- Static table block: `{payload.get('staticTableBlockNumber')}`",
        f"- X-Y cycles: `{payload['cycleCount']}`, pools read: `{payload['poolCount']}`",
        f"- Spot potential cycles: `{payload['spotPotentialCount']}`, exact positive cycles: `{payload['exactPositiveCount']}`",
        f"- Failed spot cycles: `{payload['failedSpotCount']}`",
        "",
        "| Pair | Direction | Spot Profit bps | Exact Profit bps | Exact Status |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in payload.get("bestReports") or []:
        exact = row.get("exact") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("pairName")),
                    str(row.get("direction")),
                    str(row.get("profitBps")),
                    str(exact.get("profitBps") or "-"),
                    str("positive" if exact.get("profitableBeforeCosts") else "not-positive"),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_round(
    w3: Web3,
    cycles: list[dict[str, Any]],
    *,
    initial_stable: Decimal,
    exact_on_spot_potential: bool,
    max_exact_workers: int,
    static_table_block_number: int | None,
    rpc_url: str,
    run_id: str,
    round_index: int,
) -> dict[str, Any]:
    started = time.monotonic()
    block_number = int(w3.eth.block_number)
    states = _fetch_states(w3, cycles)
    reports = [_spot_cycle(cycle, states, initial_stable) for cycle in cycles]
    reports.sort(key=lambda item: float(_decimal(item.get("profitBps")) or Decimal("-Infinity")), reverse=True)
    potential = [report for report in reports if report.get("ok") and report.get("spotPotential")]
    if exact_on_spot_potential and potential:
        cycle_by_path = {
            (cycle["direction"], _route_key(cycle["hops"])): cycle
            for cycle in cycles
        }
        with ThreadPoolExecutor(max_workers=max_exact_workers) as executor:
            futures = {}
            for report in potential:
                cycle = next(
                    cycle
                    for cycle in cycles
                    if cycle["direction"] == report["direction"]
                    and [*cycle["path"]] == [*report["path"]]
                    and _route_key(cycle["hops"]) == _route_key(
                        [
                            {
                                "pool": hop["pool"],
                                "protocol": hop["protocol"],
                                "fee": hop["fee"],
                            }
                            for hop in report["hops"]
                        ]
                    )
                )
                futures[executor.submit(_exact_cycle, w3, cycle, initial_stable, block_number)] = report
            for future in as_completed(futures):
                futures[future]["exact"] = future.result()
    exact_positive = [
        report for report in reports
        if (report.get("exact") or {}).get("profitableBeforeCosts")
    ]
    return {
        "runId": run_id,
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "mode": "niche_xy_only_dynamic_shadow",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "quoteMode": "spot_with_exact_on_potential",
        "rpcUrl": rpc_url,
        "staticTableBlockNumber": static_table_block_number,
        "roundIndex": round_index,
        "generatedAt": _utc_now(),
        "roundElapsedMs": int((time.monotonic() - started) * 1000),
        "cycleCount": len(cycles),
        "poolCount": len(states),
        "spotPotentialCount": len(potential),
        "exactPositiveCount": len(exact_positive),
        "failedSpotCount": sum(1 for report in reports if not report.get("ok")),
        "bestReports": reports[:20],
        "reports": reports,
    }


def monitor(args: argparse.Namespace) -> dict[str, Any]:
    table = json.loads(args.static_table.read_text(encoding="utf-8"))
    cycles = build_cycles(table, args.max_bridge_routes)
    if not cycles:
        raise RuntimeError("no niche X-Y cycles found in static table")
    w3, rpc_url = _connect(args.rpc_url or list(bnb.DEFAULT_RPC_URLS))
    initial_stable = Decimal(str(args.initial_stable))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = _utc_now()
    deadline = time.monotonic() + float(args.duration_seconds)
    round_index = 0
    next_round = time.monotonic()
    latest: dict[str, Any] = {}
    print(
        json.dumps(
            {
                "event": "niche_xy_monitor_started",
                "runId": run_id,
                "rpcUrl": rpc_url,
                "cycleCount": len(cycles),
                "durationSeconds": args.duration_seconds,
                "maxBridgeRoutes": args.max_bridge_routes,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    while time.monotonic() < deadline:
        round_index += 1
        latest = run_round(
            w3,
            cycles,
            initial_stable=initial_stable,
            exact_on_spot_potential=args.exact_on_spot_potential,
            max_exact_workers=args.max_exact_workers,
            static_table_block_number=table.get("blockNumber"),
            rpc_url=rpc_url,
            run_id=run_id,
            round_index=round_index,
        )
        _atomic_json(args.output_dir / LATEST_NAME, latest)
        _append_jsonl(args.output_dir / HISTORY_NAME, latest)
        _summary(args.output_dir / SUMMARY_NAME, latest)
        print(
            json.dumps(
                {
                    "event": "round",
                    "roundIndex": round_index,
                    "cycleCount": latest["cycleCount"],
                    "spotPotential": latest["spotPotentialCount"],
                    "exactPositive": latest["exactPositiveCount"],
                    "failed": latest["failedSpotCount"],
                    "elapsedMs": latest["roundElapsedMs"],
                    "generatedAt": latest["generatedAt"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        next_round += float(args.interval_seconds)
        sleep_for = next_round - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
    latest = {
        **latest,
        "startedAt": started_at,
        "finishedAt": _utc_now(),
        "completedRounds": round_index,
    }
    _atomic_json(args.output_dir / FINAL_NAME, latest)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor only niche-token X-Y arbitrage cycles on BNB.")
    parser.add_argument("--duration-seconds", type=float, default=1200.0)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--static-table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--initial-stable", default="1000")
    parser.add_argument("--initial-usdc", dest="initial_stable")
    parser.add_argument("--max-bridge-routes", type=int, default=3)
    parser.add_argument("--max-exact-workers", type=int, default=4)
    parser.add_argument("--exact-on-spot-potential", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    final = monitor(args)
    print(
        json.dumps(
            {
                "ok": True,
                "completedRounds": final.get("completedRounds"),
                "latest": str(args.output_dir / LATEST_NAME),
                "history": str(args.output_dir / HISTORY_NAME),
                "summary": str(args.output_dir / SUMMARY_NAME),
                "final": str(args.output_dir / FINAL_NAME),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
