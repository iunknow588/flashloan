"""Dynamic BNB Chain triangular arbitrage monitor.

The monitor is read-only: it calls public routers/quoters, never signs, and
never broadcasts. It discovers route candidates from the BNB static pool table
and writes one compact observation snapshot per polling round.
"""

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

from intent_trade.chains.bnb import config as bnb
from intent_trade.chains.bnb.intent import STATIC_TABLE_PATH, load_static_table
from intent_trade.chains.bnb.quote import _connect, quote_route
from intent_trade.chains.bnb.scanner import V2_PAIR_ABI, V3_POOL_ABI


SRC_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = SRC_ROOT.parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data_info" / "BNB"
LATEST_NAME = "bnb_dynamic_arbitrage_monitor_latest.json"
HISTORY_NAME = "bnb_dynamic_arbitrage_monitor_history.jsonl"
SUMMARY_NAME = "bnb_dynamic_arbitrage_monitor_summary.md"
FINAL_NAME = "bnb_dynamic_arbitrage_monitor_final.json"
PREFLIGHT_REJECTED_NAME = "bnb_dynamic_arbitrage_monitor_preflight_rejected.json"
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

DEFAULT_AMOUNTS: dict[str, str] = {
    "USDT": "1000",
    "USDC": "1000",
    "BUSD": "1000",
    "DAI": "1000",
    "WBNB": "1",
    "ETH": "1",
    "BTCB": "0.02",
    "CAKE": "100",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_path(path: str) -> tuple[str, str, str, str]:
    symbols = tuple(part.strip().upper() for part in str(path).split("->") if part.strip())
    if len(symbols) != 4 or symbols[0] != symbols[-1]:
        raise ValueError(f"invalid triangular route path: {path!r}")
    return symbols  # type: ignore[return-value]


def _rotate(route: tuple[str, str, str, str], start: str) -> tuple[str, str, str, str]:
    body = list(route[:3])
    index = body.index(start)
    rotated = body[index:] + body[:index]
    return (rotated[0], rotated[1], rotated[2], rotated[0])


def discover_routes(
    *,
    static_table_path: Path,
    allowed_statuses: set[str],
    rotations: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table = load_static_table(static_table_path)
    pools = list(table.get("pools") or [])
    best_edges = bnb.best_edges_by_pair(pools, allowed_statuses)
    triangles = bnb.triangle_routes(best_edges)
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for triangle in triangles:
        forward = _parse_path(str(triangle["path"]))
        reverse = (forward[0], forward[2], forward[1], forward[0])
        candidates: list[tuple[str, str, str, str]] = []
        if rotations == "all":
            for base in (forward, reverse):
                for start in base[:3]:
                    candidates.append(_rotate(base, start))
        else:
            candidates.extend([forward, reverse])
        for route in candidates:
            if route in seen:
                continue
            seen.add(route)
            routes.append(
                {
                    "routeSymbols": list(route),
                    "path": " -> ".join(route),
                    "trianglePath": triangle["path"],
                    "triangleStatus": triangle["status"],
                    "minTvlUsd": triangle["minTvlUsd"],
                    "edgePools": [
                        {
                            "pool": edge.get("pool"),
                            "symbolPair": edge.get("symbolPair"),
                            "dex": edge.get("dex"),
                            "protocol": edge.get("protocol"),
                            "fee": edge.get("fee"),
                            "status": edge.get("status"),
                            "tvlUsd": edge.get("tvlUsd"),
                        }
                        for edge in triangle["edges"]
                    ],
                    "routeHops": [
                        {
                            **best_edges[tuple(sorted((route[index], route[index + 1])))],
                            "tokenIn": route[index],
                            "tokenOut": route[index + 1],
                        }
                        for index in range(3)
                    ],
                }
            )
    routes.sort(key=lambda item: (-float(item.get("minTvlUsd") or 0), item["path"]))
    return table, routes


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _profit_bps(initial: Any, final: Any) -> str | None:
    initial_value = _decimal(initial)
    final_value = _decimal(final)
    if initial_value is None or final_value is None or initial_value <= 0:
        return None
    return format((((final_value / initial_value) - Decimal(1)) * Decimal(10000)).normalize(), "f")


def _compact_quote(route: dict[str, Any], amount: str, report: dict[str, Any]) -> dict[str, Any]:
    hops = []
    for hop in report.get("hops") or []:
        hops.append(
            {
                "tokenIn": hop.get("tokenIn"),
                "tokenOut": hop.get("tokenOut"),
                "dex": hop.get("dex"),
                "protocol": hop.get("protocol"),
                "pool": hop.get("pool"),
                "fee": hop.get("fee"),
                "quality": hop.get("quality"),
                "tvlUsd": hop.get("tvlUsd"),
                "amountIn": hop.get("amountIn"),
                "amountOut": hop.get("amountOut"),
                "quoteMethod": hop.get("quoteMethod"),
            }
        )
    return {
        "ok": True,
        "path": route["path"],
        "routeSymbols": route["routeSymbols"],
        "testAmount": amount,
        "quotedAt": report.get("quotedAt"),
        "blockNumber": report.get("blockNumber"),
        "initialAmount": report.get("initialAmount"),
        "finalAmount": report.get("finalAmount"),
        "grossProfit": report.get("grossProfit"),
        "profitBps": _profit_bps(report.get("initialAmount"), report.get("finalAmount")),
        "profitableBeforeFlashloanAndGas": bool(report.get("profitableBeforeFlashloanAndGas")),
        "costStatus": report.get("costStatus"),
        "triangleStatus": route.get("triangleStatus"),
        "minTvlUsd": route.get("minTvlUsd"),
        "hops": hops,
    }


def _quote_one(
    w3: Any,
    route: dict[str, Any],
    *,
    amount_override: str | None,
    static_table_path: Path,
    allowed_statuses: set[str],
) -> dict[str, Any]:
    symbols = tuple(str(symbol) for symbol in route["routeSymbols"])
    amount = amount_override or DEFAULT_AMOUNTS.get(symbols[0], "1")
    try:
        report = quote_route(
            w3,
            symbols,  # type: ignore[arg-type]
            amount=amount,
            static_table_path=static_table_path,
            allowed_statuses=allowed_statuses,
        )
        return _compact_quote(route, amount, report)
    except Exception as exc:
        return {
            "ok": False,
            "path": route["path"],
            "routeSymbols": route["routeSymbols"],
            "testAmount": amount,
            "triangleStatus": route.get("triangleStatus"),
            "minTvlUsd": route.get("minTvlUsd"),
            "error": str(exc),
        }


def _amount_from_units(value: int, symbol: str) -> Decimal:
    return Decimal(int(value)) / Decimal(10 ** bnb.TOKENS[symbol].decimals)


def _amount_to_units(value: Decimal, symbol: str) -> str:
    return format(value.normalize(), "f")


def _parse_fee_number(value: Any) -> Decimal:
    text = str(value or "0")
    digits = "".join(ch for ch in text if ch.isdigit())
    return Decimal(digits or "0")


def _fee_multiplier(edge: dict[str, Any]) -> Decimal:
    protocol = str(edge.get("protocol") or "")
    fee = _parse_fee_number(edge.get("fee"))
    if protocol == "v3":
        return Decimal(1) - (fee / Decimal(1_000_000))
    if protocol == "v2":
        return Decimal(1) - (fee / Decimal(10_000))
    return Decimal(1)


def _fetch_pool_state(w3: Any, edge: dict[str, Any]) -> dict[str, Any]:
    protocol = str(edge.get("protocol") or "")
    pool_address = str(edge.get("pool") or "")
    token0 = str(edge.get("token0Symbol") or "")
    token1 = str(edge.get("token1Symbol") or "")
    if protocol == "v2":
        pair = w3.eth.contract(address=w3.to_checksum_address(pool_address), abi=V2_PAIR_ABI)
        reserve0_raw, reserve1_raw, block_timestamp_last = pair.functions.getReserves().call()
        reserve0 = _amount_from_units(int(reserve0_raw), token0)
        reserve1 = _amount_from_units(int(reserve1_raw), token1)
        return {
            "ok": True,
            "pool": pool_address,
            "protocol": protocol,
            "token0Symbol": token0,
            "token1Symbol": token1,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "blockTimestampLast": int(block_timestamp_last),
        }
    if protocol == "v3":
        pool = w3.eth.contract(address=w3.to_checksum_address(pool_address), abi=V3_POOL_ABI)
        slot0 = pool.functions.slot0().call()
        sqrt_price_x96 = Decimal(int(slot0[0]))
        price = (sqrt_price_x96 * sqrt_price_x96) / Decimal(2**192)
        return {
            "ok": True,
            "pool": pool_address,
            "protocol": protocol,
            "token0Symbol": token0,
            "token1Symbol": token1,
            "sqrtPriceX96": str(slot0[0]),
            "tick": int(slot0[1]),
            "spotToken1PerToken0": price,
        }
    raise ValueError(f"unsupported pool protocol for spot mode: {protocol}")


def _unique_route_edges(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for route in routes:
        for edge in route.get("routeHops") or []:
            pool = str(edge.get("pool") or "")
            if pool:
                selected[pool.lower()] = edge
    return list(selected.values())


def _fetch_pool_states_multicall(w3: Any, routes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    edges = _unique_route_edges(routes)
    multicall = w3.eth.contract(address=w3.to_checksum_address(MULTICALL3_ADDRESS), abi=MULTICALL3_ABI)
    calls = []
    metadata = []
    for edge in edges:
        protocol = str(edge.get("protocol") or "")
        pool_address = str(edge.get("pool") or "")
        checksum_pool = w3.to_checksum_address(pool_address)
        if protocol == "v2":
            pair = w3.eth.contract(address=checksum_pool, abi=V2_PAIR_ABI)
            calldata = pair.functions.getReserves()._encode_transaction_data()
            return_types = ["uint112", "uint112", "uint32"]
        elif protocol == "v3":
            pool = w3.eth.contract(address=checksum_pool, abi=V3_POOL_ABI)
            calldata = pool.functions.slot0()._encode_transaction_data()
            return_types = ["uint160", "int24", "uint16", "uint16", "uint16", "uint32", "bool"]
        else:
            continue
        calls.append((checksum_pool, True, bytes.fromhex(calldata[2:])))
        metadata.append((edge, return_types))
    results = multicall.functions.aggregate3(calls).call()
    states: dict[str, dict[str, Any]] = {}
    for (success, return_data), (edge, return_types) in zip(results, metadata):
        pool_address = str(edge.get("pool") or "")
        key = pool_address.lower()
        if not success:
            states[key] = {"ok": False, "pool": pool_address, "error": "multicall subcall failed"}
            continue
        try:
            decoded = w3.codec.decode(return_types, return_data)
            protocol = str(edge.get("protocol") or "")
            token0 = str(edge.get("token0Symbol") or "")
            token1 = str(edge.get("token1Symbol") or "")
            if protocol == "v2":
                reserve0 = _amount_from_units(int(decoded[0]), token0)
                reserve1 = _amount_from_units(int(decoded[1]), token1)
                states[key] = {
                    "ok": True,
                    "pool": pool_address,
                    "protocol": protocol,
                    "token0Symbol": token0,
                    "token1Symbol": token1,
                    "reserve0": reserve0,
                    "reserve1": reserve1,
                    "blockTimestampLast": int(decoded[2]),
                }
            elif protocol == "v3":
                sqrt_price_x96 = Decimal(int(decoded[0]))
                price = (sqrt_price_x96 * sqrt_price_x96) / Decimal(2**192)
                states[key] = {
                    "ok": True,
                    "pool": pool_address,
                    "protocol": protocol,
                    "token0Symbol": token0,
                    "token1Symbol": token1,
                    "sqrtPriceX96": str(decoded[0]),
                    "tick": int(decoded[1]),
                    "spotToken1PerToken0": price,
                }
        except Exception as exc:
            states[key] = {"ok": False, "pool": pool_address, "error": str(exc)}
    return states


def _fetch_pool_states(w3: Any, routes: list[dict[str, Any]], max_workers: int) -> dict[str, dict[str, Any]]:
    try:
        return _fetch_pool_states_multicall(w3, routes)
    except Exception:
        pass
    states: dict[str, dict[str, Any]] = {}
    edges = _unique_route_edges(routes)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_pool_state, w3, edge): edge for edge in edges}
        for future in as_completed(futures):
            edge = futures[future]
            pool = str(edge.get("pool") or "")
            try:
                states[pool.lower()] = future.result()
            except Exception as exc:
                states[pool.lower()] = {"ok": False, "pool": pool, "error": str(exc)}
    return states


def _spot_hop_amount(amount_in: Decimal, edge: dict[str, Any], state: dict[str, Any]) -> Decimal:
    amount_after_fee = amount_in * _fee_multiplier(edge)
    token_in = str(edge.get("tokenIn") or "")
    token0 = str(state.get("token0Symbol") or "")
    token1 = str(state.get("token1Symbol") or "")
    protocol = str(edge.get("protocol") or "")
    if protocol == "v2":
        reserve0 = state.get("reserve0")
        reserve1 = state.get("reserve1")
        if not isinstance(reserve0, Decimal) or not isinstance(reserve1, Decimal):
            raise ValueError("missing v2 reserves")
        if token_in == token0:
            reserve_in, reserve_out = reserve0, reserve1
        elif token_in == token1:
            reserve_in, reserve_out = reserve1, reserve0
        else:
            raise ValueError(f"{token_in} is not in pool {state.get('pool')}")
        if reserve_in <= 0 or reserve_out <= 0:
            raise ValueError("empty v2 reserves")
        return (amount_after_fee * reserve_out) / (reserve_in + amount_after_fee)
    if protocol == "v3":
        price = state.get("spotToken1PerToken0")
        if not isinstance(price, Decimal) or price <= 0:
            raise ValueError("missing v3 spot price")
        if token_in == token0:
            return amount_after_fee * price
        if token_in == token1:
            return amount_after_fee / price
        raise ValueError(f"{token_in} is not in pool {state.get('pool')}")
    raise ValueError(f"unsupported protocol: {protocol}")


def _spot_quote_one(
    route: dict[str, Any],
    *,
    states: dict[str, dict[str, Any]],
    amount_override: str | None,
    block_number: int,
) -> dict[str, Any]:
    symbols = [str(symbol) for symbol in route["routeSymbols"]]
    amount_text = amount_override or DEFAULT_AMOUNTS.get(symbols[0], "1")
    amount = _decimal(amount_text)
    if amount is None or amount <= 0:
        return {"ok": False, "path": route["path"], "routeSymbols": symbols, "testAmount": amount_text, "error": "invalid test amount"}
    hops = []
    amount_in = amount
    try:
        for edge in route.get("routeHops") or []:
            state = states.get(str(edge.get("pool") or "").lower())
            if not state or not state.get("ok"):
                raise ValueError(str((state or {}).get("error") or "missing pool state"))
            hop_in = amount_in
            amount_out = _spot_hop_amount(hop_in, edge, state)
            hops.append(
                {
                    "tokenIn": edge.get("tokenIn"),
                    "tokenOut": edge.get("tokenOut"),
                    "dex": edge.get("dex"),
                    "protocol": edge.get("protocol"),
                    "pool": edge.get("pool"),
                    "fee": edge.get("fee"),
                    "quality": edge.get("status"),
                    "tvlUsd": edge.get("tvlUsd"),
                    "amountIn": _amount_to_units(hop_in, str(edge.get("tokenIn"))),
                    "amountOut": _amount_to_units(amount_out, str(edge.get("tokenOut"))),
                    "quoteMethod": "spot_from_pool_state",
                }
            )
            amount_in = amount_out
        final_amount = amount_in
        gross_profit = final_amount - amount
        return {
            "ok": True,
            "path": route["path"],
            "routeSymbols": symbols,
            "testAmount": amount_text,
            "quotedAt": _utc_now(),
            "blockNumber": block_number,
            "initialAmount": _amount_to_units(amount, symbols[0]),
            "finalAmount": _amount_to_units(final_amount, symbols[0]),
            "grossProfit": _amount_to_units(gross_profit, symbols[0]),
            "profitBps": _profit_bps(amount, final_amount),
            "profitableBeforeFlashloanAndGas": final_amount > amount,
            "costStatus": "spot_quote_flashloan_and_gas_unverified",
            "triangleStatus": route.get("triangleStatus"),
            "minTvlUsd": route.get("minTvlUsd"),
            "hops": hops,
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": route["path"],
            "routeSymbols": symbols,
            "testAmount": amount_text,
            "triangleStatus": route.get("triangleStatus"),
            "minTvlUsd": route.get("minTvlUsd"),
            "error": str(exc),
        }


def _sort_key(report: dict[str, Any]) -> tuple[int, float]:
    value = _decimal(report.get("profitBps"))
    return (0 if report.get("ok") else 1, -float(value or Decimal("-Infinity")))


def _return_ratio(report: dict[str, Any]) -> Decimal | None:
    initial = _decimal(report.get("initialAmount"))
    final = _decimal(report.get("finalAmount"))
    if initial is None or final is None or initial <= 0:
        return None
    return final / initial


def preflight_exact_routes(
    w3: Any,
    routes: list[dict[str, Any]],
    *,
    amount_override: str | None,
    static_table_path: Path,
    allowed_statuses: set[str],
    min_return_ratio: Decimal,
    max_workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _quote_one,
                w3,
                route,
                amount_override=amount_override,
                static_table_path=static_table_path,
                allowed_statuses=allowed_statuses,
            ): route
            for route in routes
        }
        for future in as_completed(futures):
            route = futures[future]
            report = future.result()
            ratio = _return_ratio(report)
            route_with_preflight = {
                **route,
                "preflightExact": {
                    "ok": bool(report.get("ok")),
                    "initialAmount": report.get("initialAmount"),
                    "finalAmount": report.get("finalAmount"),
                    "grossProfit": report.get("grossProfit"),
                    "returnRatio": format(ratio.normalize(), "f") if ratio is not None else None,
                    "error": report.get("error"),
                    "blockNumber": report.get("blockNumber"),
                },
            }
            if report.get("ok") and ratio is not None and ratio >= min_return_ratio:
                accepted.append(route_with_preflight)
            else:
                rejected.append(
                    {
                        **route_with_preflight,
                        "rejectReason": "exact_preflight_return_below_threshold"
                        if report.get("ok")
                        else "exact_preflight_quote_failed",
                        "minReturnRatio": format(min_return_ratio.normalize(), "f"),
                    }
                )
    accepted.sort(key=lambda item: (-float(item.get("minTvlUsd") or 0), item["path"]))
    rejected.sort(key=lambda item: item["path"])
    return accepted, rejected


def run_round(
    w3: Any,
    *,
    run_id: str,
    round_index: int,
    started_at: str,
    rpc_url: str,
    routes: list[dict[str, Any]],
    static_table_path: Path,
    static_table_generated_at: Any,
    amount_override: str | None,
    allowed_statuses: set[str],
    max_workers: int,
    quote_mode: str,
) -> dict[str, Any]:
    round_started = _utc_now()
    monotonic_started = time.monotonic()
    reports: list[dict[str, Any]] = []
    block_number = int(w3.eth.block_number)
    if quote_mode == "spot":
        states = _fetch_pool_states(w3, routes, max_workers=max_workers)
        reports = [
            _spot_quote_one(
                route,
                states=states,
                amount_override=amount_override,
                block_number=block_number,
            )
            for route in routes
        ]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _quote_one,
                    w3,
                    route,
                    amount_override=amount_override,
                    static_table_path=static_table_path,
                    allowed_statuses=allowed_statuses,
                )
                for route in routes
            ]
            for future in as_completed(futures):
                reports.append(future.result())
    reports.sort(key=_sort_key)
    successful = [report for report in reports if report.get("ok")]
    profitable = [report for report in successful if report.get("profitableBeforeFlashloanAndGas")]
    best = successful[:10]
    elapsed_ms = int((time.monotonic() - monotonic_started) * 1000)
    return {
        "runId": run_id,
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "mode": "dynamic_shadow_quote_only",
        "quoteMode": quote_mode,
        "executionEnabled": False,
        "broadcastEnabled": False,
        "startedAt": started_at,
        "roundStartedAt": round_started,
        "generatedAt": _utc_now(),
        "roundIndex": round_index,
        "roundElapsedMs": elapsed_ms,
        "rpcUrl": rpc_url,
        "staticTablePath": str(static_table_path),
        "staticTableGeneratedAt": static_table_generated_at,
        "allowedStatuses": sorted(allowed_statuses),
        "routeCount": len(routes),
        "poolStateCount": len(_unique_route_edges(routes)),
        "successfulQuoteCount": len(successful),
        "failedQuoteCount": len(reports) - len(successful),
        "profitableBeforeCostsCount": len(profitable),
        "bestReports": best,
        "reports": reports,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _fmt(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(number):
        return "-"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


def _write_summary(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# BNB Dynamic Arbitrage Monitor",
        "",
        f"- Run: `{payload['runId']}`",
        f"- Latest round: `{payload['roundIndex']}` at `{payload['generatedAt']}`",
        f"- RPC: `{payload['rpcUrl']}`",
        f"- Static table: `{payload['staticTableGeneratedAt']}`",
        f"- Quote mode: `{payload.get('quoteMode')}`",
        f"- Exact preflight rejected routes: `{payload.get('preflightRejectedRouteCount')}`",
        f"- Routes: `{payload['routeCount']}`, pools read: `{payload.get('poolStateCount')}`, quoted ok: `{payload['successfulQuoteCount']}`, failed: `{payload['failedQuoteCount']}`",
        f"- Profitable before flashloan/gas: `{payload['profitableBeforeCostsCount']}`",
        f"- Round elapsed: `{payload['roundElapsedMs']} ms`",
        "",
        "## Best Live Routes",
        "",
        "| Path | Amount | Final | Gross | Profit bps | Block |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in payload.get("bestReports") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(report.get("path") or "-"),
                    _fmt(report.get("initialAmount")),
                    _fmt(report.get("finalAmount")),
                    _fmt(report.get("grossProfit")),
                    _fmt(report.get("profitBps")),
                    str(report.get("blockNumber") or "-"),
                ]
            )
            + " |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def monitor(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    allowed_statuses = {str(status).strip() for status in args.status if str(status).strip()}
    table, routes = discover_routes(
        static_table_path=args.static_table,
        allowed_statuses=allowed_statuses,
        rotations=args.rotations,
    )
    if not routes:
        raise RuntimeError("no BNB triangular routes discovered from static table")
    w3, rpc_url = _connect(args.rpc_url or list(bnb.DEFAULT_RPC_URLS))
    rejected_routes: list[dict[str, Any]] = []
    if args.preflight_exact:
        min_return_ratio = Decimal(str(args.min_preflight_return_ratio))
        routes, rejected_routes = preflight_exact_routes(
            w3,
            routes,
            amount_override=args.amount,
            static_table_path=args.static_table,
            allowed_statuses=allowed_statuses,
            min_return_ratio=min_return_ratio,
            max_workers=max(1, int(args.max_workers)),
        )
        _atomic_write_json(
            output_dir / PREFLIGHT_REJECTED_NAME,
            {
                "generatedAt": _utc_now(),
                "rpcUrl": rpc_url,
                "staticTablePath": str(args.static_table),
                "allowedStatuses": sorted(allowed_statuses),
                "minPreflightReturnRatio": format(min_return_ratio.normalize(), "f"),
                "acceptedRouteCount": len(routes),
                "rejectedRouteCount": len(rejected_routes),
                "rejectedRoutes": rejected_routes,
            },
        )
        if not routes:
            raise RuntimeError("all BNB triangular routes were rejected by exact preflight")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = _utc_now()
    deadline = time.monotonic() + float(args.duration_seconds)
    latest_payload: dict[str, Any] = {}
    round_index = 0
    next_round_at = time.monotonic()
    print(
        json.dumps(
            {
                "event": "monitor_started",
                "runId": run_id,
                "routeCount": len(routes),
                "preflightExact": bool(args.preflight_exact),
                "preflightRejectedRouteCount": len(rejected_routes),
                "durationSeconds": args.duration_seconds,
                "intervalSeconds": args.interval_seconds,
                "rpcUrl": rpc_url,
                "outputDir": str(output_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    while time.monotonic() < deadline:
        round_index += 1
        latest_payload = run_round(
            w3,
            run_id=run_id,
            round_index=round_index,
            started_at=started_at,
            rpc_url=rpc_url,
            routes=routes,
            static_table_path=args.static_table,
            static_table_generated_at=table.get("generatedAt"),
            amount_override=args.amount,
            allowed_statuses=allowed_statuses,
            max_workers=max(1, int(args.max_workers)),
            quote_mode=args.quote_mode,
        )
        latest_payload["preflightExact"] = bool(args.preflight_exact)
        latest_payload["preflightRejectedRouteCount"] = len(rejected_routes)
        _atomic_write_json(output_dir / LATEST_NAME, latest_payload)
        _append_jsonl(output_dir / HISTORY_NAME, latest_payload)
        _write_summary(output_dir / SUMMARY_NAME, latest_payload)
        print(
            json.dumps(
                {
                    "event": "round",
                    "roundIndex": round_index,
                    "routeCount": latest_payload["routeCount"],
                    "poolStateCount": latest_payload.get("poolStateCount"),
                    "quoteMode": latest_payload.get("quoteMode"),
                    "ok": latest_payload["successfulQuoteCount"],
                    "profitableBeforeCosts": latest_payload["profitableBeforeCostsCount"],
                    "elapsedMs": latest_payload["roundElapsedMs"],
                    "generatedAt": latest_payload["generatedAt"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        next_round_at += float(args.interval_seconds)
        sleep_for = next_round_at - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
    final_payload = {**latest_payload, "finishedAt": _utc_now(), "completedRounds": round_index}
    _atomic_write_json(output_dir / FINAL_NAME, final_payload)
    return final_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor BNB Chain triangular arbitrage routes every second.")
    parser.add_argument("--duration-seconds", type=float, default=1200.0)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--static-table", type=Path, default=STATIC_TABLE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--amount", default=None, help="Override test amount for every route; defaults are per start token.")
    parser.add_argument("--status", action="append", default=["recommended"])
    parser.add_argument("--rotations", choices=["canonical", "all"], default="all")
    parser.add_argument("--quote-mode", choices=["spot", "exact"], default="spot")
    parser.add_argument("--preflight-exact", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-preflight-return-ratio", default="0.95")
    parser.add_argument("--max-workers", type=int, default=12)
    args = parser.parse_args()
    final_payload = monitor(args)
    print(
        json.dumps(
            {
                "ok": True,
                "completedRounds": final_payload.get("completedRounds"),
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
