from __future__ import annotations

from decimal import Decimal
from typing import Any

from dex.runtime.dex_quote_evidence_common import (
    _as_dict,
    _int_or_none,
    _norm_symbol,
    _pool_map,
    _positive_decimal_or_none,
    _raw_from_human,
    _text,
)

def _offchain_initial_probe_amount_raw(
    route: dict[str, Any],
    *,
    input_decimals: int,
    analysis: dict[str, Any] | None = None,
    pools: dict[str, dict[str, Any]] | None = None,
    price_map: dict[str, float] | None = None,
) -> tuple[int, str, str, list[str]]:
    """估算链下初始探测借币量，是所有初始探测量的唯一起点。

    当路由/规模分析已经显式携带 initialProbeAmount、offchainInitialProbeAmount、
    requestedAmount 或 recommendedBorrowInputAmount 时，优先使用这些外部给定值，
    避免把用户/上游已经确定的初始量覆盖掉。
    规则：
    - 只使用传入 price_map 的可信稳定币/稳定池价格锚点；
      绝不从 referenceInputAmount/notionalUsd 反推 TZ_USDT 价格；
    - 没有显式初始量且没有可信价格时，使用一个基础代币单位 10 ** input_decimals；
    - 仅在推导默认初始量时，若路由包含池子余额信息，会将初始探测值限制在池子流动性的 2% 以内，
      避免一开始就把过度资金穿透整个池子；
    - 无法计算时返回 0，由调用方决定是否回退到请求量。
    """
    route_analysis = analysis if isinstance(analysis, dict) else _as_dict(route.get("quoteAnalysis"))
    size_analysis = (
        _as_dict(route_analysis.get("sizeAnalysis"))
        if isinstance(route_analysis.get("sizeAnalysis"), dict)
        else _as_dict(route.get("sizeAnalysis"))
    )
    pools = pools or _pool_map(route)
    price_map = price_map or {}
    input_symbol = _norm_symbol(
        route_analysis.get("inputSymbol")
        or (route.get("routeSymbols") or [None])[0]
        or route.get("tokenXSymbol")
    )
    explicit_amount_raw = _explicit_initial_probe_amount_raw(
        route=route,
        route_analysis=route_analysis,
        size_analysis=size_analysis,
        input_decimals=input_decimals,
    )
    if explicit_amount_raw is not None and explicit_amount_raw > 0:
        return (
            explicit_amount_raw,
            "borrow_token",
            "requested_borrow_amount_fallback",
            ["requested_borrow_amount_fallback"],
        )
    input_price = price_map.get(input_symbol) if input_symbol else None
    if input_price is not None and input_price > 0:
        amount_raw = max(1, _raw_from_human(Decimal(str(1000.0 / input_price)), input_decimals))
        reason = "stablecoin_usd_1000u_anchor"
        reasons = ["stablecoin_usd_1000u_anchor"]
    elif input_decimals is not None and input_decimals > 0:
        amount_raw = max(1, int(Decimal(10) ** int(input_decimals)))
        reason = "one_basic_unit_fallback"
        reasons = ["one_basic_unit_fallback"]
    else:
        return 0, "borrow_token", "no_offchain_initial_anchor", ["no_offchain_initial_anchor"]
    one_basic_unit_raw = (
        max(1, int(Decimal(10) ** int(input_decimals)))
        if input_decimals is not None and input_decimals > 0
        else 1
    )
    liquidity_cap_raw, liquidity_reason = _liquidity_cap_raw(pools, input_symbol, input_decimals)
    if liquidity_cap_raw is not None and liquidity_cap_raw > 0 and amount_raw > liquidity_cap_raw:
        # 2% liquidity cap is only a conservative search starting point. It
        # must never force the initial probe below one basic token unit.
        amount_raw = max(int(liquidity_cap_raw), one_basic_unit_raw)
        reason = liquidity_reason or "liquidity_cap"
        reasons.append(liquidity_reason or "liquidity_cap")
    return amount_raw, "borrow_token", reason, reasons


def _explicit_initial_probe_amount_raw(
    *,
    route: dict[str, Any],
    route_analysis: dict[str, Any],
    size_analysis: dict[str, Any],
    input_decimals: int,
) -> int | None:
    """Return a caller/upstream-provided initial probe amount when present.

    Explicit fields are preferred over automatic anchors. This is the fix for
    cases where an external initial amount (for example 0.001 TZ or 1 TZ) was
    ignored and the runtime recomputed 1000 / price or one basic unit instead.
    """
    raw_candidates = [
        route.get("offchainInitialProbeAmountRaw"),
        route.get("initialProbeAmountRaw"),
        route.get("requestedAmountRaw"),
        route_analysis.get("offchainInitialProbeAmountRaw"),
        route_analysis.get("initialProbeAmountRaw"),
        route_analysis.get("requestedAmountRaw"),
        size_analysis.get("offchainInitialProbeAmountRaw"),
        size_analysis.get("initialProbeAmountRaw"),
        size_analysis.get("recommendedBorrowInputAmountRaw"),
    ]
    for candidate in raw_candidates:
        try:
            raw = int(str(candidate))
        except (TypeError, ValueError):
            continue
        if raw > 0:
            return raw
    human_candidates = [
        route.get("offchainInitialProbeAmount"),
        route.get("initialProbeAmount"),
        route.get("requestedAmount"),
        route_analysis.get("offchainInitialProbeAmount"),
        route_analysis.get("initialProbeAmount"),
        route_analysis.get("requestedAmount"),
        route_analysis.get("amountOptimizedFrom"),
        size_analysis.get("offchainInitialProbeAmount"),
        size_analysis.get("initialProbeAmount"),
        size_analysis.get("recommendedBorrowInputAmount"),
    ]
    for candidate in human_candidates:
        positive = _positive_decimal_or_none(candidate)
        if positive is None or positive <= 0:
            continue
        raw = max(1, _raw_from_human(positive, input_decimals))
        if raw > 0:
            return raw
    return None

def _step_token_rate(step: dict[str, Any]) -> Decimal | None:
    input_amount = _positive_decimal_or_none(step.get("referenceInputAmount") or step.get("inputAmount") or step.get("amountIn"))
    output_amount = _positive_decimal_or_none(step.get("referenceOutputAmount") or step.get("outputAmount") or step.get("amountOut"))
    if input_amount is None or output_amount is None or input_amount <= 0:
        return None
    return output_amount / input_amount

def _pool_borrow_side_amount(pool: dict[str, Any], input_symbol: str) -> Decimal | None:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    symbol = _norm_symbol(input_symbol)
    for index in (0, 1):
        if _norm_symbol(pool.get(f"token{index}Symbol")) == symbol:
            amount = balances.get(f"token{index}") if balances else pool.get(f"token{index}Amount")
            return _positive_decimal_or_none(amount)
    return None

def _pool_borrow_liquidity_raw(
    pools: dict[str, dict[str, Any]] | None,
    input_symbol: str,
    input_decimals: int,
) -> int | None:
    total: Decimal | None = None
    for pool in (pools or {}).values():
        if not isinstance(pool, dict):
            continue
        side_amount = _pool_borrow_side_amount(pool, input_symbol)
        if side_amount is None or side_amount <= 0:
            continue
        total = (total or Decimal("0")) + side_amount
    if total is None:
        return None
    return max(1, _raw_from_human(total, input_decimals))

def _liquidity_cap_raw(
    pools: dict[str, dict[str, Any]] | None,
    input_symbol: str,
    input_decimals: int,
) -> tuple[int | None, str | None]:
    pool_cap_raw = _pool_borrow_liquidity_raw(pools, input_symbol, input_decimals)
    if pool_cap_raw is not None:
        pool_cap_raw = max(1, pool_cap_raw // 50)
    if pool_cap_raw is not None:
        return pool_cap_raw, "liquidity_cap_pool_reserve_2pct"
    return None, None

def _route_size_advice(route: dict[str, Any]) -> tuple[dict[str, Any], int | None, int | None, int | None]:
    size_analysis = _as_dict(route.get("sizeAnalysis"))
    if not size_analysis:
        size_analysis = _as_dict(_as_dict(route.get("quoteAnalysis")).get("sizeAnalysis"))
    if not size_analysis:
        size_analysis = _as_dict(_as_dict(route.get("overviewRoute")).get("sizeAnalysis"))
    execution_probe = _as_dict(
        size_analysis.get("executionSizeProbe")
        or size_analysis.get("routeExecutionSizeProbe")
    )
    input_amount_unit = _text(size_analysis.get("inputAmountUnit")).lower()
    max_executable_raw = _int_or_none(
        (
            size_analysis.get("maxExecutableAmountRaw")
            or execution_probe.get("maxExecutableAmountRaw")
            or route.get("maxExecutableAmountRaw")
        )
        if input_amount_unit != "per_hop"
        else None
    )
    first_failure_raw = _int_or_none(
        (
            size_analysis.get("firstFailureAmountRaw")
            or execution_probe.get("firstFailureAmountRaw")
            or route.get("firstFailureAmountRaw")
        )
        if input_amount_unit != "per_hop"
        else None
    )
    recommended_raw = _int_or_none(
        size_analysis.get("offchainInitialProbeAmountRaw")
        or size_analysis.get("initialProbeAmountRaw")
        or size_analysis.get("recommendedBorrowInputAmountRaw")
        or execution_probe.get("recommendedBorrowInputAmountRaw")
        or execution_probe.get("offchainInitialProbeAmountRaw")
        or route.get("recommendedBorrowInputAmountRaw")
    )
    return execution_probe, max_executable_raw, first_failure_raw, recommended_raw

def _transaction_development(ready: bool, blockers: list[str]) -> dict[str, Any]:
    if ready:
        return {
            "status": "ready_for_transaction_preflight_development",
            "executionEnabled": False,
            "nextModules": [
                "build flash loan borrow entry",
                "verify route and generate AMM swap calldata",
                "add minimum repayment and slippage protection",
                "完成链上复核确认链上可执行性后再发起链上执行",
            ],
        }
    return {
        "status": "blocked_before_transaction_development",
        "executionEnabled": False,
        "blockers": blockers,
    }
