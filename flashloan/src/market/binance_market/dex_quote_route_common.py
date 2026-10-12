from __future__ import annotations

from typing import Any
from urllib.parse import quote

from market.binance_market.dex_surface_analysis import DEFAULT_QUOTE_PROBE_NOTIONAL_USD
from core.dex_provenance import build_price_evidence_payload


def _pool_protocol(pool: dict[str, Any]) -> str:
    return str(pool.get("protocol") or pool.get("dex") or "").strip().lower()


def quote_route_status(quote_analysis: dict[str, Any]) -> str:
    live_verified = bool(quote_analysis.get("livePriceVerified")) or str(quote_analysis.get("priceEvidenceMode") or "").strip().lower() == "live_verified"
    if live_verified:
        return "live_verified"
    terminal_status = str(quote_analysis.get("terminalStatus") or "").strip()
    if terminal_status in {"missing_route_identity", "route_invalid", "uniswapx_fallback_failed"}:
        return terminal_status
    if quote_analysis.get("executableCandidate"):
        return "static_probe_ready_for_precheck"
    if quote_analysis.get("chainSubmissionCandidate"):
        return "chain_profit_check_candidate"
    if quote_analysis.get("requiresLiveQuoter"):
        return "requires_live_quoter"
    if quote_analysis.get("complete"):
        return "static_probe_not_profitable"
    return "static_probe_incomplete"


def build_quote_verification_payload(*, quote_analysis: dict[str, Any] | None) -> dict[str, Any]:
    analysis = dict(quote_analysis or {})
    live_price_verified = bool(analysis.get("livePriceVerified")) or str(analysis.get("priceEvidenceMode") or "").strip().lower() == "live_verified"
    requires_live_quoter = bool(analysis.get("requiresLiveQuoter")) or bool(analysis.get("adapterRequired"))
    status = "live_verified" if live_price_verified else ("live_required" if requires_live_quoter else "reference_only")
    payload = {
        "status": status,
        "livePriceVerified": live_price_verified,
        "requiresLiveQuoter": requires_live_quoter,
        "adapterRequired": bool(analysis.get("adapterRequired")),
        "executableCandidate": bool(analysis.get("executableCandidate")),
        "chainSubmissionCandidate": bool(analysis.get("chainSubmissionCandidate")),
        "trustedForExecution": bool(live_price_verified and (analysis.get("chainSubmissionCandidate") or analysis.get("executableCandidate"))),
    }
    payload.update(
        build_price_evidence_payload(
            source=analysis.get("priceEvidenceSource"),
            mode=analysis.get("priceEvidenceMode"),
        )
    )
    return payload


def build_quote_analysis_defaults(
    *,
    quote_analysis: dict[str, Any] | None,
    route_symbols: list[str],
    price_evidence_source: str | None = None,
    price_evidence_mode: str | None = None,
    method: str = "overview_route_snapshot",
    notional_usd: float = DEFAULT_QUOTE_PROBE_NOTIONAL_USD,
) -> dict[str, Any]:
    analysis = dict(quote_analysis or {})
    analysis.setdefault("method", method)
    analysis.setdefault("notionalUsd", notional_usd)
    analysis.setdefault("complete", bool(route_symbols))
    analysis.setdefault("requiresLiveQuoter", True)
    analysis.setdefault("adapterRequired", False)
    analysis.setdefault("livePriceVerified", False)
    analysis.update(
        build_price_evidence_payload(
            source=price_evidence_source or analysis.get("priceEvidenceSource") or "cached_framework_live_confirmation",
            mode=price_evidence_mode or analysis.get("priceEvidenceMode") or "framework_reused_live_confirmation",
        )
    )
    analysis.setdefault("finalOutputUsd", None)
    analysis.setdefault("referenceFinalOutputUsd", None)
    analysis.setdefault("grossProfitUsd", None)
    analysis.setdefault("referenceGrossProfitUsd", None)
    analysis.setdefault("netProfitUsd", None)
    analysis.setdefault("referenceNetProfitUsd", None)
    analysis.setdefault("referenceReturnBasis", None)
    analysis.setdefault("referenceReturnTrusted", False)
    analysis.setdefault("profitable", False)
    analysis.setdefault("executableCandidate", False)
    analysis.setdefault("chainSubmissionCandidate", False)
    analysis.setdefault("chainSubmissionPolicy", "live_framework_reuse")
    analysis.setdefault(
        "recommendation",
        {
            "level": "watch",
            "action": "等待链上实时价格复核",
            "reasons": [
                "当前仅公开静态路由框架，不把静态快照当作实时价格",
                "需要链上实时 Quoter 或 eth_call 结果后才能进入自动交易",
            ],
        },
    )
    return analysis


def build_quote_request_specs(
    *,
    pair_symbol: str,
    pools: list[dict[str, Any]],
    route_symbols: list[str],
    route_addresses: list[str],
    route_tokens: list[dict[str, Any]],
    amm_quote_mode: str,
    amm_status: str,
    chain_id: int | None,
    settlement: dict[str, Any],
    v4_quote_mode: str | None = None,
    v4_status: str | None = None,
    uniswapx_quote_mode: str | None = None,
    uniswapx_status: str | None = None,
    uniswapx_middle_hop: dict[str, Any] | None = None,
    adapter_hints: list[str] | None = None,
    amm_limit: int = 5,
) -> list[dict[str, Any]]:
    quote_requests = [
        {
            "venue": "amm_pool",
            "quoteMode": amm_quote_mode,
            "status": amm_status,
            "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(pair_symbol, safe='')}&venue=amm_pool",
            "pool": pool.get("pool"),
            "protocol": pool.get("protocol") or pool.get("dex"),
            "fee": pool.get("fee"),
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "routeTokenPath": route_tokens,
        }
        for pool in pools[:amm_limit]
    ]
    v4_pools = [pool for pool in pools if _pool_protocol(pool) == "uniswap_v4"]
    if v4_pools and v4_quote_mode:
        quote_requests.append(
            {
                "venue": "uniswap_v4",
                "quoteMode": v4_quote_mode,
                "status": v4_status or amm_status,
                "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(pair_symbol, safe='')}&venue=uniswap_v4",
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "routeTokenPath": route_tokens,
                "chainId": chain_id,
                "adapterHints": list(adapter_hints or []),
                "pools": [pool.get("pool") for pool in v4_pools[:amm_limit]],
            }
        )
    uniswapx = settlement.get("uniswapx") if isinstance(settlement.get("uniswapx"), dict) else {}
    if uniswapx.get("supported") and len(route_symbols) >= 2 and uniswapx_quote_mode:
        middle_hop = dict(uniswapx_middle_hop or {})
        request_symbols = list(middle_hop.get("routeSymbols") or [])
        request_addresses = list(middle_hop.get("routeAddresses") or [])
        middle_pair = str(middle_hop.get("symbolPair") or "").strip()
        if middle_hop:
            request_symbols = request_symbols or [
                middle_hop.get("fromSymbol"),
                middle_hop.get("toSymbol"),
            ]
            request_addresses = request_addresses or [
                middle_hop.get("fromAddress"),
                middle_hop.get("toAddress"),
            ]
            if not middle_pair:
                middle_pair = " -> ".join(
                    str(symbol or "").strip()
                    for symbol in request_symbols
                    if str(symbol or "").strip()
                )
        request = {
            "venue": "uniswapx",
            "quoteMode": uniswapx_quote_mode,
            "status": uniswapx_status or amm_status,
            "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(middle_pair or pair_symbol, safe='')}&venue=uniswapx",
            "routeSymbols": request_symbols or route_symbols,
            "routeAddresses": request_addresses or route_addresses,
            "routeTokenPath": route_tokens,
            "chainId": chain_id,
            "reactorFamily": uniswapx.get("reactor_family"),
            "requires": ["sellToken", "buyToken", "amount", "userAddress"],
        }
        if middle_hop:
            request["middleHop"] = middle_hop
            if middle_hop.get("middleLeg"):
                request["middleLeg"] = middle_hop.get("middleLeg")
            quote_parameters = middle_hop.get("quoteParameters")
            if isinstance(quote_parameters, dict):
                request["quoteParameters"] = quote_parameters
                request["sellToken"] = quote_parameters.get("sellToken")
                request["buyToken"] = quote_parameters.get("buyToken")
                request["amount"] = quote_parameters.get("sellAmount")
                request["minBuyAmount"] = quote_parameters.get("buyAmount") or quote_parameters.get("minBuyAmount")
                request["limitPrice"] = quote_parameters.get("limitPrice")
                request["targetProfitBps"] = quote_parameters.get("targetProfitBps")
        quote_requests.append(request)
    return quote_requests


def build_quote_inputs_bundle(
    *,
    candidate_pools: list[dict[str, Any]],
    route_pools: list[dict[str, Any]],
    quote_requests: list[dict[str, Any]],
    conditions: list[str],
) -> dict[str, Any]:
    return {
        "candidatePools": candidate_pools,
        "routePools": route_pools,
        "quoteRequests": quote_requests,
        "conditions": conditions,
    }
