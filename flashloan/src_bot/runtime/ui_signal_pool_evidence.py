"""Record pool-level evidence for UI-triggered Avalanche arbitrage signals."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.sensitive_data import redact_sensitive_text
from market.observer_common import write_json_atomic
from tools import probe_signal_pool_quotes as pool_quotes


SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_EVIDENCE_DIR = SRC_ROOT / "runtime" / "evidence" / "ui_signal_pool_quotes"
DEFAULT_LATEST_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_ui_signal_pool_evidence.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _safe_name(value: Any) -> str:
    text = str(value or "").strip().replace("/", "_").replace("\\", "_")
    text = text.replace(" ", "_").replace(":", "_").replace(">", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch in "._-")[:120] or "signal"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def ui_signal_pool_evidence_enabled() -> bool:
    return _env_bool("UI_SIGNAL_POOL_EVIDENCE_ENABLED", True)


def _route_paths(pair: dict[str, Any]) -> list[dict[str, Any]]:
    routes = []
    for route in pair.get("route_results") or []:
        if not isinstance(route, dict):
            continue
        path = route.get("route") or route.get("route_symbols") or route.get("path")
        if not isinstance(path, list) or len(path) < 4:
            continue
        normalized = [pool_quotes._base_symbol(item) for item in path[:4]]
        if normalized[0] != "USDC" or normalized[-1] != "USDC":
            continue
        routes.append(
            {
                "routeNo": route.get("route_no"),
                "priorityReason": route.get("priority_reason"),
                "routeDirection": route.get("route_direction"),
                "routePath": normalized,
                "quoteRequired": route.get("quote_required"),
                "poolAssemblySupported": route.get("pool_assembly_supported"),
            }
        )
    return routes


def _binance_pair_snapshot(pair: dict[str, Any]) -> dict[str, Any]:
    return {
        "pair": pair.get("pair"),
        "rank": pair.get("rank"),
        "gridRank": pair.get("grid_rank"),
        "candidateBasis": pair.get("candidate_basis"),
        "triggerSource": pair.get("trigger_source"),
        "windowSpreadPercent": pair.get("window_spread_percent"),
        "edgeHintPercent": pair.get("edge_hint_percent"),
        "quoteRequired": pair.get("quote_required"),
        "poolAssemblySupported": pair.get("pool_assembly_supported"),
        "poolAssembly": pair.get("pool_assembly"),
        "x": {
            "symbol": pair.get("x_symbol"),
            "baseSymbol": pair.get("x_base_symbol"),
            "changePercent": pair.get("x_change_percent"),
            "startPrice": pair.get("x_start_price"),
            "currentPrice": pair.get("x_current_price"),
            "endPrice": pair.get("x_end_price"),
            "startMs": pair.get("x_start_ms"),
            "endMs": pair.get("x_end_ms"),
            "priceSource": pair.get("x_price_source"),
        },
        "y": {
            "symbol": pair.get("y_symbol"),
            "baseSymbol": pair.get("y_base_symbol"),
            "changePercent": pair.get("y_change_percent"),
            "startPrice": pair.get("y_start_price"),
            "currentPrice": pair.get("y_current_price"),
            "endPrice": pair.get("y_end_price"),
            "startMs": pair.get("y_start_ms"),
            "endMs": pair.get("y_end_ms"),
            "priceSource": pair.get("y_price_source"),
        },
    }


def _route_quote_matrix(
    *,
    route_path: list[str],
    cache: dict[str, Any],
    w3: Any,
    quoter: str,
    usdc_amount: int,
    min_profit_usdc: int,
    premium_bps: int,
) -> dict[str, Any]:
    x_symbol = route_path[1]
    y_symbol = route_path[2]
    trade_specs = [
        ("ux_usdc_to_x", "USDC", x_symbol),
        ("uy_usdc_to_y", "USDC", y_symbol),
        ("xy_x_to_y", x_symbol, y_symbol),
        ("yx_y_to_x_fallback", y_symbol, x_symbol),
    ]
    trades = [
        pool_quotes._trade_quote_matrix(
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
    return {
        "routePath": route_path,
        "xSymbol": x_symbol,
        "ySymbol": y_symbol,
        "contractCallShape": {
            "runtimeTradeSpecs": "4",
            "poolsPerTrade": str(pool_quotes.MAX_POOLS_PER_TRADE),
            "poolSlotsPerCall": str(4 * pool_quotes.MAX_POOLS_PER_TRADE),
        },
        "trades": trades,
        "allTradesHaveFiveEffectivePools": all(item["hasFiveEffectivePools"] for item in trades),
        "effectivePoolSlots": str(sum(int(item["effectivePoolCount"]) for item in trades)),
        "contractPathProfit": pool_quotes._contract_path_profit(
            trades,
            quoter,
            w3,
            usdc_amount,
            min_profit_usdc,
            premium_bps,
        ),
    }


def _candidate_pairs(market_state: dict[str, Any]) -> list[dict[str, Any]]:
    return [pair for pair in market_state.get("pairs") or [] if isinstance(pair, dict)]


def build_ui_signal_pool_evidence(
    *,
    market_state: dict[str, Any],
    extremes: dict[str, Any] | None = None,
    pool_cache_path: Path = DEFAULT_POOL_CACHE,
    rpc_url: str = "",
    usdc_amount: int = 1_000_000_000,
    min_profit_usdc: int = 1,
    premium_bps: int = 5,
) -> dict[str, Any]:
    cache = json.loads(pool_cache_path.read_text(encoding="utf-8"))
    pairs = _candidate_pairs(market_state)
    route_refs = [
        (pair, route)
        for pair in pairs
        for route in _route_paths(pair)
    ]
    entries = [
        pool_quotes._oriented_entry(cache, route["routePath"][1], route["routePath"][2])
        for _, route in route_refs
    ]
    needs_rpc = any(pool_quotes._pool_rows(entry) for entry in entries)
    w3, selected_rpc, rpc_error = pool_quotes._connect_if_needed(needs_rpc, rpc_url)
    block_number = int(w3.eth.block_number) if w3 is not None else None
    quoter = pool_quotes.Web3.to_checksum_address(str(cache.get("quoter") or "0xbe0F5544EC67e9B3b2D979aaA43f18Fd87E6257F"))
    route_cache: dict[str, dict[str, Any]] = {}
    candidate_reports = []
    for pair in pairs:
        routes = []
        for route in _route_paths(pair):
            key = "->".join(route["routePath"])
            if key not in route_cache:
                try:
                    route_cache[key] = _route_quote_matrix(
                        route_path=route["routePath"],
                        cache=cache,
                        w3=w3,
                        quoter=quoter,
                        usdc_amount=usdc_amount,
                        min_profit_usdc=min_profit_usdc,
                        premium_bps=premium_bps,
                    )
                except Exception as exc:
                    route_cache[key] = {
                        "routePath": route["routePath"],
                        "status": "quote_matrix_failed",
                        "error": redact_sensitive_text(exc),
                    }
            routes.append({**route, "poolQuoteMatrix": route_cache[key]})
        candidate_reports.append(
            {
                "binanceSignal": _binance_pair_snapshot(pair),
                "routes": routes,
            }
        )

    return {
        "generatedAt": _utc_now_iso(),
        "mode": "ui_signal_pool_quote_evidence",
        "safety": {
            "loadsSigner": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "deploysContract": False,
        },
        "uiSignal": {
            "observedAt": market_state.get("observed_at") or (extremes or {}).get("observed_at"),
            "windowSeconds": market_state.get("window_seconds") or (extremes or {}).get("window_seconds"),
            "priceSource": market_state.get("price_source") or (extremes or {}).get("price_source"),
            "marketStateSource": market_state.get("market_state_source") or (extremes or {}).get("market_state_source"),
            "pairCount": str(len(pairs)),
            "candidateBasis": market_state.get("candidate_basis"),
            "quoteCandidateSource": market_state.get("quote_candidate_source"),
            "cowFilter": market_state.get("cow_filter"),
        },
        "poolSource": {
            "cachePath": str(pool_cache_path),
            "sourceMethod": "factory.getPool discovery cache; current pool prices use pool.slot0/liquidity and Quoter quoteExactInputSingle/quoteExactInput",
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
        },
        "candidateCount": str(len(candidate_reports)),
        "routeCount": str(sum(len(item["routes"]) for item in candidate_reports)),
        "candidates": candidate_reports,
        "blockedReasons": [] if candidate_reports else ["no_ui_signal_candidate"],
    }


def evidence_output_path(evidence_dir: Path, evidence: dict[str, Any]) -> Path:
    timestamp = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    pair_hint = "no_candidate"
    first = (evidence.get("candidates") or [{}])[0]
    signal = first.get("binanceSignal") if isinstance(first, dict) else {}
    if isinstance(signal, dict) and signal.get("pair"):
        pair_hint = _safe_name(signal.get("pair"))
    return evidence_dir / f"{timestamp}_{pair_hint}.json"


def write_ui_signal_pool_evidence(
    evidence: dict[str, Any],
    *,
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR,
    latest_output: Path = DEFAULT_LATEST_OUTPUT,
) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    output = evidence_output_path(evidence_dir, evidence)
    write_json_atomic(output, evidence)
    write_json_atomic(latest_output, {**evidence, "evidencePath": str(output)})
    return {
        "recorded": True,
        "output": str(output),
        "latestOutput": str(latest_output),
        "candidateCount": evidence.get("candidateCount"),
        "routeCount": evidence.get("routeCount"),
        "blockNumber": (evidence.get("quoteContext") or {}).get("blockNumber"),
        "blockedReasons": evidence.get("blockedReasons") or [],
    }


def record_ui_signal_pool_evidence(
    *,
    market_state: dict[str, Any],
    extremes: dict[str, Any] | None = None,
    record_empty: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    if not ui_signal_pool_evidence_enabled():
        return {"recorded": False, "reason": "disabled"}
    if not _candidate_pairs(market_state) and not record_empty:
        return {"recorded": False, "reason": "no_ui_signal_candidate"}
    evidence = build_ui_signal_pool_evidence(
        market_state=market_state,
        extremes=extremes,
        **kwargs,
    )
    return write_ui_signal_pool_evidence(evidence)
