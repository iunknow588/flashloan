"""Preflight, token risk, and route scoring helpers for unified direct execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def _route_group_token_risk_report(
    group: dict[str, Any],
    trades: list[dict[str, Any]],
    *,
    require_metadata: bool = False,
) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    route_tokens = sorted(
        {
            token.lower()
            for trade in trades
            if isinstance(trade, dict)
            for token in (trade.get("tokenX"), trade.get("tokenY"))
            if impl._normalize_pair_address(token)
        }
    )
    raw = None
    for key in ("tokenRisks", "token_risks", "tokenWhitelist", "token_whitelist"):
        value = group.get(key)
        if value not in (None, ""):
            raw = value
            break
    if raw in (None, ""):
        return {
            "provided": False,
            "broadcastable": not require_metadata,
            "routeTokens": route_tokens,
            "missingTokens": route_tokens if require_metadata else [],
            "tokens": [],
            "blocked": [],
        }

    entries: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for token, metadata in raw.items():
            item = dict(metadata) if isinstance(metadata, dict) else {}
            item.setdefault("token", token)
            entries.append(item)
    elif isinstance(raw, list):
        entries = [dict(item) for item in raw if isinstance(item, dict)]

    blocked: list[dict[str, Any]] = []
    normalized_entries: list[dict[str, Any]] = []
    for item in entries:
        token = impl._normalize_pair_address(item.get("token") or item.get("address"))
        if token and route_tokens and token.lower() not in route_tokens:
            continue
        reentrancy_risk = str(
            item.get("reentrancyRisk", item.get("reentrancy_risk", "none")) or "none"
        ).strip().lower()
        truncation_risk = str(
            item.get("decimalTruncationRisk", item.get("decimal_truncation_risk", "none")) or "none"
        ).strip().lower()
        normalized_item = {
            "token": token or str(item.get("token") or item.get("address") or ""),
            "reentrancyRisk": reentrancy_risk,
            "decimalTruncationRisk": truncation_risk,
        }
        normalized_entries.append(normalized_item)
        if reentrancy_risk not in {"none", "low"} or truncation_risk not in {"none", "low"}:
            blocked.append(normalized_item)
    covered_tokens = {str(item["token"]).lower() for item in normalized_entries if item["token"]}
    missing_tokens = [token for token in route_tokens if token not in covered_tokens]

    return {
        "provided": True,
        "broadcastable": not blocked and (not require_metadata or not missing_tokens),
        "routeTokens": route_tokens,
        "missingTokens": missing_tokens,
        "tokens": normalized_entries,
        "blocked": blocked,
    }


def _route_group_usdc_penalty(group: dict[str, Any], *names: str) -> int:
    from dex.intent_trade import direct_impl as impl

    raw = impl._first_value(group, names=tuple(names))
    if raw in (None, ""):
        return 0
    try:
        return impl._positive_int_value(raw)
    except ValueError:
        return 0


def _public_fallback_enabled(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> bool:
    from dex.intent_trade import direct_impl as impl

    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("allow_public_fallback", "allowPublicFallback", "public_fallback_enabled", "publicFallbackEnabled"),
    )
    if raw is not None:
        return impl._bool_value(raw)
    return impl._env_bool(
        "UNIFIED_EXECUTOR_ALLOW_PUBLIC_FALLBACK",
        "TRIANGULAR_ALLOW_PUBLIC_FALLBACK",
    )


def _private_relay_research_enabled(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> bool:
    from dex.intent_trade import direct_impl as impl

    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=(
            "private_relay_research_enabled",
            "privateRelayResearchEnabled",
            "private_first_enabled",
            "privateFirstEnabled",
        ),
    )
    if raw is not None:
        return impl._bool_value(raw)
    return impl._env_bool(
        "UNIFIED_EXECUTOR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "TRIANGULAR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "LIQUIDATION_PRIVATE_RELAY_RESEARCH_ENABLED",
    )


def _delivery_policy_report(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    private_relay_research = _private_relay_research_enabled(protocol, opportunity)
    return {
        "mode": "private_relay_research" if private_relay_research else "public_rpc_direct_after_fresh_gates",
        "privateRelayResearchEnabled": private_relay_research,
        "privateFirst": private_relay_research,
        "publicFallbackRequested": _public_fallback_enabled(protocol, opportunity) if private_relay_research else False,
        "publicFallbackRequiresRevalidation": private_relay_research,
        "privacyBoundary": (
            "deferred_to_cow_or_intent_layer"
            if not private_relay_research
            else "operator_supplied_private_rpc_research_only"
        ),
    }


def _triangular_trade_allowed(trade: dict[str, Any], usdc_address: str) -> bool:
    from dex.intent_trade import direct_impl as impl

    token_x = impl._normalize_pair_address(trade.get("tokenX"))
    token_y = impl._normalize_pair_address(trade.get("tokenY"))
    return bool(token_x and token_y and token_x != token_y and token_x != usdc_address and token_y != usdc_address)


def _non_usdc_cross_pool_enabled(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> bool:
    from dex.intent_trade import direct_impl as impl

    configured = impl._first_value(
        protocol,
        opportunity,
        names=("enable_non_usdc_cross_pool", "non_usdc_cross_pool_enabled", "allow_token_x_flashloan_cross_pool"),
    )
    if configured is not None:
        return impl._bool_value(configured, False)
    return impl._env_bool("TRIANGULAR_ENABLE_NON_USDC_CROSS_POOL", "TRIANGULAR_DIRECT_ENABLE_TOKEN_X_CROSS_POOL")


def _cross_pool_trade_allowed(
    trade: dict[str, Any],
    *,
    usdc_address: str,
    borrowable_addresses: set[str],
    allow_non_usdc: bool,
) -> bool:
    from dex.intent_trade import direct_impl as impl

    token_x = impl._normalize_pair_address(trade.get("tokenX"))
    token_y = impl._normalize_pair_address(trade.get("tokenY"))
    if not token_x or not token_y or token_x == token_y:
        return False
    if token_x == usdc_address:
        return True
    if not allow_non_usdc:
        return False
    if borrowable_addresses and token_x not in borrowable_addresses:
        return False
    return True


def _direct_execution_enabled(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> bool:
    from dex.intent_trade import direct_impl as impl

    configured = impl._first_value(
        protocol,
        opportunity,
        names=("execute_runtime_trade", "execute_selected_runtime_trade", "call_executor", "execute_with_aave"),
    )
    if configured is not None:
        return impl._bool_value(configured, False)
    return impl._env_bool(
        "TRIANGULAR_DIRECT_EXECUTE_RUNTIME_TRADE",
        "TRIANGULAR_DIRECT_EXECUTOR_ENABLED",
        "TRIANGULAR_AB_EXECUTOR_ENABLED",
    )


def _direct_execution_mode(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> str:
    from dex.intent_trade import direct_impl as impl

    configured = impl._first_value(
        protocol,
        opportunity,
        names=("execution_mode", "direct_execution_mode", "runtime_execution_mode"),
    ) or impl._env("TRIANGULAR_DIRECT_EXECUTION_MODE", default="auto")
    mode = str(configured or "auto").strip().lower().replace("-", "_")
    aliases = {
        "a": "auto",
        "abc": "auto",
        "ordered": "ordered_auto",
        "ordered_status": "ordered_auto",
        "status_order": "ordered_auto",
        "auto_select": "auto",
        "two_or_three_pool": "auto",
        "b": "triangular",
        "three_pool": "triangular",
        "triangular_only": "triangular",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"auto", "ordered_auto", "triangular"}:
        raise ValueError("TRIANGULAR_DIRECT_EXECUTION_MODE must be auto, ordered_auto, or triangular")
    return mode


def _runtime_execution_params(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> dict[str, int]:
    from dex.intent_trade import direct_impl as impl

    amount = impl._positive_int_value(
        impl._first_value(
            protocol,
            opportunity,
            names=("execution_amount_usdc", "execution_amount", "borrow_amount", "amount"),
        )
        or impl._env(
            "TRIANGULAR_BORROW_AMOUNT_UNITS",
            "TRIANGULAR_EXECUTION_AMOUNT_USDC_BASE_UNITS",
            "TRIANGULAR_EXECUTION_AMOUNT",
            "TRIANGULAR_FLASHLOAN_AMOUNT_USDC",
        )
    )
    amount_out_min = impl._positive_int_value(
        impl._first_value(
            protocol,
            opportunity,
            names=("amountOutMinUsdc", "amount_out_min_usdc", "min_final_usdc"),
        )
        or impl._env(
            "TRIANGULAR_AMOUNT_OUT_MIN_USDC",
            "TRIANGULAR_EXECUTION_AMOUNT_OUT_MIN_USDC",
            default=str(amount),
        )
    )
    explicit_min_profit = impl._first_value(
        protocol,
        opportunity,
        names=("minProfitUsdc", "min_profit_usdc", "execution_min_profit_usdc"),
    )
    expected_profit_usdc = impl._first_value(
        protocol,
        opportunity,
        names=("expected_profit_usdc", "expected_profit_amount"),
    )
    if explicit_min_profit is not None:
        min_profit = impl._positive_int_value(explicit_min_profit, default=1)
    elif expected_profit_usdc is not None:
        min_profit = impl._usdc_decimal_to_base_units(expected_profit_usdc)
    else:
        min_profit = impl._positive_int_value(
            impl._env(
                "TRIANGULAR_MIN_PROFIT_USDC_BASE_UNITS",
                "TRIANGULAR_MIN_PROFIT_USDC",
                "TRIANGULAR_EXECUTION_MIN_PROFIT_USDC",
                default="1",
            ),
            default=1,
        )
    min_net_profit = impl._positive_int_value(
        impl._env(
            "TRIANGULAR_MIN_NET_PROFIT_USDC_BASE_UNITS",
            "TRIANGULAR_MIN_NET_PROFIT_USDC",
            default="1",
        ),
        default=1,
    )
    min_profit = max(min_profit, min_net_profit)
    deadline = impl._positive_int_value(
        impl._first_value(protocol, opportunity, names=("deadline", "execution_deadline"))
        or impl._env("TRIANGULAR_EXECUTION_DEADLINE")
    )
    if deadline == 0:
        ttl = impl._positive_int_value(impl._env("TRIANGULAR_EXECUTION_DEADLINE_SECONDS", default="300"), default=300)
        deadline = int(datetime.now(timezone.utc).timestamp()) + max(1, ttl)
    usdc_to_token_x_fee = impl._positive_int_value(
        impl._first_value(
            protocol,
            opportunity,
            names=("usdcToTokenXFee", "usdc_to_token_x_fee", "usdc_to_x_fee"),
        )
        or impl._env("TRIANGULAR_USDC_TO_TOKEN_X_FEE", default="3000"),
        default=3000,
    )
    token_y_to_usdc_fee = impl._positive_int_value(
        impl._first_value(
            protocol,
            opportunity,
            names=("tokenYToUsdcFee", "token_y_to_usdc_fee", "y_to_usdc_fee"),
        )
        or impl._env("TRIANGULAR_TOKEN_Y_TO_USDC_FEE", default="3000"),
        default=3000,
    )
    if amount == 0:
        raise ValueError("execution amount is required when executor mode is enabled")
    if min_profit == 0:
        raise ValueError("min profit is required when executor mode is enabled")
    if usdc_to_token_x_fee <= 0 or usdc_to_token_x_fee > 16_777_215:
        raise ValueError("TRIANGULAR_USDC_TO_TOKEN_X_FEE must be between 1 and 16777215")
    if token_y_to_usdc_fee <= 0 or token_y_to_usdc_fee > 16_777_215:
        raise ValueError("TRIANGULAR_TOKEN_Y_TO_USDC_FEE must be between 1 and 16777215")
    return {
        "amount": amount,
        "deadline": deadline,
        "amountOutMinUsdc": amount_out_min,
        "minProfitUsdc": min_profit,
        "usdcToTokenXFee": usdc_to_token_x_fee,
        "tokenYToUsdcFee": token_y_to_usdc_fee,
    }


def _runtime_cross_pool_execution_params(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None,
    triangular_params: dict[str, int] | None = None,
) -> dict[str, int]:
    from dex.intent_trade import direct_impl as impl

    fallback_amount = str((triangular_params or {}).get("amount") or "")
    amount = impl._positive_int_value(
        impl._first_value(
            protocol,
            opportunity,
            names=("cross_pool_amount", "crossPoolAmount", "cross_pool_borrow_amount", "token_x_borrow_amount"),
        )
        or impl._env(
            "TRIANGULAR_CROSS_POOL_BORROW_AMOUNT_UNITS",
            "TRIANGULAR_CROSS_POOL_AMOUNT_UNITS",
            default=fallback_amount,
        )
    )
    min_final = impl._positive_int_value(
        impl._first_value(protocol, opportunity, names=("minFinalTokenX", "min_final_token_x", "cross_pool_min_final"))
        or impl._env("TRIANGULAR_CROSS_POOL_MIN_FINAL_TOKEN_X", default=str(amount))
    )
    min_profit = impl._positive_int_value(
        impl._first_value(
            protocol,
            opportunity,
            names=("minProfitTokenX", "min_profit_token_x", "cross_pool_min_profit"),
        )
        or impl._env("TRIANGULAR_CROSS_POOL_MIN_PROFIT_TOKEN_X_BASE_UNITS", default="1"),
        default=1,
    )
    deadline = impl._positive_int_value(
        impl._first_value(protocol, opportunity, names=("cross_pool_deadline", "crossPoolDeadline"))
        or impl._env("TRIANGULAR_CROSS_POOL_DEADLINE")
    )
    if deadline == 0:
        deadline = int((triangular_params or {}).get("deadline") or 0)
    if deadline == 0:
        ttl = impl._positive_int_value(impl._env("TRIANGULAR_EXECUTION_DEADLINE_SECONDS", default="300"), default=300)
        deadline = int(datetime.now(timezone.utc).timestamp()) + max(1, ttl)
    if amount == 0:
        raise ValueError("cross-pool amount is required when auto executor mode is enabled")
    if min_profit == 0:
        raise ValueError("cross-pool min profit is required when auto executor mode is enabled")
    return {
        "amount": amount,
        "deadline": deadline,
        "minFinalTokenX": min_final,
        "minProfitTokenX": min_profit,
    }


def _route_symbols_match(route_symbols: Any, route_path: list[Any]) -> bool:
    if not isinstance(route_symbols, (list, tuple)) or not route_path:
        return False
    left = [str(item or "").strip().upper() for item in route_symbols]
    right = [str(item or "").strip().upper() for item in route_path]
    return bool(left and left == right)


def _runtime_group_intent_score(group: dict[str, Any], route_path: list[Any]) -> int:
    if not isinstance(group, dict) or len(route_path) < 3:
        return 0
    normalized_path = [str(item or "").strip().upper() for item in route_path if str(item or "").strip()]
    route_key = str(group.get("routeKey") or "").strip().upper()
    route_kind = str(group.get("routeKind") or "").strip().lower()
    trades = group.get("trades") if isinstance(group.get("trades"), list) else []

    if len(normalized_path) >= 4:
        forward_key = f"{normalized_path[1]}:{normalized_path[2]}"
        reverse_key = f"{normalized_path[2]}:{normalized_path[1]}"
        if any(_route_symbols_match(trade.get("routeSymbols") or trade.get("route_symbols"), normalized_path) for trade in trades):
            return 300
        if route_kind == "usdc_triangular" and route_key == forward_key:
            return 250
        if route_kind == "usdc_triangular" and route_key == reverse_key:
            return 200
    if len(normalized_path) >= 3 and normalized_path[0] == "USDC":
        direct_key = f"USDC:{normalized_path[1]}"
        if route_kind == "usdc_cross_pool" and route_key == direct_key:
            return 150
    return 0


def _select_runtime_group_for_intent(
    route_groups: list[dict[str, Any]],
    route_path: list[Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    normalized_path = [str(item or "").strip().upper() for item in route_path if str(item or "").strip()]
    scored: list[tuple[int, float, int, dict[str, Any]]] = []
    for index, group in enumerate(route_groups):
        score = _runtime_group_intent_score(group, route_path)
        if score <= 0:
            continue
        scored.append((score, float(group.get("score") or 0.0), -index, group))
    if scored:
        selected = max(scored, key=lambda item: (item[0], item[1], item[2]))
        return selected[3], {
            "matched": True,
            "matchScore": str(selected[0]),
            "routeKey": str(selected[3].get("routeKey") or ""),
            "routeKind": str(selected[3].get("routeKind") or ""),
            "routePath": normalized_path,
        }

    preferred_groups = [
        group for group in route_groups
        if group.get("routeKind") == "usdc_triangular" and len(group.get("trades") or []) >= 3
    ]
    if preferred_groups:
        return preferred_groups[0], {
            "matched": False,
            "matchScore": "0",
            "routeKey": str(preferred_groups[0].get("routeKey") or ""),
            "routeKind": str(preferred_groups[0].get("routeKind") or ""),
            "routePath": normalized_path,
            "fallback": "first_triangular_group",
        }
    if route_groups:
        return route_groups[0], {
            "matched": False,
            "matchScore": "0",
            "routeKey": str(route_groups[0].get("routeKey") or ""),
            "routeKind": str(route_groups[0].get("routeKind") or ""),
            "routePath": normalized_path,
            "fallback": "first_route_group",
        }
    return None, {"matched": False, "matchScore": "0", "routeKey": "", "routeKind": "", "routePath": normalized_path}
