"""Export current pool quotes for the route that triggered the latest signal.

This is a local-only evidence tool. It reads the latest signal route, maps it to
the 4 RuntimeTradeSpec slots used by UnifiedFlashLoanMevExecutor, and exports a
5-slot pool quote matrix for each trade. Empty slots are reported explicitly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIGNAL_PATH = SRC_ROOT / "runtime" / "state" / "latest_unified_data_simulation.json"
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_signal_pool_quotes.json"
MAX_POOLS_PER_TRADE = 5

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from core.sensitive_data import redact_sensitive_text
from bot.execution.uniswap_v3 import (
    normalized_v3_tick,
    quote_v3_exact_input_path,
    quote_v3_exact_input_single,
    snapshot_v3_pool,
)
from market.observer_common import DEFAULT_RPC, parse_rpc_urls


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _base_symbol(symbol: Any) -> str:
    value = str(symbol or "").strip().upper()
    for suffix in ("USDT", "USDC"):
        if value.endswith(suffix) and value not in {"USDT", "USDC"}:
            return value[: -len(suffix)]
    return value


def _symbol_aliases(symbol: Any) -> set[str]:
    value = _base_symbol(symbol)
    if not value:
        return set()
    aliases = {value}
    if "." in value:
        aliases.add(value.split(".", 1)[0])
    wrapped = {
        "AVAX": {"WAVAX"},
        "WAVAX": {"AVAX"},
        "BTC": {"BTC.B"},
        "BTC.B": {"BTC"},
        "WBTC": {"WBTC.E"},
        "WBTC.E": {"WBTC"},
        "ETH": {"WETH", "WETH.E"},
        "WETH": {"ETH", "WETH.E"},
        "WETH.E": {"ETH", "WETH"},
    }
    aliases.update(wrapped.get(value, set()))
    return aliases


def _entry_symbols(entry: dict[str, Any]) -> tuple[set[str], set[str]]:
    left = entry.get("tokenX_symbol") or entry.get("token_x_symbol") or entry.get("token0_symbol")
    right = entry.get("tokenY_symbol") or entry.get("token_y_symbol") or entry.get("token1_symbol")
    return _symbol_aliases(left), _symbol_aliases(right)


def _entry_tokens(entry: dict[str, Any]) -> tuple[str, str]:
    return (
        str(entry.get("tokenX") or entry.get("token_x") or entry.get("token0") or "").strip(),
        str(entry.get("tokenY") or entry.get("token_y") or entry.get("token1") or "").strip(),
    )


def _oriented_entry(cache: dict[str, Any], token_x_symbol: str, token_y_symbol: str) -> dict[str, Any] | None:
    wanted_x = _symbol_aliases(token_x_symbol)
    wanted_y = _symbol_aliases(token_y_symbol)
    for entry in cache.get("pools") or []:
        if not isinstance(entry, dict):
            continue
        left_symbols, right_symbols = _entry_symbols(entry)
        same_order = bool(wanted_x & left_symbols and wanted_y & right_symbols)
        reverse_order = bool(wanted_x & right_symbols and wanted_y & left_symbols)
        if not same_order and not reverse_order:
            continue
        entry_x, entry_y = _entry_tokens(entry)
        if not entry_x or not entry_y:
            continue
        oriented = dict(entry)
        oriented["tokenX"] = entry_x if same_order else entry_y
        oriented["tokenY"] = entry_y if same_order else entry_x
        oriented["tokenX_symbol"] = _base_symbol(token_x_symbol)
        oriented["tokenY_symbol"] = _base_symbol(token_y_symbol)
        return oriented
    return None


def _pool_rows(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    rows = entry.get("preferredPools") if isinstance(entry.get("preferredPools"), list) else None
    if not rows:
        rows = entry.get("qualityPools") if isinstance(entry.get("qualityPools"), list) else None
    if not rows:
        rows = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
    is_quality_annotated = any(
        isinstance(row, dict) and str(row.get("quality") or "").strip()
        for row in (
            entry.get("preferredPools")
            if isinstance(entry.get("preferredPools"), list)
            else entry.get("qualityPools")
            if isinstance(entry.get("qualityPools"), list)
            else entry.get("pools")
            if isinstance(entry.get("pools"), list)
            else []
        )
    )
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("adapterKind", row.get("adapter_kind", 1)) or 0) != 1:
            continue
        if (
            is_quality_annotated
            and not isinstance(entry.get("preferredPools"), list)
            and str(row.get("quality") or "").lower() not in {"pass", ""}
        ):
            continue
        pool = str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()
        if not pool:
            continue
        result.append(row)
        if len(result) >= MAX_POOLS_PER_TRADE:
            break
    return result


def _all_effective_pool_rows(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        return []
    rows = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("adapterKind", row.get("adapter_kind", 1)) or 0) != 1:
            continue
        pool = str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()
        if pool:
            result.append(row)
    return result


def _route_path_from_signal(signal: dict[str, Any]) -> list[str]:
    intent = signal.get("intentTradeDraft") if isinstance(signal.get("intentTradeDraft"), dict) else {}
    path = intent.get("route_path") if isinstance(intent, dict) else None
    if isinstance(path, list) and len(path) >= 4:
        return [_base_symbol(item) for item in path[:4]]
    protocol = intent.get("direct_onchain_protocol") if isinstance(intent.get("direct_onchain_protocol"), dict) else {}
    path = protocol.get("route_path")
    if isinstance(path, list) and len(path) >= 4:
        return [_base_symbol(item) for item in path[:4]]
    simulation = signal.get("paperSimulation") if isinstance(signal.get("paperSimulation"), dict) else {}
    path = simulation.get("route_symbols")
    if isinstance(path, list) and len(path) >= 4:
        return [_base_symbol(item) for item in path[:4]]
    raise ValueError("latest signal does not contain a 4-token route path")


def _rpc_candidates(rpc_url: str = "") -> list[str]:
    candidates = [rpc_url.strip()] if rpc_url.strip() else []
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


def _connect_if_needed(needs_rpc: bool, rpc_url: str = "") -> tuple[Web3 | None, str, str]:
    if not needs_rpc:
        return None, "", "not_required_no_candidate_pools"
    errors: list[str] = []
    for candidate in _rpc_candidates(rpc_url):
        try:
            w3 = Web3(Web3.HTTPProvider(candidate, request_kwargs={"timeout": 12}))
            if int(w3.eth.chain_id) != 43114:
                errors.append("not_avalanche_chain")
                continue
            return w3, candidate, ""
        except Exception as exc:
            errors.append(redact_sensitive_text(exc))
    return None, "", "; ".join(errors[:3]) or "rpc_unavailable"


def _slot_quote(
    w3: Web3 | None,
    quoter: str,
    entry: dict[str, Any],
    pool_row: dict[str, Any],
    *,
    usdc_amount: int,
) -> dict[str, Any]:
    pool = str(pool_row.get("pool") or pool_row.get("pool_address") or pool_row.get("address") or "")
    report: dict[str, Any] = {
        "adapterKind": "1",
        "pool": pool,
        "cacheFee": str(pool_row.get("fee") or ""),
        "cacheLiquidity": str(pool_row.get("liquidity") or ""),
        "cacheTick": str(pool_row.get("tick") or ""),
        "quoteStatus": "not_run",
    }
    if w3 is None:
        report["quoteStatus"] = "rpc_unavailable"
        return report
    token_x, token_y = Web3.to_checksum_address(entry["tokenX"]), Web3.to_checksum_address(entry["tokenY"])
    try:
        snapshot = snapshot_v3_pool(w3, pool)
        amount_x = usdc_amount if token_x.lower() == Web3.to_checksum_address(str(entry["tokenX"])).lower() and _base_symbol(entry.get("tokenX_symbol")) == "USDC" else 10 ** int(pool_row.get("token0_decimals") if snapshot.token0.lower() == token_x.lower() else pool_row.get("token1_decimals") or 18)
        amount_y = usdc_amount if _base_symbol(entry.get("tokenY_symbol")) == "USDC" else 10 ** int(pool_row.get("token1_decimals") if snapshot.token1.lower() == token_y.lower() else pool_row.get("token0_decimals") or 18)
        x_to_y, _, _, gas_xy = quote_v3_exact_input_single(w3, quoter, token_x, token_y, snapshot.fee, amount_x)
        y_to_x, _, _, gas_yx = quote_v3_exact_input_single(w3, quoter, token_y, token_x, snapshot.fee, amount_y)
        report.update(
            {
                "quoteStatus": "quoted",
                "snapshot": asdict(snapshot),
                "normalizedTickTokenXTokenY": str(normalized_v3_tick(snapshot, token_x, token_y)),
                "quoteTokenXToTokenY": {
                    "tokenInSymbol": _base_symbol(entry.get("tokenX_symbol")),
                    "tokenOutSymbol": _base_symbol(entry.get("tokenY_symbol")),
                    "amountIn": str(amount_x),
                    "amountOut": str(x_to_y),
                    "quoterGasEstimate": str(gas_xy),
                },
                "quoteTokenYToTokenX": {
                    "tokenInSymbol": _base_symbol(entry.get("tokenY_symbol")),
                    "tokenOutSymbol": _base_symbol(entry.get("tokenX_symbol")),
                    "amountIn": str(amount_y),
                    "amountOut": str(y_to_x),
                    "quoterGasEstimate": str(gas_yx),
                },
            }
        )
    except Exception as exc:
        report["quoteStatus"] = "quote_failed"
        report["error"] = redact_sensitive_text(exc)
    return report


def _trade_quote_matrix(
    w3: Web3 | None,
    cache: dict[str, Any],
    quoter: str,
    *,
    label: str,
    token_x_symbol: str,
    token_y_symbol: str,
    usdc_amount: int,
) -> dict[str, Any]:
    entry = _oriented_entry(cache, token_x_symbol, token_y_symbol)
    rows = _pool_rows(entry)
    all_rows = _all_effective_pool_rows(entry)
    quality_rows = [
        row for row in all_rows
        if str(row.get("quality") or "").lower() == "pass"
    ]
    has_quality_annotations = any(str(row.get("quality") or "").strip() for row in all_rows)
    slots = []
    for index in range(MAX_POOLS_PER_TRADE):
        if index >= len(rows) or entry is None:
            slots.append(
                {
                    "slotIndex": str(index),
                    "adapterKind": "0",
                    "pool": "",
                    "quoteStatus": "empty_slot",
                    "reason": (
                        "pair_not_in_cache"
                        if entry is None
                        else "no_quality_pass_pools_for_static_table"
                        if has_quality_annotations and not rows
                        else "no_more_selected_pools"
                    ),
                }
            )
            continue
        slot = _slot_quote(w3, quoter, entry, rows[index], usdc_amount=usdc_amount)
        slot["slotIndex"] = str(index)
        slots.append(slot)
    return {
        "label": label,
        "tokenXSymbol": _base_symbol(token_x_symbol),
        "tokenYSymbol": _base_symbol(token_y_symbol),
        "tokenX": str((entry or {}).get("tokenX") or ""),
        "tokenY": str((entry or {}).get("tokenY") or ""),
        "pairFoundInCache": entry is not None,
        "staticPoolCount": str(len(all_rows)),
        "qualityPoolCount": str(len(quality_rows)) if has_quality_annotations else "",
        "effectivePoolCount": str(len(rows)),
        "requiredPoolSlots": str(MAX_POOLS_PER_TRADE),
        "hasFiveEffectivePools": len(rows) >= MAX_POOLS_PER_TRADE,
        "slots": slots,
    }


def _quote_trade_direction(
    w3: Web3 | None,
    quoter: str,
    trade: dict[str, Any],
    *,
    token_in_symbol: str,
    token_out_symbol: str,
    amount_in: int,
    block_identifier: int | None = None,
) -> list[dict[str, Any]]:
    if w3 is None:
        return []
    token_x = Web3.to_checksum_address(str(trade.get("tokenX") or ""))
    token_y = Web3.to_checksum_address(str(trade.get("tokenY") or ""))
    x_symbol = _base_symbol(trade.get("tokenXSymbol"))
    y_symbol = _base_symbol(trade.get("tokenYSymbol"))
    if _base_symbol(token_in_symbol) == x_symbol and _base_symbol(token_out_symbol) == y_symbol:
        token_in, token_out = token_x, token_y
    elif _base_symbol(token_in_symbol) == y_symbol and _base_symbol(token_out_symbol) == x_symbol:
        token_in, token_out = token_y, token_x
    else:
        raise ValueError(f"trade direction does not match {token_in_symbol}->{token_out_symbol}")

    outputs: list[dict[str, Any]] = []
    for slot in trade.get("slots") or []:
        if not isinstance(slot, dict) or slot.get("quoteStatus") != "quoted":
            continue
        pool = str(slot.get("pool") or "").strip()
        snapshot = slot.get("snapshot") if isinstance(slot.get("snapshot"), dict) else {}
        fee = int(snapshot.get("fee") or slot.get("cacheFee") or 0)
        if not pool or fee <= 0:
            continue
        try:
            amount_out, sqrt_after, ticks_crossed, gas_estimate = quote_v3_exact_input_single(
                w3,
                quoter,
                token_in,
                token_out,
                fee,
                int(amount_in),
                block_identifier=block_identifier,
            )
            quote = {
                "tokenInSymbol": _base_symbol(token_in_symbol),
                "tokenOutSymbol": _base_symbol(token_out_symbol),
                "amountIn": str(amount_in),
                "amountOut": str(amount_out),
                "quoterGasEstimate": str(gas_estimate),
                "sqrtPriceX96After": str(sqrt_after),
                "initializedTicksCrossed": str(ticks_crossed),
                "quoteMode": "chained_route",
                "quoteBlockNumber": str(block_identifier or ""),
            }
            slot["chainedQuote"] = quote
            outputs.append({"slot": slot, "amountOut": int(amount_out)})
        except Exception as exc:
            slot["chainedQuoteStatus"] = "quote_failed"
            slot["chainedQuoteError"] = redact_sensitive_text(exc)
    return outputs


def _chained_route_quotes(
    w3: Web3 | None,
    quoter: str,
    trades: list[dict[str, Any]],
    *,
    route_path: list[str],
    usdc_amount: int,
    block_identifier: int | None = None,
) -> dict[str, Any]:
    if len(route_path) < 4 or len(trades) < 3:
        return {"status": "blocked", "reason": "route_trades_missing"}
    if w3 is None:
        return {"status": "blocked", "reason": "rpc_unavailable"}

    x_symbol, y_symbol = route_path[1], route_path[2]
    ux_trade = trades[0]
    xy_trade = trades[2]
    yc_trade = trades[1]
    ux_quotes = _quote_trade_direction(
        w3,
        quoter,
        ux_trade,
        token_in_symbol="USDC",
        token_out_symbol=x_symbol,
        amount_in=usdc_amount,
        block_identifier=block_identifier,
    )
    if not ux_quotes:
        return {"status": "blocked", "reason": "u_x_no_quoted_pools"}
    ux_best = max(ux_quotes, key=lambda item: item["amountOut"])
    xy_input = int(ux_best["amountOut"])

    xy_quotes = _quote_trade_direction(
        w3,
        quoter,
        xy_trade,
        token_in_symbol=x_symbol,
        token_out_symbol=y_symbol,
        amount_in=xy_input,
        block_identifier=block_identifier,
    )
    if not xy_quotes:
        return {
            "status": "blocked",
            "reason": "x_y_no_quoted_pools",
            "uXInput": str(usdc_amount),
            "uXOutput": str(xy_input),
        }
    xy_best = max(xy_quotes, key=lambda item: item["amountOut"])
    yc_input = int(xy_best["amountOut"])

    yc_quotes = _quote_trade_direction(
        w3,
        quoter,
        yc_trade,
        token_in_symbol=y_symbol,
        token_out_symbol="USDC",
        amount_in=yc_input,
        block_identifier=block_identifier,
    )
    if not yc_quotes:
        return {
            "status": "blocked",
            "reason": "y_c_no_quoted_pools",
            "uXInput": str(usdc_amount),
            "uXOutput": str(xy_input),
            "xYInput": str(xy_input),
            "xYOutput": str(yc_input),
        }
    yc_best = max(yc_quotes, key=lambda item: item["amountOut"])
    return {
        "status": "quoted",
        "quoteBlockNumber": str(block_identifier or ""),
        "uX": {
            "inputSymbol": "USDC",
            "outputSymbol": x_symbol,
            "amountIn": str(usdc_amount),
            "recommendedPool": str(ux_best["slot"].get("pool") or ""),
            "recommendedAmountOut": str(xy_input),
        },
        "xY": {
            "inputSymbol": x_symbol,
            "outputSymbol": y_symbol,
            "amountIn": str(xy_input),
            "recommendedPool": str(xy_best["slot"].get("pool") or ""),
            "recommendedAmountOut": str(yc_input),
        },
        "yC": {
            "inputSymbol": y_symbol,
            "outputSymbol": "USDC",
            "amountIn": str(yc_input),
            "recommendedPool": str(yc_best["slot"].get("pool") or ""),
            "recommendedAmountOut": str(yc_best["amountOut"]),
        },
    }


def _selected_pool(slots: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
    quoted = [slot for slot in slots if slot.get("quoteStatus") == "quoted"]
    if not quoted:
        return None
    key = lambda slot: int(slot.get("normalizedTickTokenXTokenY") or 0)
    return max(quoted, key=key) if side == "high" else min(quoted, key=key)


def _contract_path_profit(
    trades: list[dict[str, Any]],
    quoter: str,
    w3: Web3 | None,
    usdc_amount: int,
    min_profit_usdc: int,
    premium_bps: int,
    *,
    preferred_pools: dict[str, Any] | None = None,
    block_identifier: int | None = None,
) -> dict[str, Any]:
    missing = [
        trade["label"]
        for trade in trades
        if not trade.get("pairFoundInCache") or int(trade.get("effectivePoolCount") or 0) == 0
    ]
    if missing:
        return {
            "status": "blocked",
            "reason": "signal_pair_pools_not_found",
            "missingTrades": missing,
        }
    if w3 is None:
        return {"status": "blocked", "reason": "rpc_unavailable"}
    ux, uy, xy = trades[0], trades[1], trades[2]
    preferred_pools = preferred_pools if isinstance(preferred_pools, dict) else {}

    def preferred_slot(trade: dict[str, Any], key: str) -> dict[str, Any] | None:
        wanted = str(preferred_pools.get(key) or "").lower()
        if wanted:
            for slot in trade.get("slots") or []:
                if str(slot.get("pool") or "").lower() == wanted:
                    return slot
        return None

    high_ux = preferred_slot(ux, "usdcToX") or _selected_pool(ux["slots"], "high")
    high_xy = preferred_slot(xy, "xToY") or _selected_pool(xy["slots"], "high")
    low_uy = preferred_slot(uy, "yToUsdc") or _selected_pool(uy["slots"], "low")
    if not high_ux or not high_xy or not low_uy:
        return {"status": "blocked", "reason": "not_enough_quoted_pools_for_contract_path"}
    try:
        tokens = [ux["tokenX"], ux["tokenY"], xy["tokenY"], uy["tokenX"]]
        fees = [
            int((high_ux.get("snapshot") or {}).get("fee") or 0),
            int((high_xy.get("snapshot") or {}).get("fee") or 0),
            int((low_uy.get("snapshot") or {}).get("fee") or 0),
        ]
        quoted_final = quote_v3_exact_input_path(
            w3,
            quoter,
            tokens,
            fees,
            usdc_amount,
            block_identifier=block_identifier,
        )
        premium = (usdc_amount * max(0, premium_bps) + 9999) // 10000
        required = usdc_amount + premium + min_profit_usdc
        return {
            "status": "quoted",
            "tokens": tokens,
            "fees": [str(item) for item in fees],
            "selectedPools": {
                "usdcToX": high_ux["pool"],
                "xToY": high_xy["pool"],
                "yToUsdc": low_uy["pool"],
            },
            "amountUsdc": str(usdc_amount),
            "quotedFinalUsdc": str(quoted_final),
            "premiumUsdc": str(premium),
            "minProfitUsdc": str(min_profit_usdc),
            "requiredFinalUsdc": str(required),
            "expectedProfitUsdc": str(quoted_final - required),
            "profitableBeforeGas": quoted_final > required,
        }
    except Exception as exc:
        return {"status": "quote_failed", "error": redact_sensitive_text(exc)}


def build_report(
    *,
    signal_path: Path,
    pool_cache_path: Path,
    rpc_url: str = "",
    usdc_amount: int = 1_000_000_000,
    min_profit_usdc: int = 1,
    premium_bps: int = 5,
) -> dict[str, Any]:
    signal = _read_json(signal_path)
    cache = _read_json(pool_cache_path)
    route_path = _route_path_from_signal(signal)
    if len(route_path) < 4 or route_path[0] != "USDC" or route_path[-1] != "USDC":
        raise ValueError(f"unsupported signal route path: {route_path}")
    x_symbol = route_path[1]
    y_symbol = route_path[2]
    trade_specs = [
        ("ux_usdc_to_x", "USDC", x_symbol),
        ("uy_usdc_to_y", "USDC", y_symbol),
        ("xy_x_to_y", x_symbol, y_symbol),
        ("yx_y_to_x_fallback", y_symbol, x_symbol),
    ]
    entries = [_oriented_entry(cache, left, right) for _, left, right in trade_specs]
    needs_rpc = any(_pool_rows(entry) for entry in entries)
    w3, selected_rpc, rpc_error = _connect_if_needed(needs_rpc, rpc_url)
    block_number = None
    if w3 is not None:
        block_number = int(w3.eth.block_number)
    quoter = Web3.to_checksum_address(str(cache.get("quoter") or "0xbe0F5544EC67e9B3b2D979aaA43f18Fd87E6257F"))
    trades = [
        _trade_quote_matrix(
            w3,
            cache,
            quoter,
            label=label,
            token_x_symbol=left,
            token_y_symbol=right,
            usdc_amount=usdc_amount,
        )
        for label, left, right in trade_specs
    ]
    chained = _chained_route_quotes(
        w3,
        quoter,
        trades,
        route_path=route_path,
        usdc_amount=usdc_amount,
        block_identifier=block_number,
    )
    preferred_pools = {}
    if chained.get("status") == "quoted":
        preferred_pools = {
            "usdcToX": (chained.get("uX") or {}).get("recommendedPool"),
            "xToY": (chained.get("xY") or {}).get("recommendedPool"),
            "yToUsdc": (chained.get("yC") or {}).get("recommendedPool"),
        }
    return {
        "generatedAt": _utc_now_iso(),
        "mode": "latest_signal_pool_quote_matrix",
        "safety": {
            "loadsSigner": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "deploysContract": False,
        },
        "signal": {
            "inputObservedAt": signal.get("inputObservedAt"),
            "routePath": route_path,
            "xSymbol": x_symbol,
            "ySymbol": y_symbol,
            "paperSignalProfitUsdc": (signal.get("profitEstimate") or {}).get("paperSignalProfitUsdc"),
        },
        "poolSource": {
            "cachePath": str(pool_cache_path),
            "sourceMethod": "factory.getPool(tokenA,tokenB,feeTier) discovery cache; current quotes use pool.slot0/liquidity and Quoter quoteExactInput",
            "network": str(cache.get("network") or "avalanche"),
            "chainId": str(cache.get("chain_id") or 43114),
            "factory": str(cache.get("factory") or ""),
            "router": str(cache.get("router") or ""),
            "quoter": str(cache.get("quoter") or ""),
            "cacheBlockNumber": str(cache.get("block_number") or ""),
            "cacheFetchedAt": str(cache.get("fetched_at") or ""),
        },
        "quoteContext": {
            "rpcUrl": selected_rpc,
            "rpcError": rpc_error,
            "blockNumber": str(block_number or ""),
            "usdcAmount": str(usdc_amount),
            "minProfitUsdc": str(min_profit_usdc),
            "premiumBps": str(premium_bps),
            "quoteMode": "independent_probe_plus_chained_route",
        },
        "contractCallShape": {
            "runtimeTradeSpecs": "4",
            "poolsPerTrade": str(MAX_POOLS_PER_TRADE),
            "poolSlotsPerCall": str(4 * MAX_POOLS_PER_TRADE),
        },
        "trades": trades,
        "chainedRoute": chained,
        "allTradesHaveFiveEffectivePools": all(item["hasFiveEffectivePools"] for item in trades),
        "effectivePoolSlots": str(sum(int(item["effectivePoolCount"]) for item in trades)),
        "contractPathProfit": _contract_path_profit(
            trades,
            quoter,
            w3,
            usdc_amount,
            min_profit_usdc,
            premium_bps,
            preferred_pools=preferred_pools,
            block_identifier=block_number,
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal", type=Path, default=DEFAULT_SIGNAL_PATH)
    parser.add_argument("--pool-cache", type=Path, default=DEFAULT_POOL_CACHE)
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--usdc-amount", type=int, default=1_000_000_000)
    parser.add_argument("--min-profit-usdc", type=int, default=1)
    parser.add_argument("--premium-bps", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    load_env_files(__file__)
    args = parse_args()
    report = build_report(
        signal_path=args.signal,
        pool_cache_path=args.pool_cache,
        rpc_url=args.rpc_url,
        usdc_amount=max(1, args.usdc_amount),
        min_profit_usdc=max(0, args.min_profit_usdc),
        premium_bps=max(0, args.premium_bps),
    )
    _write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "routePath": report["signal"]["routePath"],
                "effectivePoolSlots": report["effectivePoolSlots"],
                "allTradesHaveFiveEffectivePools": report["allTradesHaveFiveEffectivePools"],
                "contractPathProfit": report["contractPathProfit"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
