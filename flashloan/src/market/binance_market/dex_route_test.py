from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_NETWORK


ROOT = Path(__file__).resolve().parents[4]
OFFICIAL_ARB_SCRIPT = ROOT / "test-token_create" / "scripts" / "testnet-v4-official-arb-live.js"
OFFICIAL_DEX_DIR = ROOT / "contract" / "robinhood-testnet" / "DEX"
ROBINHOOD_TESTNET_TOKEN_ALLOWLIST = ROOT / "flashloan" / "src" / "chains" / "robinhood-testnet" / "v4_token_allowlist.json"

OFFICIAL_TRIANGLE_SYMBOLS = {"TZ", "TA", "TB"}


def _load_robinhood_testnet_symbol_addresses() -> dict[str, str]:
    """Build symbol->address identity from the chain-owned token allowlist.

    The allowlist is the single source of truth for Robinhood Testnet token
    identities. There are intentionally no in-code symbol fallbacks here: a
    missing identity must surface as an incomplete-address error instead of
    sending guessed addresses into on-chain review.
    """
    mapping: dict[str, str] = {}
    try:
        data = json.loads(ROBINHOOD_TESTNET_TOKEN_ALLOWLIST.read_text(encoding="utf-8"))
        for item in (data.get("tokens") or {}).values():
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            address = str(item.get("address") or "").strip()
            if symbol and address:
                mapping.setdefault(symbol, address)
    except Exception:
        mapping = {}
    return mapping


_ROBINHOOD_TESTNET_SYMBOL_ADDRESSES = _load_robinhood_testnet_symbol_addresses()


def _official_route_addresses(route_symbols: list[str]) -> list[str]:
    normalized = [symbol.upper() for symbol in route_symbols]
    if not normalized or any(symbol not in _ROBINHOOD_TESTNET_SYMBOL_ADDRESSES for symbol in normalized):
        return []
    return [_ROBINHOOD_TESTNET_SYMBOL_ADDRESSES[symbol] for symbol in normalized]


def _quote_failure_status(error: object) -> str:
    message = str(error or "").lower()
    markers = (
        "not enough liquidity",
        "insufficient liquidity",
        "notenoughliquidity",
        "liquidity is zero",
        "live liquidity is zero",
    )
    if any(marker in message for marker in markers):
        return "not_enough_liquidity"
    return "live_quote_failed"


def _step_failure_status(step: dict[str, Any]) -> str:
    trust = str(step.get("executionTrust") or "").strip().lower()
    if trust == "live_quote_failed" and step.get("liveQuoteError"):
        return _quote_failure_status(step.get("liveQuoteError"))
    return trust


def _norm_symbols(value: object) -> list[str]:
    if isinstance(value, str):
        raw = value.strip()
        if "->" in raw:
            return [part.strip().upper() for part in re.split(r"\s*->\s*", raw) if part.strip()]
        symbols = [part.strip().upper() for part in re.split(r"[\s,;]+", raw) if part.strip()]
        return symbols
    if isinstance(value, (list, tuple)):
        symbols: list[str] = []
        for item in value:
            for symbol in _norm_symbols(item):
                symbols.append(symbol)
        return symbols
    return []


normalize_route_symbols = _norm_symbols


def _route_symbols(route: dict[str, Any]) -> list[str]:
    symbols = _norm_symbols(route.get("routeSymbols") or route.get("symbolPair") or "")
    if not symbols:
        overview_route = route.get("overviewRoute") if isinstance(route.get("overviewRoute"), dict) else {}
        symbols = _norm_symbols(overview_route.get("pathSymbols") or route.get("routeTokenPath") or [])
    return [symbol for symbol in symbols if symbol]


def _match_route(routes: list[dict[str, Any]], requested_symbols: list[str]) -> dict[str, Any] | None:
    if not requested_symbols:
        return None
    requested = [symbol.upper() for symbol in requested_symbols]
    for route in routes:
        existing = _route_symbols(route)
        if existing and existing == requested:
            return route
    return None


def _recommended_route_test_amount(route: dict[str, Any] | None) -> float | None:
    if not isinstance(route, dict):
        return None
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    size = route.get("sizeAnalysis") if isinstance(route.get("sizeAnalysis"), dict) else {}
    candidates = [
        analysis.get("inputAmount"),
        analysis.get("referenceInputAmount"),
        size.get("recommendedBorrowInputAmount"),
    ]
    token_amount_steps = size.get("tokenAmountSteps")
    if isinstance(token_amount_steps, list) and token_amount_steps:
        candidates.append(token_amount_steps[0])
    for candidate in candidates:
        try:
            amount = float(candidate)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            return amount
    return None


def recommended_route_test_amount(
    *,
    route_symbols: list[str],
    chain_id: int,
    protocols: list[str],
) -> float | None:
    routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
    route = _match_route(routes_payload.get("routes") or [], route_symbols)
    return _recommended_route_test_amount(route)


def _amount_float(value: object) -> float:
    try:
        amount = float(str(value or "").strip())
    except (TypeError, ValueError):
        raise ValueError("amount must be a positive number")
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    return amount


def _step_payload(step: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(step, dict):
        return None
    return {
        "hop": step.get("hop"),
        "fromSymbol": step.get("fromSymbol"),
        "toSymbol": step.get("toSymbol"),
        "pool": step.get("pool"),
        "feeBps": step.get("feeBps"),
        "inputAmount": step.get("inputAmount"),
        "outputAmount": step.get("outputAmount"),
        "tokenIn": step.get("tokenIn") or step.get("fromAddress"),
        "tokenOut": step.get("tokenOut") or step.get("toAddress"),
        "referenceOutputAmount": step.get("referenceOutputAmount"),
        "effectiveRate": step.get("effectiveRate"),
        "referenceEffectiveRate": step.get("referenceEffectiveRate"),
        "priceImpactBps": step.get("priceImpactBps"),
        "executionTrust": step.get("executionTrust"),
        "quoteSource": step.get("quoteSource"),
        "liveQuoteError": step.get("liveQuoteError"),
    }


def estimate_route_live_quote(
    *,
    chain_id: int,
    protocols: list[str],
    route_symbols: list[str],
    amount: float,
) -> dict[str, Any]:
    from market.binance_market.dex_surface_graph import _price_map_from_pairs
    from market.binance_market.dex_surface_quotes import _route_quote_analysis
    from market.binance_market.pool_candidates import dex_pool_candidates_payload

    route_symbols = _norm_symbols(route_symbols)
    routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
    route = _match_route(routes_payload.get("routes") or [], route_symbols)
    if route is None:
        available = [_route_symbols(item) for item in (routes_payload.get("routes") or [])]
        return {
            "ok": False,
            "mode": "dex_route_live_estimate",
            "error": "route_not_found",
            "routeSymbols": route_symbols,
            "availableRoutes": available,
        }

    pool_payload = dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols)
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    analysis = _route_quote_analysis(
        route,
        price_map=price_map,
        chain_id=chain_id,
        token_basis_input_amount=amount,
    )
    route_addresses = [str(item) for item in analysis.get("routeAddresses") or [] if str(item).strip()]
    if not route_addresses:
        route_addresses = _official_route_addresses(route_symbols)
    steps = [step for step in (analysis.get("steps") or []) if isinstance(step, dict)]
    full_steps = [int(step.get("hop") or 0) for step in steps]
    complete = bool(steps) and full_steps == list(range(1, max(1, len(route_symbols))))
    live_verified = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    failure_statuses = [_step_failure_status(step) for step in steps]
    liquidity_failed = "not_enough_liquidity" in failure_statuses
    live_failed = any(
        status in {"live_quote_failed", "not_enough_liquidity", "sdk_adapter_required", "adapter_required"}
        for status in failure_statuses
    ) or any(
        bool(step.get("liveQuoteError"))
        for step in steps
    )
    quote_status = (
        "live_verified"
        if live_verified
        else "not_enough_liquidity"
        if liquidity_failed
        else "live_quote_failed"
        if live_failed
        else "requires_live_quoter"
        if bool(analysis.get("requiresLiveQuoter"))
        else "live_quote_reference_only"
    )
    execution_trust = (
        "live_v4_adapter"
        if live_verified
        else "not_enough_liquidity"
        if quote_status == "not_enough_liquidity"
        else "live_quote_failed"
        if quote_status == "live_quote_failed"
        else "static_live_quoter_estimate"
    )
    return {
        "ok": True,
        "mode": "dex_route_live_estimate",
        "chain_id": chain_id,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "amount": amount,
        "amountSymbol": route_symbols[0] if route_symbols else None,
        "complete": complete,
        "liveVerified": live_verified,
        "notEnoughLiquidity": liquidity_failed,
        "quoteStatus": quote_status,
        "executionTrust": execution_trust,
        "steps": [_step_payload(step) for step in steps],
        "analysis": {
            "routeAddresses": route_addresses,
            "referenceInputSymbol": analysis.get("referenceInputSymbol") or (route_symbols[0] if route_symbols else None),
            "referenceFinalOutputAmount": analysis.get("referenceFinalOutputAmount"),
            "referenceFinalOutputSymbol": analysis.get("referenceFinalOutputSymbol"),
            "referenceGrossProfitAmount": analysis.get("referenceGrossProfitAmount"),
            "referenceNetProfitAmount": analysis.get("referenceNetProfitAmount"),
            "referenceNetReturnBps": analysis.get("referenceNetReturnBps"),
            "maxStaticDepthImpactBps": analysis.get("maxStaticDepthImpactBps"),
            "warnings": analysis.get("warnings") or [],
        },
    }


def is_official_triangle_route(route_symbols: list[str]) -> bool:
    if len(route_symbols) != 4 or route_symbols[0] != route_symbols[-1]:
        return False
    return set(route_symbols) == OFFICIAL_TRIANGLE_SYMBOLS


def _static_call_revert_detail(parsed: dict[str, Any]) -> str:
    raw = str(parsed.get("errorDetail") or parsed.get("detail") or parsed.get("error") or "").strip()
    return f"链上静态估算未通过：{raw}" if raw else "链上静态估算未通过：execution reverted"


def run_official_v4_arb_route_test(
    *,
    route_symbols: list[str],
    amount: float,
    broadcast: bool,
) -> dict[str, Any]:
    if not is_official_triangle_route(route_symbols):
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_route_unsupported",
            "detail": "广播/staticCall 测试目前支持 TA/TB/TZ 三角闭环；其他链上路由请先使用链下估算，再接入对应执行器。",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }
    if not OFFICIAL_ARB_SCRIPT.exists() or not OFFICIAL_DEX_DIR.exists():
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_script_missing",
            "detail": f"missing script or DEX workdir: {OFFICIAL_ARB_SCRIPT}",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }

    env = os.environ.copy()
    env["HARDHAT_NETWORK"] = "robinhoodTestnet"
    env["ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST"] = "true"
    env["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"] = "true" if broadcast else "false"
    rpc_urls = robinhood.testnet_rpc_urls_from_env()
    if rpc_urls:
        env["ROBINHOOD_TESTNET_RPC_URL"] = rpc_urls[0]

    command = [
        os.environ.get("NODE", "node"),
        str(OFFICIAL_ARB_SCRIPT),
        "--route",
        " -> ".join(route_symbols),
        "--amount",
        str(amount),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(OFFICIAL_DEX_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "mode": "dex_route_broadcast_test",
            "error": "official_arb_broadcast_timeout",
            "detail": "链上脚本超时，建议降低金额或检查 RPC。",
            "routeSymbols": route_symbols,
            "broadcast": broadcast,
        }

    appended = result.stdout + "\n" + result.stderr
    json_match = re.search(r"OFFICIAL_V4_ARB_RESULT_JSON\s+(\{.*\})", appended, flags=re.DOTALL)
    parsed: dict[str, Any] = {}
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            parsed = {}

    payload = {
        "ok": result.returncode == 0 and parsed.get("ok") is not False,
        "mode": "dex_route_broadcast_test",
        "routeSymbols": route_symbols,
        "routeAddresses": _official_route_addresses(route_symbols),
        "amount": amount,
        "broadcast": broadcast,
        "exitCode": result.returncode,
        "detail": parsed.get("detail") or parsed.get("error") or (result.stderr.strip() or result.stdout.strip()),
        "errorDetail": parsed.get("errorDetail"),
        "errorData": parsed.get("errorData"),
        "errorSelector": parsed.get("errorSelector"),
        "staticCall": parsed.get("staticCall"),
        "profit": parsed.get("profit"),
        "outAmount": parsed.get("outAmount"),
        "txHash": parsed.get("txHash") if broadcast else None,
        "stdoutTail": _tail(result.stdout),
        "stderrTail": _tail(result.stderr),
    }
    if parsed.get("staticCall") == "reverted" and parsed.get("error") == "static_arb_call_reverted":
        payload["detail"] = _static_call_revert_detail(parsed)
    if "already broadcast-disabled" not in payload["detail"] and result.returncode != 0:
        payload["error"] = parsed.get("error") or "official_arb_broadcast_failed"
    return payload


def _tail(text: str, limit: int = 1200) -> str:
    clean = str(text or "").strip()
    return clean[-limit:] if len(clean) > limit else clean


def _routes_payload(*, chain_id: int, protocols: list[str]) -> dict[str, Any]:
    from market.binance_market.dex_surfaces import dex_quote_routes_payload

    key = "DEX_V4_LIVE_QUOTE_ENABLED"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    try:
        return dex_quote_routes_payload(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        )
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


__all__ = [
    "estimate_route_live_quote",
    "is_official_triangle_route",
    "normalize_route_symbols",
    "recommended_route_test_amount",
    "run_official_v4_arb_route_test",
]
