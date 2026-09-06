"""Build a static BNB lookup table for niche-token direct and stable bridge pools."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from web3 import Web3

from dex.intent_trade.chains.bnb import config as bnb
from dex.intent_trade.chains.bnb.quote import ROUTER_ABI, V3_QUOTER_ABI
from dex.intent_trade.chains.bnb.scanner import V2_FACTORY_ABI, V2_PAIR_ABI, V3_FACTORY_ABI, V3_POOL_ABI


SRC_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = SRC_ROOT.parents[1]
DEFAULT_CANDIDATES = REPO_ROOT / "data_info" / "BNB" / "bnb_niche_xy_direct_pool_candidates.json"
DEFAULT_OUTPUT = REPO_ROOT / "data_info" / "BNB" / "bnb_niche_token_static_table.json"
ANCHOR_STABLES = ("USDT", "USDC", "BUSD", "DAI")
DEFAULT_MIN_SIDE_UNITS = Decimal("100")
DEFAULT_MIN_V2_RESERVE_IN_UNITS = Decimal("50000")
DEFAULT_MIN_V3_ACTIVE_LIQUIDITY = 10**18
DEFAULT_BRIDGE_PREFLIGHT_STABLE = Decimal("1000")
DEFAULT_MIN_BRIDGE_RETURN_RATIO = Decimal("0.90")
ERC20_METADATA_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _connect(rpc_urls: list[str]) -> tuple[Web3, str]:
    errors: list[str] = []
    for rpc_url in rpc_urls:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
        try:
            if w3.is_connected() and int(w3.eth.chain_id) == bnb.CHAIN_ID:
                return w3, rpc_url
        except Exception as exc:
            errors.append(f"{rpc_url}: {exc!r}")
    raise RuntimeError("no usable BNB RPC: " + "; ".join(errors[:3]))


def _pair_key(left: str, right: str) -> str:
    return ":".join(sorted((left.upper(), right.upper())))


def _load_decimals(w3: Web3, token: dict[str, Any]) -> None:
    if token.get("decimals") is not None:
        return
    contract = w3.eth.contract(address=Web3.to_checksum_address(str(token["address"])), abi=ERC20_METADATA_ABI)
    token["decimals"] = int(contract.functions.decimals().call())


def _token_ref(token: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "address": token.get("address"),
        "decimals": token.get("decimals"),
    }


def _amount(raw: int, decimals: int) -> str:
    value = int(raw) / float(10**decimals)
    return format(value, ".18g")


def _amount_decimal(raw: int, decimals: int) -> Decimal:
    return Decimal(int(raw)) / Decimal(10 ** int(decimals))


def _v2_quality(
    token0: dict[str, Any],
    token1: dict[str, Any],
    reserve0: int,
    reserve1: int,
) -> tuple[str, list[str]]:
    reasons = []
    amount0 = _amount_decimal(reserve0, int(token0["decimals"]))
    amount1 = _amount_decimal(reserve1, int(token1["decimals"]))
    if amount0 <= 0 or amount1 <= 0:
        reasons.append("empty_reserve")
    if amount0 < DEFAULT_MIN_SIDE_UNITS:
        reasons.append(f"token0_reserve_below_{DEFAULT_MIN_SIDE_UNITS}")
    if amount1 < DEFAULT_MIN_SIDE_UNITS:
        reasons.append(f"token1_reserve_below_{DEFAULT_MIN_SIDE_UNITS}")
    if token0.get("stable") and amount0 < DEFAULT_MIN_V2_RESERVE_IN_UNITS:
        reasons.append(f"stable_token0_reserve_below_{DEFAULT_MIN_V2_RESERVE_IN_UNITS}")
    if token1.get("stable") and amount1 < DEFAULT_MIN_V2_RESERVE_IN_UNITS:
        reasons.append(f"stable_token1_reserve_below_{DEFAULT_MIN_V2_RESERVE_IN_UNITS}")
    return ("qualified" if not reasons else "rejected", reasons)


def _v2_pool(
    w3: Web3,
    dex: bnb.DexConfig,
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any] | None:
    factory = w3.eth.contract(address=Web3.to_checksum_address(dex.factory), abi=V2_FACTORY_ABI)
    pool_address = factory.functions.getPair(
        Web3.to_checksum_address(str(left["address"])),
        Web3.to_checksum_address(str(right["address"])),
    ).call()
    if int(pool_address, 16) == 0:
        return None
    pair = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=V2_PAIR_ABI)
    reserve0, reserve1, block_timestamp = pair.functions.getReserves().call()
    token0 = Web3.to_checksum_address(pair.functions.token0().call()).lower()
    token1 = Web3.to_checksum_address(pair.functions.token1().call()).lower()
    by_address = {str(left["address"]).lower(): left, str(right["address"]).lower(): right}
    token0_info = _token_ref(by_address[token0])
    token1_info = _token_ref(by_address[token1])
    quality_status, quality_reasons = _v2_quality(token0_info, token1_info, int(reserve0), int(reserve1))
    usable = quality_status == "qualified"
    return {
        "dex": dex.name,
        "protocol": "v2",
        "pool": Web3.to_checksum_address(pool_address),
        "router": dex.router,
        "fee": f"{dex.fee_bps} bps",
        "token0": token0_info,
        "token1": token1_info,
        "reserve0Raw": str(reserve0),
        "reserve1Raw": str(reserve1),
        "reserve0": _amount(int(reserve0), int(token0_info["decimals"])),
        "reserve1": _amount(int(reserve1), int(token1_info["decimals"])),
        "blockTimestampLast": int(block_timestamp),
        "poolState": "usable" if usable else "rejected",
        "qualityStatus": quality_status,
        "qualityRejectReasons": quality_reasons,
    }


def _v3_probe_amount_raw(token: dict[str, Any]) -> int:
    decimals = int(token.get("decimals") or 18)
    return max(1, 10 ** max(decimals - 6, 0))


def _v3_quote_probe(
    w3: Web3,
    dex: bnb.DexConfig,
    token_in: dict[str, Any],
    token_out: dict[str, Any],
    fee: int,
) -> tuple[bool, str | None]:
    try:
        quoter = w3.eth.contract(address=Web3.to_checksum_address(str(dex.quoter)), abi=V3_QUOTER_ABI)
        result = quoter.functions.quoteExactInputSingle(
            {
                "tokenIn": Web3.to_checksum_address(str(token_in["address"])),
                "tokenOut": Web3.to_checksum_address(str(token_out["address"])),
                "amountIn": _v3_probe_amount_raw(token_in),
                "fee": int(fee),
                "sqrtPriceLimitX96": 0,
            }
        ).call()
        if int(result[0]) <= 0:
            return False, "v3_quote_zero_output"
        return True, None
    except Exception as exc:
        return False, f"v3_quote_failed:{str(exc)[:120]}"


def _v3_pools(
    w3: Web3,
    dex: bnb.DexConfig,
    left: dict[str, Any],
    right: dict[str, Any],
) -> list[dict[str, Any]]:
    factory = w3.eth.contract(address=Web3.to_checksum_address(dex.factory), abi=V3_FACTORY_ABI)
    pools = []
    for fee in dex.fee_tiers:
        pool_address = factory.functions.getPool(
            Web3.to_checksum_address(str(left["address"])),
            Web3.to_checksum_address(str(right["address"])),
            int(fee),
        ).call()
        if int(pool_address, 16) == 0:
            continue
        pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=V3_POOL_ABI)
        token0_address = Web3.to_checksum_address(pool.functions.token0().call()).lower()
        token1_address = Web3.to_checksum_address(pool.functions.token1().call()).lower()
        token_by_address = {
            str(left["address"]).lower(): _token_ref(left),
            str(right["address"]).lower(): _token_ref(right),
        }
        liquidity = int(pool.functions.liquidity().call())
        slot0 = pool.functions.slot0().call()
        quality_reasons = []
        if liquidity <= 0:
            quality_reasons.append("zero_active_liquidity")
        if liquidity < DEFAULT_MIN_V3_ACTIVE_LIQUIDITY:
            quality_reasons.append(f"active_liquidity_below_{DEFAULT_MIN_V3_ACTIVE_LIQUIDITY}")
        if not bool(slot0[6]):
            quality_reasons.append("pool_locked")
        if liquidity > 0 and bool(slot0[6]):
            left_to_right_ok, left_to_right_reason = _v3_quote_probe(w3, dex, left, right, int(fee))
            right_to_left_ok, right_to_left_reason = _v3_quote_probe(w3, dex, right, left, int(fee))
            if not left_to_right_ok:
                quality_reasons.append(f"v3_quote_left_to_right_failed:{left_to_right_reason}")
            if not right_to_left_ok:
                quality_reasons.append(f"v3_quote_right_to_left_failed:{right_to_left_reason}")
        quality_status = "qualified" if not quality_reasons else "rejected"
        pools.append(
            {
                "dex": dex.name,
                "protocol": "v3",
                "pool": Web3.to_checksum_address(pool_address),
                "router": dex.router,
                "quoter": dex.quoter,
                "fee": int(fee),
                "token0": token_by_address[token0_address],
                "token1": token_by_address[token1_address],
                "activeLiquidity": str(liquidity),
                "tick": int(slot0[1]),
                "unlocked": bool(slot0[6]),
                "poolState": "usable" if quality_status == "qualified" else "rejected",
                "qualityStatus": quality_status,
                "qualityRejectReasons": quality_reasons,
            }
        )
    return pools


def scan_pair_pools(w3: Web3, left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = []
    for dex in bnb.DEXES:
        if dex.protocol == "v2":
            pool = _v2_pool(w3, dex, left, right)
            if pool is not None:
                pools.append(pool)
        elif dex.protocol == "v3":
            pools.extend(_v3_pools(w3, dex, left, right))
    return pools


def _usable(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [pool for pool in pools if pool.get("qualityStatus") == "qualified"]


def _scan_pairs_parallel(
    w3: Web3,
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    max_workers: int = 16,
) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(scan_pair_pools, w3, left, right): _pair_key(str(left["symbol"]), str(right["symbol"]))
            for left, right in pairs
        }
        for future in as_completed(futures):
            pair_key = futures[future]
            results[pair_key] = future.result()
    return results


def _route_hop(token_in: dict[str, Any], token_out: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    return {
        "tokenIn": _token_ref(token_in),
        "tokenOut": _token_ref(token_out),
        "pool": pool["pool"],
        "dex": pool["dex"],
        "protocol": pool["protocol"],
        "fee": pool["fee"],
        "router": pool.get("router"),
        "quoter": pool.get("quoter"),
        "token0": _token_ref(pool["token0"]) if pool.get("token0") else None,
        "token1": _token_ref(pool["token1"]) if pool.get("token1") else None,
        "poolState": pool["poolState"],
        "qualityStatus": pool.get("qualityStatus"),
        "qualityRejectReasons": pool.get("qualityRejectReasons") or [],
    }


def _to_raw(amount: Decimal, token: dict[str, Any]) -> int:
    return int(amount * Decimal(10 ** int(token.get("decimals") or 18)))


def _from_raw(raw: int, token: dict[str, Any]) -> Decimal:
    return Decimal(int(raw)) / Decimal(10 ** int(token.get("decimals") or 18))


def _quote_hop_raw(w3: Web3, hop: dict[str, Any], amount_in_raw: int) -> int:
    token_in = Web3.to_checksum_address(str(hop["tokenIn"]["address"]))
    token_out = Web3.to_checksum_address(str(hop["tokenOut"]["address"]))
    if hop["protocol"] == "v2":
        router = w3.eth.contract(address=Web3.to_checksum_address(str(hop["router"])), abi=ROUTER_ABI)
        amounts = router.functions.getAmountsOut(int(amount_in_raw), [token_in, token_out]).call()
        return int(amounts[-1])
    quoter = w3.eth.contract(address=Web3.to_checksum_address(str(hop["quoter"])), abi=V3_QUOTER_ABI)
    result = quoter.functions.quoteExactInputSingle(
        {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amountIn": int(amount_in_raw),
            "fee": int(hop["fee"]),
            "sqrtPriceLimitX96": 0,
        }
    ).call()
    return int(result[0])


def _preflight_route_to_stable(
    w3: Web3,
    route: dict[str, Any],
    stable: dict[str, Any],
) -> dict[str, Any]:
    forward = list(route["hops"])
    reverse = []
    for hop in reversed(forward):
        reverse.append({**hop, "tokenIn": hop["tokenOut"], "tokenOut": hop["tokenIn"]})
    amount_raw = _to_raw(DEFAULT_BRIDGE_PREFLIGHT_STABLE, stable)
    try:
        for hop in reverse + forward:
            amount_raw = _quote_hop_raw(w3, hop, amount_raw)
        final_amount = _from_raw(amount_raw, stable)
        ratio = final_amount / DEFAULT_BRIDGE_PREFLIGHT_STABLE
        return {
            "ok": ratio >= DEFAULT_MIN_BRIDGE_RETURN_RATIO,
            "stableSymbol": stable.get("symbol"),
            "initialStableAmount": str(DEFAULT_BRIDGE_PREFLIGHT_STABLE),
            "finalStableAmount": format(final_amount, "f"),
            "returnRatio": format(ratio, "f"),
            "minReturnRatio": str(DEFAULT_MIN_BRIDGE_RETURN_RATIO),
            "rejectReason": None if ratio >= DEFAULT_MIN_BRIDGE_RETURN_RATIO else "bridge_roundtrip_return_below_threshold",
        }
    except Exception as exc:
        return {
            "ok": False,
            "stableSymbol": stable.get("symbol"),
            "initialStableAmount": str(DEFAULT_BRIDGE_PREFLIGHT_STABLE),
            "finalStableAmount": None,
            "returnRatio": None,
            "minReturnRatio": str(DEFAULT_MIN_BRIDGE_RETURN_RATIO),
            "rejectReason": "bridge_roundtrip_quote_failed",
            "error": str(exc),
        }


def _pool_route_ref(pool: dict[str, Any]) -> dict[str, Any]:
    return {
        "dex": pool.get("dex"),
        "protocol": pool.get("protocol"),
        "pool": pool.get("pool"),
        "fee": pool.get("fee"),
        "router": pool.get("router"),
        "quoter": pool.get("quoter"),
        "qualityStatus": pool.get("qualityStatus"),
    }


def _route_stable_symbols(routes: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(route.get("stableSymbol") or (route.get("path") or [""])[-1])
            for route in routes
            if route.get("stableSymbol") or route.get("path")
        }
    )


def _table_token_record(
    token: dict[str, Any],
    direct_pair_names: dict[str, list[str]],
    layer: str,
) -> dict[str, Any]:
    address = str(token.get("address") or "").lower()
    direct_routes = list(token.get("routesToStableDirect") or [])
    via_routes = list(token.get("routesToStableViaT1") or [])
    all_routes = list(token.get("routesToStable") or [])
    return {
        **_token_ref(token),
        "layer": layer,
        "directPairNames": sorted(direct_pair_names.get(address, [])),
        "directStableRouteCount": len(direct_routes),
        "viaT1StableRouteCount": len(via_routes),
        "stableRouteCount": len(all_routes),
        "reachableStableSymbols": _route_stable_symbols(all_routes),
    }


def build_static_table(w3: Web3, candidates_path: Path) -> dict[str, Any]:
    candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    pairs = list(candidates_payload.get("candidates") or [])
    tokens_by_address: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        for side in ("base", "quote"):
            address = str(pair.get(side) or "").lower()
            if not address:
                continue
            token = {
                "symbol": pair.get(f"{side}Symbol"),
                "name": pair.get(f"{side}Name"),
                "address": address,
                "decimals": None,
            }
            tokens_by_address.setdefault(address, token)

    stable_tokens = {
        symbol: {
            "symbol": token.symbol,
            "name": token.symbol,
            "address": token.address.lower(),
            "decimals": token.decimals,
            "stable": True,
        }
        for symbol, token in bnb.TOKENS.items()
        if symbol in ANCHOR_STABLES
    }

    niche_tokens = list(tokens_by_address.values())
    for token in niche_tokens:
        _load_decimals(w3, token)
    token_stable_pairs = [
        (token, stable)
        for token in niche_tokens
        for stable in stable_tokens.values()
    ]
    scanned_token_stable = _scan_pairs_parallel(w3, token_stable_pairs, max_workers=4)
    for token in niche_tokens:
        token["directStablePools"] = {}
        for stable_symbol, stable in stable_tokens.items():
            pools = scanned_token_stable.get(_pair_key(str(token["symbol"]), stable_symbol), [])
            token["directStablePools"][stable_symbol] = {
                "pair": _pair_key(str(token["symbol"]), stable_symbol),
                "allPools": pools,
                "usablePools": _usable(pools),
            }

    stable_list = list(stable_tokens.values())
    stable_pairs = [
        (stable_list[left_index], stable_list[right_index])
        for left_index in range(len(stable_list))
        for right_index in range(left_index + 1, len(stable_list))
    ]
    scanned_stable_pairs = _scan_pairs_parallel(w3, stable_pairs, max_workers=4)
    stable_pair_pools: dict[str, dict[str, Any]] = {}
    for left, right in stable_pairs:
        left_symbol = str(left["symbol"])
        right_symbol = str(right["symbol"])
        key = _pair_key(left_symbol, right_symbol)
        pools = scanned_stable_pairs.get(key, [])
        stable_pair_pools[key] = {
            "pair": key,
            "left": _token_ref(left),
            "right": _token_ref(right),
            "allPools": pools,
            "usablePools": _usable(pools),
        }

    usdc = stable_tokens["USDC"]
    stable_usdc_pools: dict[str, dict[str, Any]] = {}
    for stable_symbol in stable_tokens:
        if stable_symbol == "USDC":
            continue
        key = _pair_key(stable_symbol, "USDC")
        stable_usdc_pools[stable_symbol] = stable_pair_pools.get(
            key,
            {"pair": key, "allPools": [], "usablePools": []},
        )

    token_records = []
    for token in niche_tokens:
        routes_to_stable = []
        for stable_symbol, stable in stable_tokens.items():
            direct_stable = token["directStablePools"][stable_symbol]["usablePools"]
            for pool in direct_stable:
                route = {
                    "routeType": "direct_stable",
                    "stableSymbol": stable_symbol,
                    "path": [token["symbol"], stable_symbol],
                    "hopCount": 1,
                    "executionStatus": "unverified_static_pool_lookup",
                    "hops": [_route_hop(token, stable, pool)],
                }
                route["preflight"] = _preflight_route_to_stable(w3, route, stable)
                if route["preflight"]["ok"]:
                    routes_to_stable.append(route)
        routes_to_usdc = []
        direct_usdc = token["directStablePools"]["USDC"]["usablePools"]
        for pool in direct_usdc:
            routes_to_usdc.append(
                {
                    "routeType": "direct",
                    "path": [token["symbol"], "USDC"],
                    "hopCount": 1,
                    "executionStatus": "unverified_static_pool_lookup",
                    "hops": [_route_hop(token, usdc, pool)],
                }
            )
        for stable_symbol in ("USDT", "BUSD", "DAI"):
            first_hop_pools = token["directStablePools"][stable_symbol]["usablePools"]
            second_hop_pools = stable_usdc_pools[stable_symbol]["usablePools"]
            stable = stable_tokens[stable_symbol]
            for first in first_hop_pools:
                for second in second_hop_pools:
                    routes_to_usdc.append(
                        {
                            "routeType": "stable_bridge",
                            "path": [token["symbol"], stable_symbol, "USDC"],
                            "hopCount": 2,
                            "executionStatus": "unverified_static_pool_lookup",
                            "hops": [
                                _route_hop(token, stable, first),
                                _route_hop(stable, usdc, second),
                            ],
                        }
                    )
        for route in routes_to_usdc:
            route["preflight"] = _preflight_route_to_stable(w3, route, usdc)
            if not route["preflight"]["ok"]:
                for hop in route["hops"]:
                    hop["qualityStatus"] = "rejected"
                    reasons = list(hop.get("qualityRejectReasons") or [])
                    if route["preflight"]["rejectReason"] not in reasons:
                        reasons.append(str(route["preflight"]["rejectReason"]))
                    hop["qualityRejectReasons"] = reasons
        routes_to_usdc = [route for route in routes_to_usdc if route["preflight"]["ok"]]
        token_records.append(
            {
                **token,
                "directStableRouteStatus": "reachable" if routes_to_stable else "unresolved",
                "stableRouteStatus": "reachable" if routes_to_stable else "unresolved",
                "routesToStableDirect": routes_to_stable,
                "routesToStableViaT1": [],
                "routesToStable": list(routes_to_stable),
                "usdcRouteStatus": "reachable" if routes_to_usdc else "unresolved",
                "routesToUsdc": routes_to_usdc,
            }
        )

    direct_pair_scan_inputs = [
        (
            tokens_by_address[str(pair["base"]).lower()],
            tokens_by_address[str(pair["quote"]).lower()],
        )
        for pair in pairs
    ]
    scanned_direct_pairs = _scan_pairs_parallel(w3, direct_pair_scan_inputs, max_workers=4)
    direct_pairs = []
    for pair in pairs:
        x = tokens_by_address[str(pair["base"]).lower()]
        y = tokens_by_address[str(pair["quote"]).lower()]
        direct_pools = scanned_direct_pairs.get(_pair_key(str(x["symbol"]), str(y["symbol"])), [])
        direct_pairs.append(
            {
                "pairName": pair.get("pairName"),
                "x": _token_ref(x),
                "y": _token_ref(y),
                "allPools": direct_pools,
                "usablePools": _usable(direct_pools),
                "source": {
                    "dex": pair.get("dexName"),
                    "volumeUsdH24": pair.get("volumeUsdH24"),
                    "tradeUrl": pair.get("tradeUrl"),
                    "pancakeV3Pools": pair.get("pancakeV3Pools") or [],
                },
            }
        )

    token_record_by_address = {
        str(token["address"]).lower(): token
        for token in token_records
    }
    qualified_direct_pairs = [pair for pair in direct_pairs if pair.get("usablePools")]
    direct_pair_names_by_token: dict[str, list[str]] = {}
    pt_addresses: set[str] = set()
    for pair in qualified_direct_pairs:
        for side in ("x", "y"):
            address = str(pair[side]["address"]).lower()
            pt_addresses.add(address)
            direct_pair_names_by_token.setdefault(address, []).append(str(pair.get("pairName")))

    t1_addresses = {
        address
        for address in pt_addresses
        if token_record_by_address[address].get("routesToStableDirect")
    }
    t2_bridge_candidates: dict[str, list[dict[str, Any]]] = {}
    t2_scan_inputs = [
        (token_record_by_address[pt_address], token_record_by_address[t1_address])
        for pt_address in sorted(pt_addresses - t1_addresses)
        for t1_address in sorted(t1_addresses)
        if pt_address != t1_address
    ]
    scanned_t2_bridges: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if t2_scan_inputs:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(scan_pair_pools, w3, pt_token, t1_token): (
                    str(pt_token["address"]).lower(),
                    str(t1_token["address"]).lower(),
                )
                for pt_token, t1_token in t2_scan_inputs
            }
            for future in as_completed(futures):
                scanned_t2_bridges[futures[future]] = future.result()
    for pt_address, t1_address in scanned_t2_bridges:
        usable_pools = _usable(scanned_t2_bridges[(pt_address, t1_address)])
        if not usable_pools:
            continue
        pt_token = token_record_by_address[pt_address]
        t1_token = token_record_by_address[t1_address]
        t2_bridge_candidates.setdefault(pt_address, []).append(
            {
                "pairName": f"{pt_token['symbol']} / {t1_token['symbol']}",
                "bridgeScope": "global_pt_to_t1",
                "viaT1Address": t1_address,
                "viaT1Token": _token_ref(t1_token),
                "x": _token_ref(pt_token),
                "y": _token_ref(t1_token),
                "allPools": scanned_t2_bridges[(pt_address, t1_address)],
                "usablePools": usable_pools,
            }
        )

    t2_addresses = set(t2_bridge_candidates)
    for t2_address, bridge_candidates in t2_bridge_candidates.items():
        token = token_record_by_address[t2_address]
        routes_via_t1 = []
        rejected_routes_via_t1 = []
        for bridge in bridge_candidates:
            via_t1 = token_record_by_address[bridge["viaT1Address"]]
            for pool in bridge["usablePools"]:
                first_hop = _route_hop(token, via_t1, pool)
                for t1_route in via_t1.get("routesToStableDirect") or []:
                    stable_symbol = str(t1_route.get("stableSymbol") or t1_route["path"][-1])
                    stable = stable_tokens[stable_symbol]
                    route = {
                        "routeType": "via_t1_stable",
                        "stableSymbol": stable_symbol,
                        "path": [token["symbol"]] + list(t1_route["path"]),
                        "hopCount": 1 + int(t1_route.get("hopCount") or len(t1_route.get("hops") or [])),
                        "executionStatus": "unverified_static_pool_lookup",
                        "viaT1Token": _token_ref(via_t1),
                        "sourcePairName": bridge.get("pairName"),
                        "hops": [first_hop] + list(t1_route.get("hops") or []),
                    }
                    route["preflight"] = _preflight_route_to_stable(w3, route, stable)
                    if route["preflight"]["ok"]:
                        routes_via_t1.append(route)
                    else:
                        rejected_routes_via_t1.append(route)
        token["routesToStableViaT1"] = routes_via_t1
        token["rejectedRoutesToStableViaT1"] = rejected_routes_via_t1
        if not token.get("routesToStableDirect") and routes_via_t1:
            token["stableRouteStatus"] = "reachable_via_t1"
            token["routesToStable"] = list(routes_via_t1)
        elif token.get("routesToStableDirect"):
            token["routesToStable"] = list(token.get("routesToStableDirect") or []) + routes_via_t1

    pn_allowed_addresses = t1_addresses | t2_addresses
    pn_pairs = []
    for pair in qualified_direct_pairs:
        x_address = str(pair["x"]["address"]).lower()
        y_address = str(pair["y"]["address"]).lower()
        if x_address not in pn_allowed_addresses or y_address not in pn_allowed_addresses:
            continue
        pn_pairs.append(
            {
                **pair,
                "xLayer": "T1" if x_address in t1_addresses else "T2",
                "yLayer": "T1" if y_address in t1_addresses else "T2",
            }
        )

    stable_pool_refs: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in stable_tokens}
    for pair_key, record in stable_pair_pools.items():
        for pool in record.get("usablePools") or []:
            for stable_symbol, counterparty in (
                (record["left"]["symbol"], record["right"]["symbol"]),
                (record["right"]["symbol"], record["left"]["symbol"]),
            ):
                stable_pool_refs[str(stable_symbol)].append(
                    {
                        "pair": pair_key,
                        "counterpartySymbol": counterparty,
                        "counterpartyType": "stable",
                        **_pool_route_ref(pool),
                    }
                )
    for token in token_records:
        for stable_symbol, pools in token.get("directStablePools", {}).items():
            for pool in pools.get("usablePools") or []:
                stable_pool_refs[str(stable_symbol)].append(
                    {
                        "pair": pools.get("pair"),
                        "counterpartySymbol": token.get("symbol"),
                        "counterpartyType": "niche",
                        **_pool_route_ref(pool),
                    }
                )

    w_tokens = []
    for stable_symbol, stable in stable_tokens.items():
        refs = stable_pool_refs.get(stable_symbol, [])
        if not refs:
            continue
        w_tokens.append(
            {
                **_token_ref(stable),
                "qualifiedPoolCount": len(refs),
                "qualifiedPools": refs,
            }
        )

    pt_token_records = [
        _table_token_record(token_record_by_address[address], direct_pair_names_by_token, "PT")
        for address in sorted(pt_addresses, key=lambda item: str(token_record_by_address[item].get("symbol")))
    ]
    t1_token_records = [
        {
            **_table_token_record(token_record_by_address[address], direct_pair_names_by_token, "T1"),
            "routesToStable": token_record_by_address[address].get("routesToStableDirect") or [],
        }
        for address in sorted(t1_addresses, key=lambda item: str(token_record_by_address[item].get("symbol")))
    ]
    t2_token_records = [
        {
            **_table_token_record(token_record_by_address[address], direct_pair_names_by_token, "T2"),
            "bridgesToT1": [
                {
                    **{key: value for key, value in bridge.items() if key != "usablePools"},
                    "usablePools": [_pool_route_ref(pool) for pool in bridge.get("usablePools") or []],
                }
                for bridge in t2_bridge_candidates.get(address, [])
            ],
            "routesToStableViaT1": token_record_by_address[address].get("routesToStableViaT1") or [],
            "rejectedRoutesToStableViaT1": token_record_by_address[address].get("rejectedRoutesToStableViaT1") or [],
        }
        for address in sorted(t2_addresses, key=lambda item: str(token_record_by_address[item].get("symbol")))
    ]

    reachable = [token for token in token_records if token["usdcRouteStatus"] == "reachable"]
    stable_reachable = [token for token in token_records if token["stableRouteStatus"] != "unresolved"]
    return {
        "version": 2,
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "generatedAt": _utc_now(),
        "sourceCandidates": {
            "path": str(candidates_path),
            "generatedAt": candidates_payload.get("generatedAt"),
            "candidatePairCount": len(direct_pairs),
        },
        "stableAnchors": stable_tokens,
        "stablePairPools": stable_pair_pools,
        "stableToUsdcPools": stable_usdc_pools,
        "nicheDirectPairs": direct_pairs,
        "nicheTokens": token_records,
        "W": {
            "description": "Stablecoins that appear in at least one qualified scanned pool.",
            "tokenCount": len(w_tokens),
            "tokens": w_tokens,
            "stablePairs": stable_pair_pools,
        },
        "PT": {
            "description": "Niche tokens and direct X-Y pairs whose X-Y pool itself is qualified.",
            "tokenCount": len(pt_token_records),
            "pairCount": len(qualified_direct_pairs),
            "tokens": pt_token_records,
            "pairs": qualified_direct_pairs,
        },
        "T1": {
            "description": "PT tokens with a qualified direct stablecoin pool and 1000 stable round-trip preflight >= 900.",
            "tokenCount": len(t1_token_records),
            "tokens": t1_token_records,
        },
        "T2": {
            "description": "PT tokens outside T1 that have a qualified X-Y pool to at least one T1 token.",
            "tokenCount": len(t2_token_records),
            "tokens": t2_token_records,
        },
        "PN": {
            "description": "Qualified PT pairs kept for analysis; both sides must be in T1 or T2.",
            "pairCount": len(pn_pairs),
            "tokens": [
                _table_token_record(token_record_by_address[address], direct_pair_names_by_token, "T1" if address in t1_addresses else "T2")
                for address in sorted(pn_allowed_addresses, key=lambda item: str(token_record_by_address[item].get("symbol")))
            ],
            "pairs": pn_pairs,
        },
        "summary": {
            "nicheTokenCount": len(token_records),
            "nichePairCount": len(direct_pairs),
            "WStableCount": len(w_tokens),
            "PTTokenCount": len(pt_token_records),
            "PTPairCount": len(qualified_direct_pairs),
            "T1TokenCount": len(t1_token_records),
            "T2TokenCount": len(t2_token_records),
            "PNTokenCount": len(pn_allowed_addresses),
            "PNPairCount": len(pn_pairs),
            "tokensReachableToUsdcWithinTwoHops": len(reachable),
            "tokensUnresolvedToUsdcWithinTwoHops": len(token_records) - len(reachable),
            "tokensReachableToAnyStable": len(stable_reachable),
            "tokensUnresolvedToAnyStable": len(token_records) - len(stable_reachable),
            "lookupStatus": "static_pool_existence_and_state_only",
            "qualityStatus": "qualified_pools_only_for_routes",
            "qualityRules": {
                "minSideUnits": str(DEFAULT_MIN_SIDE_UNITS),
                "minStableSideUnitsForV2Bridge": str(DEFAULT_MIN_V2_RESERVE_IN_UNITS),
                "minV3ActiveLiquidity": str(DEFAULT_MIN_V3_ACTIVE_LIQUIDITY),
                "bridgePreflightStable": str(DEFAULT_BRIDGE_PREFLIGHT_STABLE),
                "minBridgeRoundtripReturnRatio": str(DEFAULT_MIN_BRIDGE_RETURN_RATIO),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build BNB niche-token layered stable lookup table.")
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    w3, rpc_url = _connect(args.rpc_url or list(bnb.DEFAULT_RPC_URLS))
    payload = build_static_table(w3, args.candidates)
    payload["rpcUrl"] = rpc_url
    payload["blockNumber"] = int(w3.eth.block_number)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "summary": payload["summary"],
                "blockNumber": payload["blockNumber"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
