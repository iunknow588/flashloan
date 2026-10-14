"""Reusable helpers for Robinhood testnet V4 route tests.

The functions here are implementation-level helpers shared by
``dex_route_test`` and its tests. They are kept in a separate module so the
route test entrypoint stays focused on orchestration while the address,
status, symbol, and payload helpers remain independently testable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from market.binance_market.dex_best_amount_snapshots import (
    _probe_records_from_size_analysis,
    _row_profit_metric,
    _safe_float,
)


ROOT = Path(__file__).resolve().parents[4]
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID
from dex.intent_trade.chains.robinhood_common.stock_token_registry import stock_token_by_symbol
from core.chain_data_registry import chain_data_by_chain_id

ROBINHOOD_TESTNET_TOKEN_ALLOWLIST = (
    ROOT
    / "flashloan"
    / "src"
    / "chains"
    / "robinhood-testnet"
    / "v4_token_allowlist.json"
)
ROBINHOOD_TESTNET_OFFICIAL_V4_MANIFEST = (
    ROOT
    / "test-token_create"
    / "config"
    / "testnet-v4-amm"
    / "manifest.json"
)


def _chain_official_v4_manifest_path(chain_id: int | None) -> Path:
    network = "robinhood-testnet"
    if chain_id is not None:
        row = chain_data_by_chain_id(chain_id) or {}
        network = str(row.get("network") or network).strip().lower()
    manifest_network = {
        "robinhood": "mainnet",
        "robinhood-testnet": "testnet",
    }.get(network, network)
    return ROOT / "test-token_create" / "config" / f"{manifest_network}-v4-amm" / "manifest.json"


_OFFICIAL_TEST_ROUTE_SYMBOLS = {"TZ", "TA", "TB"}

_SYMBOL_ADDRESS_CACHE: dict[int, tuple[dict[str, str], set[str]]] = {}


def _chain_token_allowlist_path(chain_id: int | None) -> Path | None:
    row = chain_data_by_chain_id(chain_id) if chain_id is not None else {}
    if not row:
        return None
    data_files = row.get("data_files") if isinstance(row.get("data_files"), dict) else {}
    relative = str(data_files.get("v4_token_allowlist") or "").strip()
    if relative:
        return ROOT / "flashloan" / "src" / relative
    network = str(row.get("network") or "").strip()
    if network:
        candidate = ROOT / "flashloan" / "src" / "chains" / network / "v4_token_allowlist.json"
        if candidate.exists():
            return candidate
    return None


def _load_chain_symbol_addresses(chain_id: int | None) -> tuple[dict[str, str], set[str]]:
    """Build symbol->address identity from the chain-owned token allowlist.

    The allowlist is the single source of truth for Robinhood Testnet token
    identities. There are intentionally no in-code symbol fallbacks here: a
    missing identity, or a symbol with multiple distinct addresses, must surface
    as an incomplete-address error instead of sending guessed addresses into
    on-chain review.
    """
    if chain_id in _SYMBOL_ADDRESS_CACHE:
        return _SYMBOL_ADDRESS_CACHE[chain_id]
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    allowlist_path = _chain_token_allowlist_path(chain_id) or ROBINHOOD_TESTNET_TOKEN_ALLOWLIST
    try:
        data = json.loads(allowlist_path.read_text(encoding="utf-8"))
        for item in (data.get("tokens") or {}).values():
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            address = str(item.get("address") or "").strip()
            if symbol and address:
                existing = mapping.get(symbol)
                if existing is not None and existing.lower() != address.lower():
                    ambiguous.add(symbol)
                    mapping.pop(symbol, None)
                elif existing is None:
                    mapping[symbol] = address
    except Exception:
        mapping = {}
        ambiguous = set()
    chain_id_value = int(chain_id or 0)
    if chain_id_value in {4663, 46630}:
        try:
            network = "robinhood" if chain_id_value == 4663 else "robinhood-testnet"
            for symbol, item in stock_token_by_symbol(
                network=network,
                chain_id=chain_id_value,
            ).items():
                address = str(item.get("address") or "").strip()
                existing = mapping.get(symbol)
                if existing is not None and existing.lower() != address.lower():
                    ambiguous.add(symbol)
                    mapping.pop(symbol, None)
                elif existing is None and address:
                    mapping[symbol] = address
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    _SYMBOL_ADDRESS_CACHE[chain_id] = (mapping, ambiguous)
    return mapping, ambiguous


def _official_route_addresses(
    route_symbols: list[str],
    *,
    chain_id: int | None = None,
) -> list[str]:
    if chain_id is None:
        return []
    chain_id = int(chain_id)
    symbol_addresses, ambiguous_symbols = _load_chain_symbol_addresses(chain_id)
    normalized = [symbol.upper() for symbol in route_symbols]
    if (
        not normalized
        or any(symbol in ambiguous_symbols for symbol in normalized)
        or any(symbol not in symbol_addresses for symbol in normalized)
    ):
        return []
    return [symbol_addresses[symbol] for symbol in normalized]


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
    if "und_err_connect_timeout" in message or "connecttimeouterror" in message:
        return "rpc_verification_timeout"
    if "winerror 10061" in message or " 10061 " in message or "error code: 1010" in message or "403 forbidden" in message:
        return "rpc_verification_failed"
    if "rpc" in message and "unavailable after" in message and "connect attempt" in message:
        return "rpc_verification_failed"
    if "rpc" in message and (
        "unavailable" in message
        or "failed" in message
        or "connect" in message
        or "timeout" in message
        or "timed out" in message
    ):
        return "rpc_verification_timeout" if ("timeout" in message or "timed out" in message) else "rpc_verification_failed"
    if "pool not initialized" in message or "missing sqrtpricex96" in message or "slot0 missing" in message:
        return "pool_not_initialized"
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


def _route_addresses_from_route(route: dict[str, Any]) -> list[str]:
    """Return closed token-address lists carried by a market observation route."""
    if not isinstance(route, dict):
        return []
    candidates: list[list[str]] = []
    for key in ("routeAddresses", "route_addresses", "pathAddresses", "path_addresses"):
        raw = route.get(key)
        if isinstance(raw, list):
            candidates.append([str(item or "").strip() for item in raw if str(item or "").strip()])
    overview = route.get("overviewRoute") if isinstance(route.get("overviewRoute"), dict) else {}
    for key in ("pathAddresses", "routeAddresses"):
        raw = overview.get(key)
        if isinstance(raw, list):
            candidates.append([str(item or "").strip() for item in raw if str(item or "").strip()])
    raw_tokens = route.get("routeTokenPath") or overview.get("pathTokens") or []
    if isinstance(raw_tokens, list):
        token_addresses: list[str] = []
        for item in raw_tokens:
            if isinstance(item, dict):
                address = str(item.get("address") or "").strip()
                if address:
                    token_addresses.append(address)
        if token_addresses:
            candidates.append(token_addresses)
    for addresses in candidates:
        if addresses and addresses[0].lower() == addresses[-1].lower():
            return addresses
    return []


def _match_route_by_addresses(
    routes: list[dict[str, Any]],
    route_addresses: list[str],
) -> dict[str, Any] | None:
    if not routes or not route_addresses:
        return None
    normalized = [str(address or "").strip().lower() for address in route_addresses]
    if not normalized or normalized[0] != normalized[-1]:
        return None
    for route in routes:
        existing = _route_addresses_from_route(route)
        if existing and [str(address or "").strip().lower() for address in existing] == normalized:
            return route
    return None


def _recommended_route_test_amount(
    route: dict[str, Any] | None,
) -> float | None:
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
    return _route_initial_probe_human(route)


def recommended_route_test_amount(
    *,
    route_symbols: list[str],
    chain_id: int,
    protocols: list[str],
) -> float | None:
    routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
    route = _match_route(routes_payload.get("routes") or [], route_symbols)
    return _recommended_route_test_amount(route)


def _route_initial_probe_human(route: dict[str, Any]) -> float | None:
    """Use the system per-token initial probe handler as the sole fallback."""
    try:
        from dex.runtime.dex_quote_evidence_common import _pool_map
        from dex.runtime.dex_quote_evidence_pool import _pool_side
        from dex.runtime.dex_quote_evidence_size import _offchain_initial_probe_amount_raw
    except Exception:
        return None
    pools = _pool_map(route)
    input_symbol = route.get("routeSymbols") or []
    input_symbol = input_symbol[0] if input_symbol else None
    first_pool = next(iter(pools.values()), {})
    _, _, input_decimals = _pool_side(first_pool, input_symbol or "")
    if input_decimals is None:
        input_decimals = 18
    raw, _, _, _ = _offchain_initial_probe_amount_raw(
        route,
        input_decimals=input_decimals,
        analysis=route.get("quoteAnalysis"),
        pools=pools,
    )
    if not raw or raw <= 0:
        return None
    return float(raw / (10 ** input_decimals))


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


def _official_v4_route_pool_keys(
    *,
    route_symbols: list[str],
    chain_id: int,
    protocols: list[str],
    route_addresses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the exact 3 Uniswap V4 PoolKeys used by an official triangle route.

    The quote and route page displays only closed loops that came from the DEX
    overview market observation with enough pool depth and complete PoolKeys.
    On-chain review must therefore prefer those same observed bridge PoolKeys
    instead of guessing global default hooks.  Manifest keys are only a
    fallback for routes that did not carry complete market-observation pool
    identities yet.
    """
    try:
        routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
        route = _match_route(routes_payload.get("routes") or [], route_symbols)
        if not isinstance(route, dict) and route_addresses:
            route = _match_route_by_addresses(routes_payload.get("routes") or [], route_addresses)
        if isinstance(route, dict):
            hop_count = max(1, len(route_symbols) - 1)
            pool_keys: list[dict[str, Any]] = []
            for pool in (route.get("routePools") or [])[:hop_count]:
                if not isinstance(pool, dict):
                    continue
                pool_key = pool.get("poolKey")
                if not isinstance(pool_key, dict):
                    continue
                currency0 = str(pool_key.get("currency0") or pool_key.get("token0") or "").strip()
                currency1 = str(pool_key.get("currency1") or pool_key.get("token1") or "").strip()
                fee = pool_key.get("fee")
                tick_spacing = pool_key.get("tickSpacing")
                hooks = str(pool_key.get("hooks") or "").strip()
                if not currency0 or not currency1 or fee is None or tick_spacing is None or not hooks:
                    continue
                pool_keys.append(
                    {
                        "currency0": currency0,
                        "currency1": currency1,
                        "fee": int(fee),
                        "tickSpacing": int(tick_spacing),
                        "hooks": hooks,
                    }
                )
            if len(pool_keys) == hop_count:
                return pool_keys
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        pass
    return _official_v4_manifest_route_pool_keys(route_symbols, chain_id=chain_id)


def _official_v4_manifest_route_pool_keys(
    route_symbols: list[str],
    *,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    normalized = [str(symbol or "").strip().upper() for symbol in route_symbols]
    if (
        len(normalized) != 4
        or normalized[0] != normalized[-1]
        or normalized[0] not in _OFFICIAL_TEST_ROUTE_SYMBOLS
        or set(normalized) != _OFFICIAL_TEST_ROUTE_SYMBOLS
    ):
        return []
    try:
        data = json.loads(_chain_official_v4_manifest_path(chain_id).read_text(encoding="utf-8"))
    except Exception:
        return []
    official = data.get("officialV4") if isinstance(data, dict) else None
    if not isinstance(official, dict):
        return []
    try:
        fee = int(official.get("fee"))
        tick_spacing = int(official.get("tickSpacing"))
    except (TypeError, ValueError):
        return []
    hooks = str(official.get("hooks") or "0x0000000000000000000000000000000000000000")
    addresses = _official_route_addresses(normalized, chain_id=chain_id)
    if len(addresses) != 4 or addresses[0].lower() != addresses[-1].lower():
        return []
    pool_keys: list[dict[str, Any]] = []
    for index in range(3):
        currency0 = addresses[index]
        currency1 = addresses[index + 1]
        try:
            swapped = int(currency0.lower(), 16) > int(currency1.lower(), 16)
        except ValueError:
            return []
        if swapped:
            currency0, currency1 = currency1, currency0
        pool_keys.append(
            {
                "currency0": currency0,
                "currency1": currency1,
                "fee": fee,
                "tickSpacing": tick_spacing,
                "hooks": hooks,
            }
        )
    return pool_keys


def _probe_record_item(
    probe_analysis: dict[str, Any],
    probe_amount: float,
    route_symbols: list[str],
) -> dict[str, Any]:
    symbol = route_symbols[0] if route_symbols else None
    return {
        "amount": float(probe_amount),
        "borrowAmount": float(probe_amount),
        "symbol": symbol,
        "borrowSymbol": symbol,
        "borrowBasis": symbol,
        "referenceInputSymbol": probe_analysis.get("referenceInputSymbol") or symbol,
        "referenceFinalOutputAmount": probe_analysis.get("referenceFinalOutputAmount"),
        "referenceFinalOutputSymbol": probe_analysis.get("referenceFinalOutputSymbol") or symbol,
        "referenceNetProfitAmount": probe_analysis.get("referenceNetProfitAmount"),
        "referenceNetReturnBps": probe_analysis.get("referenceNetReturnBps"),
        "executableCandidate": bool(
            (probe_analysis.get("referenceNetProfitAmount") or 0)
            and 0 < float(probe_analysis.get("referenceNetProfitAmount") or 0)
        ),
    }


def _candidate_amount_ladder(route: dict[str, Any] | None, amount: float, max_attempts: int) -> list[float]:
    base_amount = float(amount) if amount is not None and amount > 0 else 1.0
    # Generate a dynamic halving/doubling ladder from the base amount.
    # The ladder is algorithm-controlled by max_attempts; there is no fixed
    # multiplier table or static token step list baked into the probe range.
    halving_steps = max(1, max_attempts // 2)
    ladder: list[float] = []
    for index in range(-halving_steps, halving_steps + 1):
        ladder.append(base_amount * (2 ** index))
    unique_amounts: dict[float, float] = {}
    for raw in ladder:
        key = round(float(raw), 12)
        if key > 0:
            unique_amounts[key] = key
    return list(unique_amounts.values())[:max_attempts]


def _optimize_amount_ladder(
    *,
    route: dict[str, Any],
    route_symbols: list[str],
    price_map: dict[str, float],
    chain_id: int,
    amount: float,
    max_attempts: int,
    analysis: dict[str, Any],
    probe_records: list[dict[str, Any]],
    quote_analysis_fn: Any,
) -> tuple[dict[str, Any], float, float, list[dict[str, Any]], dict[str, Any]]:
    def _parse_profit(candidate_analysis: dict[str, Any]) -> float | None:
        try:
            return float(candidate_analysis.get("referenceNetProfitAmount"))
        except (TypeError, ValueError):
            return None

    best_amount = float(amount) if amount is not None and amount > 0 else 1.0
    best_profit = _parse_profit(analysis)
    best_analysis = analysis
    attempts_used = len(probe_records)
    # Whether the search found a non-improving neighbor or an unprofitable
    # upper bound. Without that comparison we only know the maximum scanned
    # amount, not a best amount.
    found_upper_neighbor = False
    refined_around_peak = False
    scan_reason = "initial_probe"
    comparison_base_amount = best_amount

    def _probe(candidate_amount: float) -> tuple[dict[str, Any], float | None] | None:
        nonlocal attempts_used
        if attempts_used >= max_attempts or candidate_amount <= 0:
            return None
        try:
            candidate_analysis = quote_analysis_fn(
                route,
                price_map=price_map,
                chain_id=chain_id,
                token_basis_input_amount=candidate_amount,
            )
        except Exception:
            attempts_used += 1
            return None
        probe_records.append(_probe_record_item(candidate_analysis, candidate_amount, route_symbols))
        attempts_used += 1
        return candidate_analysis, _parse_profit(candidate_analysis)

    lower_amount = best_amount / 2.0
    upper_amount = None
    initial_profitable = best_profit is not None and best_profit > 0
    base_amount = best_amount

    if not initial_profitable:
        # Unprofitable at the initial amount: halve until the first profitable
        # amount is found, then refine between that profitable lower point and
        # the last unprofitable upper point.
        test_amount = best_amount / 2.0
        last_unprofitable_amount = best_amount
        while attempts_used < max_attempts:
            probe = _probe(test_amount)
            if probe is None:
                break
            candidate_analysis, candidate_profit = probe
            if candidate_profit is not None and candidate_profit > 0:
                best_amount = test_amount
                best_profit = candidate_profit
                best_analysis = candidate_analysis
                base_amount = test_amount
                lower_amount = test_amount / 2.0
                upper_amount = last_unprofitable_amount
                found_upper_neighbor = True
                comparison_base_amount = test_amount
                break
            last_unprofitable_amount = test_amount
            test_amount /= 2.0
    else:
        # Profitable at the initial amount: double until the next candidate no
        # longer beats the current best, then refine with both midpoint
        # directions so direction of the next step is chosen by profit.
        test_amount = best_amount * 2.0
        while attempts_used < max_attempts:
            probe = _probe(test_amount)
            if probe is None:
                break
            candidate_analysis, candidate_profit = probe
            if candidate_profit is not None and candidate_profit > best_profit:
                best_amount = test_amount
                best_profit = candidate_profit
                best_analysis = candidate_analysis
                base_amount = test_amount
                lower_amount = test_amount / 2.0
                comparison_base_amount = test_amount
                upper_amount = None
                found_upper_neighbor = False
                test_amount *= 2.0
                if attempts_used >= max_attempts:
                    break
                continue
            upper_amount = test_amount
            found_upper_neighbor = True
            break

    if upper_amount is not None and attempts_used < max_attempts:
        refined_around_peak = True
        tolerance = max(abs(float(best_amount)), 1.0) * 1e-9
        while attempts_used < max_attempts and float(upper_amount) - float(lower_amount) > tolerance:
            if float(upper_amount) <= float(base_amount) + tolerance or float(lower_amount) >= float(base_amount) - tolerance:
                break
            left_mid = float(lower_amount) + (float(base_amount) - float(lower_amount)) / 2.0
            right_mid = float(base_amount) + (float(upper_amount) - float(base_amount)) / 2.0
            if left_mid <= float(lower_amount) or left_mid >= float(base_amount) or right_mid <= float(base_amount) or right_mid >= float(upper_amount):
                break

            left = _probe(left_mid)
            right = _probe(right_mid)
            if left is None and right is None:
                break
            left_analysis, left_profit = left if left is not None else (None, None)
            right_analysis, right_profit = right if right is not None else (None, None)
            left_ok = left_profit is not None and left_profit > 0
            right_ok = right_profit is not None and right_profit > 0
            left_improves = left_ok and left_profit is not None and left_profit > best_profit
            right_improves = right_ok and right_profit is not None and right_profit > best_profit
            if not left_improves and not right_improves:
                break

            old_base = float(base_amount)
            if left_ok and right_ok:
                if right_profit is not None and left_profit is not None and left_profit >= right_profit:
                    if left_improves and left_analysis is not None and left_profit is not None:
                        best_amount = left_mid
                        best_profit = left_profit
                        best_analysis = left_analysis
                        comparison_base_amount = left_mid
                    upper_amount = old_base
                    base_amount = left_mid
                else:
                    if right_improves and right_analysis is not None and right_profit is not None:
                        best_amount = right_mid
                        best_profit = right_profit
                        best_analysis = right_analysis
                        comparison_base_amount = right_mid
                    lower_amount = old_base
                    base_amount = right_mid
            elif left_ok:
                if left_improves and left_analysis is not None and left_profit is not None:
                    best_amount = left_mid
                    best_profit = left_profit
                    best_analysis = left_analysis
                    comparison_base_amount = left_mid
                upper_amount = old_base
                base_amount = left_mid
            elif right_ok:
                if right_improves and right_analysis is not None and right_profit is not None:
                    best_amount = right_mid
                    best_profit = right_profit
                    best_analysis = right_analysis
                    comparison_base_amount = right_mid
                lower_amount = old_base
                base_amount = right_mid
    selection_confirmed = bool(found_upper_neighbor)
    if selection_confirmed:
        selection_basis = "offchain_amount_ladder"
        selection_reason = (
            "找到非更优相邻量并完成峰值比较"
            if refined_around_peak
            else "找到非更优相邻量，但未进入更细粒度峰值精修"
        )
    elif best_profit is not None and best_profit > 0:
        selection_basis = "initial_probe_profitable_scan_limit"
        selection_reason = "初始量可盈利，但未找到峰值反向点前已达到探测次数上限"
    else:
        selection_basis = "initial_probe_unconfirmed"
        selection_reason = "未完成可执行的最优量搜索，当前值只能作为初始量"
    selection_meta = {
        "selectionConfirmed": bool(selection_confirmed),
        "selectionBasis": selection_basis,
        "selectionReason": selection_reason,
        "selectionMetric": best_profit,
        "initialProbeAmount": float(amount) if amount is not None and amount > 0 else best_amount,
        "amountOptimizedFrom": float(amount) if amount is not None and amount > 0 else best_amount,
        "amountOptimized": abs(float(best_amount) - float(amount)) > 1e-12 if amount is not None and amount > 0 else False,
        "foundUpperNeighbor": bool(found_upper_neighbor),
        "refinedAroundPeak": bool(refined_around_peak),
        "comparisonBaseAmount": float(comparison_base_amount),
        "scannedCandidateCount": max(0, len(probe_records) - 1),
    }
    return best_analysis, best_amount, best_profit, probe_records, selection_meta


def _tail(text: str, limit: int = 1200) -> str:
    clean = str(text or "").strip()
    return clean[-limit:] if len(clean) > limit else clean


def _static_call_revert_detail(parsed: dict[str, Any]) -> str:
    raw = str(parsed.get("errorDetail") or parsed.get("detail") or parsed.get("error") or "").strip()
    return f"链上复核未通过：{raw}" if raw else "链上复核未通过：execution reverted"


def best_amount_calculation_from_route(route: dict[str, Any] | None, amount: float) -> dict[str, Any]:
    """Build a replayable best-amount calculation snapshot from route size analysis."""
    route = route if isinstance(route, dict) else {}
    symbols = _route_symbols(route)
    symbol = symbols[0] if symbols else None
    size = route.get("sizeAnalysis") if isinstance(route.get("sizeAnalysis"), dict) else {}
    recommended_amount = _safe_float(size.get("recommendedBorrowInputAmount"))
    selected_amount = _safe_float(amount)
    if selected_amount is None:
        selected_amount = recommended_amount
    probe_records = _probe_records_from_size_analysis(
        size=size,
        symbol=symbol,
        selected_amount=selected_amount,
        recommended_amount=recommended_amount,
    )
    rows = size.get("rows") if isinstance(size.get("rows"), list) else []
    selected_profit = None
    selected_basis = None
    selected_row_index = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        row_amount = _safe_float(row.get("borrowAmount") or row.get("referenceInputAmount") or row.get("amount"))
        if row_amount is None or selected_amount is None:
            continue
        if abs(float(row_amount) - float(selected_amount)) <= max(float(selected_amount), 1) * 1e-9:
            selected_profit, selected_basis = _row_profit_metric(row)
            selected_row_index = index
            break
    if selected_profit is None:
        best_records = [
            record
            for record in probe_records
            if record.get("isBest") and record.get("profitAmount") is not None
        ]
        if best_records and selected_amount is not None:
            best_record = best_records[0]
            selected_profit = best_record.get("profitAmount")
            selected_basis = best_record.get("profitBasis")
    return {
        "method": "static_route_size_analysis_probe_ladder",
        "requestedAmount": float(selected_amount) if selected_amount is not None else None,
        "requestedSymbol": symbol,
        "bestAmount": float(selected_amount) if selected_amount is not None else recommended_amount,
        "bestAmountSymbol": symbol,
        "selectionConfirmed": False,
        "selectionBasis": "static_route_size_analysis_initial",
        "selectionReason": "静态规模分析尚未完成链上/链下最优量确认；当前值按初始量处理",
        "selectionMetric": selected_profit,
        "selectionBasis": selected_basis,
        "selectedRowIndex": selected_row_index,
        "recommendedBorrowInputAmount": recommended_amount,
        "recommendedBorrowInputAmountReason": size.get("recommendedBorrowInputAmountReason"),
        "recommendedSelectionMetric": size.get("recommendedSelectionMetric"),
        "recommendedSelectionBasis": size.get("recommendedSelectionBasis"),
        "recommendedSelectionRowIndex": size.get("recommendedSelectionRowIndex"),
        "initialProbeAmount": float(selected_amount) if selected_amount is not None else recommended_amount,
        "initialProbeSymbol": symbol,
        "amountOptimizedFrom": float(selected_amount) if selected_amount is not None else recommended_amount,
        "amountOptimized": False,
        "amountOptimizationHint": None,
        "probeRecords": probe_records,
        "probe_records": probe_records,
        "rows": rows,
        "sizeAnalysis": size,
    }


__all__ = [
    "_amount_float",
    "_match_route",
    "_official_route_addresses",
    "_quote_failure_status",
    "_recommended_route_test_amount",
    "_routes_payload",
    "_route_symbols",
    "_static_call_revert_detail",
    "_step_failure_status",
    "_step_payload",
    "_tail",
    "best_amount_calculation_from_route",
    "normalize_route_symbols",
    "recommended_route_test_amount",
]
