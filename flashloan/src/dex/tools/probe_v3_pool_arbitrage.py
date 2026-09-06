"""Probe Avalanche V3 pool-level arbitrage using current Quoter output.

The pool cache is used only to discover candidate pools. Every profit estimate
reads current pool state and calls the configured Quoter at the current block.
No signer is loaded and no transaction is built, deployed, or broadcast.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_v3_pool_arbitrage_probe.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from bot.execution.liquidation_realtime_params import read_aave_flashloan_premium
from bot.execution.uniswap_v3 import (
    V3PoolSnapshot,
    normalized_v3_tick,
    quote_v3_exact_input_path,
    snapshot_v3_pool,
)
from market.observer_common import DEFAULT_RPC, parse_rpc_urls


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class PoolDecision:
    token_x: str
    token_y: str
    low: V3PoolSnapshot
    high: V3PoolSnapshot
    low_normalized_tick: int
    high_normalized_tick: int
    tick_delta: int
    valid_pool_count: int


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _rpc_candidates() -> list[str]:
    candidates = []
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC"):
        value = os.getenv(name, "").strip()
        if value:
            candidates.append(value)
    candidates.extend(parse_rpc_urls(os.getenv("AVALANCHE_RPCS", "")))
    candidates.append(DEFAULT_RPC)
    unique: list[str] = []
    for item in candidates:
        if item and item not in unique:
            unique.append(item)
    return unique


def _connect_web3(rpc_url: str | None = None, timeout_seconds: int = 12) -> tuple[Web3, str]:
    errors: list[str] = []
    candidates = [rpc_url] if rpc_url else _rpc_candidates()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(candidate, request_kwargs={"timeout": timeout_seconds}))
            chain_id = int(w3.eth.chain_id)
            if chain_id != 43114:
                errors.append(f"chain_id_{chain_id}_not_avalanche")
                continue
            return w3, candidate
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("no usable Avalanche RPC: " + "; ".join(errors[:3]))


def _symbol_aliases(symbol: str) -> set[str]:
    value = str(symbol or "").strip().upper()
    if not value:
        return set()
    aliases = {value}
    if "." in value:
        aliases.add(value.split(".", 1)[0])
    wrapped = {
        "AVAX": {"WAVAX"},
        "WAVAX": {"AVAX"},
        "ETH": {"WETH", "WETH.E"},
        "WETH": {"ETH", "WETH.E"},
        "BTC": {"BTC.B"},
        "BTC.B": {"BTC"},
        "WBTC": {"WBTC.E"},
        "WBTC.E": {"WBTC"},
        "USDT": {"USDT.E", "USDT"},
        "USDC": {"USDC.E", "USDC"},
    }
    aliases.update(wrapped.get(value, set()))
    return aliases


def _entry_symbols(entry: dict[str, Any]) -> tuple[set[str], set[str]]:
    left = entry.get("tokenX_symbol") or entry.get("token_x_symbol") or entry.get("token0_symbol")
    right = entry.get("tokenY_symbol") or entry.get("token_y_symbol") or entry.get("token1_symbol")
    return _symbol_aliases(str(left or "")), _symbol_aliases(str(right or ""))


def _entry_tokens(entry: dict[str, Any]) -> tuple[str, str]:
    return (
        str(entry.get("tokenX") or entry.get("token_x") or "").strip(),
        str(entry.get("tokenY") or entry.get("token_y") or "").strip(),
    )


def _pool_addresses(entry: dict[str, Any], max_pools: int = 5) -> list[str]:
    pools = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
    addresses: list[str] = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        if int(pool.get("adapterKind", pool.get("adapter_kind", 1)) or 0) != 1:
            continue
        address = str(pool.get("pool") or pool.get("pool_address") or pool.get("address") or "").strip()
        if address and address != ZERO_ADDRESS and address not in addresses:
            addresses.append(address)
        if len(addresses) >= max_pools:
            break
    return addresses


def _slot_report(entry: dict[str, Any], *, max_pools: int) -> dict[str, Any]:
    addresses = _pool_addresses(entry, max_pools=max_pools)
    return {
        "tokenXSymbol": str(entry.get("tokenX_symbol") or ""),
        "tokenYSymbol": str(entry.get("tokenY_symbol") or ""),
        "tokenX": str(entry.get("tokenX") or ""),
        "tokenY": str(entry.get("tokenY") or ""),
        "effectivePoolCount": str(len(addresses)),
        "requiredPoolSlots": str(max_pools),
        "missingPoolSlots": str(max(0, max_pools - len(addresses))),
        "hasFiveEffectivePools": len(addresses) >= max_pools,
        "pools": addresses,
    }


def _oriented_pair_entry(cache: dict[str, Any], x_symbol: str, y_symbol: str) -> dict[str, Any] | None:
    wanted_x = _symbol_aliases(x_symbol)
    wanted_y = _symbol_aliases(y_symbol)
    for entry in cache.get("pools") or []:
        if not isinstance(entry, dict):
            continue
        left, right = _entry_symbols(entry)
        same_order = bool(wanted_x & left and wanted_y & right)
        reverse_order = bool(wanted_x & right and wanted_y & left)
        if not same_order and not reverse_order:
            continue
        entry_x, entry_y = _entry_tokens(entry)
        if not entry_x or not entry_y:
            return None
        oriented = dict(entry)
        oriented["tokenX"] = entry_x if same_order else entry_y
        oriented["tokenY"] = entry_y if same_order else entry_x
        oriented["tokenX_symbol"] = x_symbol
        oriented["tokenY_symbol"] = y_symbol
        return oriented
    return None


def _candidate_groups(cache: dict[str, Any], x_symbol: str | None, y_symbol: str | None) -> list[dict[str, Any]]:
    entries = cache.get("pools") if isinstance(cache.get("pools"), list) else []
    if x_symbol and y_symbol:
        ux = _oriented_pair_entry(cache, "USDC", x_symbol)
        uy = _oriented_pair_entry(cache, "USDC", y_symbol)
        xy = _oriented_pair_entry(cache, x_symbol, y_symbol)
        yx = _oriented_pair_entry(cache, y_symbol, x_symbol)
        if not ux or not uy or not xy:
            return []
        return [{"routeKey": f"{x_symbol.upper()}:{y_symbol.upper()}", "ux": ux, "uy": uy, "xy": xy, "yx": yx}]

    usdc_symbols: list[str] = []
    for entry in entries:
        left, right = _entry_symbols(entry)
        if "USDC" in left:
            right_symbol = str(entry.get("tokenY_symbol") or entry.get("token1_symbol") or "").strip()
            if right_symbol and right_symbol.upper() != "USDC":
                usdc_symbols.append(right_symbol)
        elif "USDC" in right:
            left_symbol = str(entry.get("tokenX_symbol") or entry.get("token0_symbol") or "").strip()
            if left_symbol and left_symbol.upper() != "USDC":
                usdc_symbols.append(left_symbol)

    groups: list[dict[str, Any]] = []
    unique = sorted(dict.fromkeys(usdc_symbols), key=str.upper)
    for index, left_symbol in enumerate(unique):
        for right_symbol in unique[index + 1 :]:
            ux = _oriented_pair_entry(cache, "USDC", left_symbol)
            uy = _oriented_pair_entry(cache, "USDC", right_symbol)
            xy = _oriented_pair_entry(cache, left_symbol, right_symbol)
            yx = _oriented_pair_entry(cache, right_symbol, left_symbol)
            if ux and uy and xy:
                groups.append({"routeKey": f"{left_symbol}:{right_symbol}", "ux": ux, "uy": uy, "xy": xy, "yx": yx})
    return groups


def _runtime_decision(
    w3: Web3,
    entry: dict[str, Any],
    *,
    min_liquidity: int,
    max_pools: int,
) -> PoolDecision:
    token_x, token_y = _entry_tokens(entry)
    if not token_x or not token_y:
        raise ValueError("pair entry is missing token addresses")
    low: V3PoolSnapshot | None = None
    high: V3PoolSnapshot | None = None
    low_tick: int | None = None
    high_tick: int | None = None
    valid_count = 0
    for pool_address in _pool_addresses(entry, max_pools=max_pools):
        snapshot = snapshot_v3_pool(w3, pool_address)
        if snapshot.liquidity < min_liquidity:
            continue
        tick = normalized_v3_tick(snapshot, token_x, token_y)
        valid_count += 1
        if low is None or low_tick is None or tick < low_tick:
            low = snapshot
            low_tick = tick
        if high is None or high_tick is None or tick > high_tick:
            high = snapshot
            high_tick = tick
    if not low or not high or low_tick is None or high_tick is None:
        raise ValueError("not_enough_valid_pools")
    if low.pool_address == high.pool_address:
        raise ValueError("no_pool_spread")
    return PoolDecision(
        token_x=Web3.to_checksum_address(token_x),
        token_y=Web3.to_checksum_address(token_y),
        low=low,
        high=high,
        low_normalized_tick=low_tick,
        high_normalized_tick=high_tick,
        tick_delta=high_tick - low_tick,
        valid_pool_count=valid_count,
    )


def _decision_report(decision: PoolDecision) -> dict[str, Any]:
    return {
        "tokenX": decision.token_x,
        "tokenY": decision.token_y,
        "lowPool": asdict(decision.low),
        "highPool": asdict(decision.high),
        "lowNormalizedTick": str(decision.low_normalized_tick),
        "highNormalizedTick": str(decision.high_normalized_tick),
        "tickDelta": str(decision.tick_delta),
        "validPoolCount": str(decision.valid_pool_count),
    }


def _ceil_bps(amount: int, bps: int) -> int:
    return (int(amount) * int(bps) + 9999) // 10000


def _read_flashloan_premium_bps(rpc_url: str, cache: dict[str, Any], fallback_bps: int) -> tuple[int, dict[str, Any]]:
    report = read_aave_flashloan_premium(
        rpc_url,
        pool_address=str(cache.get("aave_pool") or ""),
        fallback_percent=max(0, fallback_bps) / 100.0,
    )
    return int(report.get("premium_bps") or fallback_bps), report


def _evaluate_group(
    w3: Web3,
    group: dict[str, Any],
    *,
    quoter: str,
    usdc: str,
    amount_usdc: int,
    min_profit_usdc: int,
    premium_bps: int,
    min_liquidity: int,
    max_pools: int,
) -> dict[str, Any]:
    ux = _runtime_decision(w3, group["ux"], min_liquidity=min_liquidity, max_pools=max_pools)
    uy = _runtime_decision(w3, group["uy"], min_liquidity=min_liquidity, max_pools=max_pools)
    xy = _runtime_decision(w3, group["xy"], min_liquidity=min_liquidity, max_pools=max_pools)
    if ux.token_x.lower() != usdc.lower() or uy.token_x.lower() != usdc.lower():
        raise ValueError("usdc_pair_layout_invalid")
    if ux.token_y.lower() != xy.token_x.lower() or uy.token_y.lower() != xy.token_y.lower():
        raise ValueError("forward_triangular_layout_invalid")

    fees = [ux.high.fee, xy.high.fee, uy.low.fee]
    tokens = [usdc, ux.token_y, xy.token_y, usdc]
    quoted_final = quote_v3_exact_input_path(w3, quoter, tokens, fees, amount_usdc)
    premium = _ceil_bps(amount_usdc, premium_bps)
    required_final = amount_usdc + premium + min_profit_usdc
    expected_profit = quoted_final - required_final
    return {
        "routeKey": group.get("routeKey"),
        "status": "quoted",
        "poolSelection": {
            "ux": _decision_report(ux),
            "uy": _decision_report(uy),
            "xy": _decision_report(xy),
        },
        "contractAlignedTriangularPath": {
            "tokens": tokens,
            "fees": [str(fee) for fee in fees],
            "poolUse": [
                {"leg": "USDC_to_X", "pool": ux.high.pool_address, "fee": str(ux.high.fee)},
                {"leg": "X_to_Y", "pool": xy.high.pool_address, "fee": str(xy.high.fee)},
                {"leg": "Y_to_USDC", "pool": uy.low.pool_address, "fee": str(uy.low.fee)},
            ],
        },
        "profitBasis": {
            "amountUsdc": str(amount_usdc),
            "quotedFinalUsdc": str(quoted_final),
            "premiumUsdc": str(premium),
            "minProfitUsdc": str(min_profit_usdc),
            "requiredFinalUsdc": str(required_final),
            "expectedProfitUsdc": str(expected_profit),
        },
        "profitableBeforeGas": expected_profit > 0,
    }


def build_probe_report(
    *,
    pool_cache_path: Path = DEFAULT_POOL_CACHE,
    rpc_url: str | None = None,
    x_symbol: str | None = None,
    y_symbol: str | None = None,
    amount_usdc: int = 1_000_000_000,
    min_profit_usdc: int = 1,
    fallback_premium_bps: int = 5,
    min_liquidity: int = 1,
    max_pools_per_pair: int = 5,
    route_limit: int = 25,
) -> dict[str, Any]:
    cache = _read_json(pool_cache_path)
    w3, selected_rpc = _connect_web3(rpc_url)
    block_number = int(w3.eth.block_number)
    quoter = Web3.to_checksum_address(str(cache.get("quoter") or ""))
    usdc = Web3.to_checksum_address(str(cache.get("usdc") or ""))
    premium_bps, premium_report = _read_flashloan_premium_bps(selected_rpc, cache, fallback_premium_bps)
    groups = _candidate_groups(cache, x_symbol, y_symbol)[: max(1, int(route_limit))]
    slot_checks = [
        {
            "routeKey": group.get("routeKey"),
            "requiredTradeSpecs": "4",
            "requiredPoolSlots": str(4 * max_pools_per_pair),
            "effectivePoolCount": str(
                sum(len(_pool_addresses(group[key], max_pools=max_pools_per_pair)) for key in ("ux", "uy", "xy", "yx") if isinstance(group.get(key), dict))
            ),
            "allTradesHaveFiveEffectivePools": all(
                isinstance(group.get(key), dict)
                and len(_pool_addresses(group[key], max_pools=max_pools_per_pair)) >= max_pools_per_pair
                for key in ("ux", "uy", "xy", "yx")
            ),
            "trades": {
                key: _slot_report(group[key], max_pools=max_pools_per_pair)
                for key in ("ux", "uy", "xy", "yx")
                if isinstance(group.get(key), dict)
            },
        }
        for group in groups
    ]
    evaluations: list[dict[str, Any]] = []
    for group in groups:
        try:
            evaluations.append(
                _evaluate_group(
                    w3,
                    group,
                    quoter=quoter,
                    usdc=usdc,
                    amount_usdc=amount_usdc,
                    min_profit_usdc=min_profit_usdc,
                    premium_bps=premium_bps,
                    min_liquidity=min_liquidity,
                    max_pools=max_pools_per_pair,
                )
            )
        except Exception as exc:
            evaluations.append({"routeKey": group.get("routeKey"), "status": "blocked", "error": str(exc)})

    quoted = [item for item in evaluations if item.get("status") == "quoted"]
    selected = max(
        quoted,
        key=lambda item: int(item["profitBasis"]["expectedProfitUsdc"]),
        default=None,
    )
    return {
        "generatedAt": _utc_now_iso(),
        "mode": "avalanche_v3_pool_arbitrage_probe",
        "safety": {
            "loadsSigner": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "poolCacheIsDiscoveryOnly": True,
            "currentQuotesFromQuoter": True,
        },
        "network": "avalanche",
        "chainId": "43114",
        "rpcUrl": selected_rpc,
        "blockNumber": str(block_number),
        "poolCache": {
            "path": str(pool_cache_path),
            "cacheBlockNumber": str(cache.get("block_number") or ""),
            "fetchedAt": str(cache.get("fetched_at") or ""),
            "factory": str(cache.get("factory") or ""),
            "router": str(cache.get("router") or ""),
            "quoter": str(cache.get("quoter") or ""),
            "usdc": str(cache.get("usdc") or ""),
        },
        "config": {
            "amountUsdc": str(amount_usdc),
            "minProfitUsdc": str(min_profit_usdc),
            "premiumBps": str(premium_bps),
            "minLiquidity": str(min_liquidity),
            "maxPoolsPerPair": str(max_pools_per_pair),
            "routeLimit": str(route_limit),
            "xSymbol": x_symbol or "",
            "ySymbol": y_symbol or "",
        },
        "flashloanPremium": premium_report,
        "evaluatedRouteCount": str(len(evaluations)),
        "quotedRouteCount": str(len(quoted)),
        "poolSlotCompleteness": {
            "contractMaxOrderedTradeInputs": "4",
            "contractMaxPoolsPerTrade": "5",
            "contractPoolSlotsPerCall": "20",
            "source": "runtime/cache/avalanche_l1_pool_static_table.json full pool table; refreshed quotes require current RPC/Quoter",
            "routes": slot_checks,
        },
        "selected": selected,
        "profitableBeforeGas": bool(selected and int(selected["profitBasis"]["expectedProfitUsdc"]) > 0),
        "netProfitVerified": False,
        "blockedReasons": [
            "contract_preview_static_call_not_run",
            "gas_cost_not_subtracted",
            "broadcast_forbidden",
        ],
        "evaluations": evaluations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-cache", type=Path, default=DEFAULT_POOL_CACHE)
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--x-symbol", default="")
    parser.add_argument("--y-symbol", default="")
    parser.add_argument("--amount-usdc", type=int, default=1_000_000_000)
    parser.add_argument("--min-profit-usdc", type=int, default=1)
    parser.add_argument("--fallback-premium-bps", type=int, default=5)
    parser.add_argument("--min-liquidity", type=int, default=1)
    parser.add_argument("--max-pools-per-pair", type=int, default=5)
    parser.add_argument("--route-limit", type=int, default=25)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    load_env_files(__file__)
    args = parse_args()
    report = build_probe_report(
        pool_cache_path=args.pool_cache,
        rpc_url=args.rpc_url or None,
        x_symbol=args.x_symbol or None,
        y_symbol=args.y_symbol or None,
        amount_usdc=max(1, args.amount_usdc),
        min_profit_usdc=max(0, args.min_profit_usdc),
        fallback_premium_bps=max(0, args.fallback_premium_bps),
        min_liquidity=max(1, args.min_liquidity),
        max_pools_per_pair=max(2, args.max_pools_per_pair),
        route_limit=max(1, args.route_limit),
    )
    _write_json_atomic(args.output, report)
    summary = {
        "ok": True,
        "output": str(args.output),
        "blockNumber": report["blockNumber"],
        "evaluatedRouteCount": report["evaluatedRouteCount"],
        "quotedRouteCount": report["quotedRouteCount"],
        "profitableBeforeGas": report["profitableBeforeGas"],
        "selected": report["selected"],
        "blockedReasons": report["blockedReasons"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
