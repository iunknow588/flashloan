"""Assemble contract parameters for a UI-selected arbitrage target.

This local diagnostic stops before RPC, Quoter, signing, deployment, or
broadcast. It answers one question: can the target selected by the UI be turned
into the RuntimeTradeSpec/params shape required by UnifiedFlashLoanMevExecutor?
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_ui_target_contract_params.json"
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from intent_trade.direct import build_triangular_onchain_intent_trade
from intent_trade import direct_impl as direct


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _base_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    for suffix in ("USDT", "USDC"):
        if text.endswith(suffix) and text not in {"USDT", "USDC"}:
            return text[: -len(suffix)]
    return text


def _route_path(args: argparse.Namespace) -> list[str]:
    if args.route_path:
        path = [item for item in args.route_path.replace(",", "->").split("->") if item.strip()]
        if len(path) >= 4:
            return [_base_symbol(item) for item in path[:4]]
    x_symbol = _base_symbol(args.x_symbol)
    y_symbol = _base_symbol(args.y_symbol)
    if not x_symbol or not y_symbol:
        raise ValueError("--route-path or both --x-symbol/--y-symbol are required")
    return ["USDC", x_symbol, y_symbol, "USDC"]


def _pool_slot_report(trade: dict[str, Any]) -> dict[str, Any]:
    pools = trade.get("pools") if isinstance(trade.get("pools"), list) else []
    effective = [
        pool
        for pool in pools
        if int(pool.get("adapterKind", pool.get("adapter_kind", 0)) or 0) == 1
        and str(pool.get("pool") or "").strip()
    ]
    return {
        "tradeIndex": str(trade.get("tradeIndex", "")),
        "strategyStatus": str(trade.get("strategyStatus", trade.get("strategy_status", ""))),
        "strategyStage": str(trade.get("strategyStage", trade.get("strategy_stage", ""))),
        "routeKey": str(trade.get("routeKey", trade.get("route_key", ""))),
        "routeSymbols": trade.get("routeSymbols") or trade.get("route_symbols") or [],
        "tokenX": str(trade.get("tokenX") or ""),
        "tokenY": str(trade.get("tokenY") or ""),
        "poolSlotCount": str(len(pools)),
        "effectivePoolCount": str(len(effective)),
        "hasFivePoolSlots": len(pools) == 5,
        "hasFiveEffectivePools": len(effective) == 5,
        "pools": [
            {
                "slotIndex": str(index),
                "adapterKind": str(pool.get("adapterKind", pool.get("adapter_kind", 0))),
                "pool": str(pool.get("pool") or ""),
                "effective": bool(
                    int(pool.get("adapterKind", pool.get("adapter_kind", 0)) or 0) == 1
                    and str(pool.get("pool") or "").strip()
                ),
            }
            for index, pool in enumerate(pools)
        ],
    }


def _direct_target_route_group_from_static_table(
    *,
    cache: dict[str, Any],
    x_symbol: str,
    y_symbol: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    missing: list[dict[str, Any]] = []

    def pair(left: str, right: str) -> tuple[str, str, list[dict[str, Any]]]:
        token_x, token_y, pools = direct._cache_pool_candidates_for_pair(cache, left, right)
        if not token_x or not token_y or not pools:
            missing.append({"pair": f"{left}:{right}", "reason": "pair_not_found_in_static_table"})
        return token_x, token_y, pools

    ux_x, ux_y, ux_pools = pair("USDC", x_symbol)
    uy_x, uy_y, uy_pools = pair("USDC", y_symbol)
    xy_x, xy_y, xy_pools = pair(x_symbol, y_symbol)
    yx_x, yx_y, yx_pools = pair(y_symbol, x_symbol)
    if missing:
        return None, missing
    route_key = f"{x_symbol}:{y_symbol}"
    trades = [
        direct._runtime_trade_with_metadata(
            trade_index=0,
            token_x=ux_x,
            token_y=ux_y,
            pools=ux_pools,
            strategy_status=1,
            strategy_stage="status1_usdc_to_x_cross_pool",
            route_symbols=["USDC", x_symbol, "USDC"],
            route_key=route_key,
        ),
        direct._runtime_trade_with_metadata(
            trade_index=1,
            token_x=uy_x,
            token_y=uy_y,
            pools=uy_pools,
            strategy_status=2,
            strategy_stage="status2_usdc_to_y_cross_pool",
            route_symbols=["USDC", y_symbol, "USDC"],
            route_key=route_key,
        ),
        direct._runtime_trade_with_metadata(
            trade_index=2,
            token_x=xy_x,
            token_y=xy_y,
            pools=xy_pools,
            strategy_status=3,
            strategy_stage="status3_xy_cross_pool_or_triangular",
            route_symbols=["USDC", x_symbol, y_symbol, "USDC"],
            route_key=route_key,
        ),
        direct._runtime_trade_with_metadata(
            trade_index=3,
            token_x=yx_x,
            token_y=yx_y,
            pools=yx_pools,
            strategy_status=5,
            strategy_stage="status5_reverse_triangular_fallback",
            route_symbols=["USDC", y_symbol, x_symbol, "USDC"],
            route_key=route_key,
        ),
    ]
    return {
        "routeKey": route_key,
        "routeKind": "ui_target_static_table_direct",
        "score": 0.0,
        "trades": trades,
    }, []


def _config_status(protocol: dict[str, Any]) -> dict[str, Any]:
    executor = str(
        protocol.get("unified_executor_address")
        or protocol.get("executor_address")
        or os.getenv("UNIFIED_EXECUTOR_ADDRESS", "")
        or os.getenv("TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS", "")
    ).strip()
    rpc = os.getenv("AVALANCHE_RPC_URL", "").strip() or os.getenv("AVALANCHE_RPC", "").strip()
    return {
        "network": str(protocol.get("network") or ""),
        "chainId": str(protocol.get("chain_id") or ""),
        "executorAddressConfigured": bool(executor),
        "executorAddress": executor,
        "rpcConfigured": bool(rpc),
        "broadcastEnabled": str(
            os.getenv("TRIANGULAR_DIRECT_BROADCAST_ENABLED")
            or os.getenv("UNIFIED_EXECUTOR_BROADCAST_ENABLED")
            or "false"
        ),
    }


def build_report(route_path: list[str], *, expected_profit: str = "0", pool_cache: Path = DEFAULT_POOL_CACHE) -> dict[str, Any]:
    if len(route_path) < 4 or route_path[0] != "USDC" or route_path[-1] != "USDC":
        raise ValueError("route path must be USDC -> X -> Y -> USDC")
    x_symbol = _base_symbol(route_path[1])
    y_symbol = _base_symbol(route_path[2])
    link_name = "->".join(route_path)
    os.environ["PINAX_POOL_DISCOVERY_CACHE_FILE"] = str(pool_cache)
    os.environ["TRIANGULAR_RUNTIME_POOL_CACHE_FILE"] = str(pool_cache)
    os.environ["COW_FLASHLOAN_INTENT_NETWORK"] = "avalanche"
    os.environ["TRIANGULAR_DIRECT_NETWORK"] = "avalanche"
    intent = build_triangular_onchain_intent_trade(
        link_name,
        expected_profit,
        [{"symbol": y_symbol, "change_percent": 1.0}],
        [{"symbol": x_symbol, "change_percent": -1.0}],
    )
    protocol = intent.get("direct_onchain_protocol") if isinstance(intent.get("direct_onchain_protocol"), dict) else {}
    route_groups = protocol.get("runtime_route_groups") if isinstance(protocol.get("runtime_route_groups"), list) else []
    runtime_trades = protocol.get("runtime_trades") if isinstance(protocol.get("runtime_trades"), list) else []
    cache = direct._load_runtime_pool_cache(pool_cache)
    direct_group, direct_missing = _direct_target_route_group_from_static_table(
        cache=cache,
        x_symbol=x_symbol,
        y_symbol=y_symbol,
    )
    direct_prepared_groups: list[dict[str, Any]] = []
    direct_rejected_groups: list[dict[str, Any]] = []
    usdc_address = str(cache.get("usdc") or direct._usdc_address_for_network("avalanche", cache)).lower()
    prepared_groups, rejected_groups = direct._prepare_unified_route_groups(
        route_groups,
        usdc_address=usdc_address,
        allow_single_pair_diagnostic=False,
        allow_stable_targets=False,
        require_token_risk_metadata=False,
    )
    if direct_group is not None:
        direct_prepared_groups, direct_rejected_groups = direct._prepare_unified_route_groups(
            [direct_group],
            usdc_address=usdc_address,
            allow_single_pair_diagnostic=False,
            allow_stable_targets=True,
            require_token_risk_metadata=False,
        )
    selected_group = None
    match = protocol.get("intent_route_match") if isinstance(protocol.get("intent_route_match"), dict) else {}
    if match.get("matched"):
        for group in prepared_groups:
            if group.get("routeKey") == match.get("routeKey"):
                selected_group = group
                break
    if selected_group is None and runtime_trades:
        selected_group = {
            "routeKey": str(match.get("routeKey") or "runtime_trades"),
            "routeKind": str(match.get("routeKind") or "runtime_trades"),
            "trades": runtime_trades,
        }
    if selected_group is None and direct_prepared_groups:
        selected_group = direct_prepared_groups[0]
        runtime_trades = list(selected_group.get("trades") or [])

    abi_args: list[dict[str, Any]] = []
    abi_error = ""
    if selected_group and isinstance(selected_group.get("trades"), list):
        try:
            abi_args = [direct._runtime_trade_abi_arg(trade, Web3) for trade in selected_group["trades"]]
        except Exception as exc:
            abi_error = str(exc)

    execution_params = None
    cross_pool_params = None
    params_error = ""
    try:
        execution_params = direct._unified_execution_params(direct._runtime_execution_params(protocol, None))
        cross_pool_params = direct._unified_cross_pool_params(
            direct._runtime_cross_pool_execution_params(protocol, None, execution_params)
        )
    except Exception as exc:
        params_error = str(exc)

    trade_reports = [_pool_slot_report(trade) for trade in runtime_trades]
    blockers: list[str] = []
    direct_assembly_ok = bool(direct_prepared_groups)
    if not match.get("matched") and not direct_assembly_ok:
        blockers.append("target_route_not_matched_to_pool_cache")
    if len(runtime_trades) != 4:
        blockers.append("runtime_trade_count_not_4")
    if any(not item["hasFivePoolSlots"] for item in trade_reports):
        blockers.append("runtime_trade_pool_slot_count_not_5")
    if any(int(item["effectivePoolCount"]) == 0 for item in trade_reports) or not runtime_trades:
        blockers.append("target_pair_pools_missing")
    if any(not item["hasFiveEffectivePools"] for item in trade_reports):
        blockers.append("not_all_trades_have_5_effective_pools")
    if abi_error:
        blockers.append("runtime_trade_abi_arg_failed")
    if params_error or execution_params is None:
        blockers.append("execution_params_failed")

    return {
        "generatedAt": _utc_now_iso(),
        "mode": "ui_target_contract_param_assembly",
        "safety": {
            "loadsSigner": False,
            "usesRpc": False,
            "callsContract": False,
            "broadcastsTransaction": False,
            "deploysContract": False,
        },
        "target": {
            "routePath": route_path,
            "xSymbol": x_symbol,
            "ySymbol": y_symbol,
            "linkName": link_name,
            "expectedProfitInput": expected_profit,
        },
        "poolStaticTable": {
            "path": str(pool_cache),
            "exists": pool_cache.exists(),
        },
        "configStatus": _config_status(protocol),
        "intentReady": bool(intent.get("direct_onchain_ready")),
        "intentRouteMatch": match,
        "routeGroupCount": str(len(route_groups)),
        "preparedRouteGroupCount": str(len(prepared_groups)),
        "rejectedRouteGroups": rejected_groups,
        "directStaticTableAssembly": {
            "ok": direct_assembly_ok,
            "missingPairs": direct_missing,
            "preparedRouteGroupCount": str(len(direct_prepared_groups)),
            "rejectedRouteGroups": direct_rejected_groups,
        },
        "selectedRouteKey": str((selected_group or {}).get("routeKey") or ""),
        "runtimeTradeCount": str(len(runtime_trades)),
        "contractCallShape": {
            "function": "previewOrderedRuntimeAutoExecution",
            "runtimeTradeSpecs": str(len(runtime_trades)),
            "expectedRuntimeTradeSpecs": "4",
            "poolsPerTrade": "5",
            "poolSlots": str(sum(int(item["poolSlotCount"]) for item in trade_reports)),
            "effectivePoolSlots": str(sum(int(item["effectivePoolCount"]) for item in trade_reports)),
        },
        "runtimeTrades": trade_reports,
        "abiRuntimeTrades": abi_args,
        "abiRuntimeTradesError": abi_error,
        "executionParams": execution_params,
        "crossPoolExecutionParams": cross_pool_params,
        "executionParamsError": params_error,
        "assemblyOk": not blockers,
        "blockedReasons": blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-path", default="")
    parser.add_argument("--x-symbol", default="")
    parser.add_argument("--y-symbol", default="")
    parser.add_argument("--expected-profit", default="0")
    parser.add_argument("--pool-cache", type=Path, default=DEFAULT_POOL_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    load_env_files(__file__)
    args = parse_args()
    route_path = _route_path(args)
    pool_cache = args.pool_cache if args.pool_cache.is_absolute() else SRC_ROOT / args.pool_cache
    report = build_report(route_path, expected_profit=args.expected_profit, pool_cache=pool_cache)
    _write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "target": report["target"],
                "assemblyOk": report["assemblyOk"],
                "contractCallShape": report["contractCallShape"],
                "intentRouteMatch": report["intentRouteMatch"],
                "blockedReasons": report["blockedReasons"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
