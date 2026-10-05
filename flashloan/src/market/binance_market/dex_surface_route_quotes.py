"""Quote calculation helpers for DEX surface routes."""

from __future__ import annotations

from typing import Any

from bot.execution.uniswap_v4 import (
    build_v4_quote_request,
    quote_v4_exact_input_single as _default_quote_v4_exact_input_single,
)
from bot.execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter as _default_load_v4_sdk_adapter
from core.chain_data_registry import chain_data_by_chain_id
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID as _ROBINHOOD_CHAIN_ID,
    ROBINHOOD_TESTNET_CHAIN_ID as _ROBINHOOD_TESTNET_CHAIN_ID,
)
from market.binance_market.dex_surface_graph import (
    _route_addresses_from_tokens,
    _route_pools,
    _route_symbols_from_tokens,
)
from market.binance_market.dex_surface_types import (
    _human_from_raw,
    _is_stable_anchor,
    _norm_symbol,
    _number,
    _optional_number,
    _pool_tokens,
    _raw_from_human,
    _route_symbol_allowed,
)
from market.binance_market.dex_surface_v4 import (
    _pool_token_decimals_trusted,
    _static_depth_available,
    _v4_live_token_metadata_enabled,
    _v4_spot_rate,
    _v4_token_decimals,
)
from market.binance_market.dex_surface_edge_quotes import _best_edge_quote, _edge_quote
from market.binance_market.dex_route_identity import missing_route_identity_analysis, route_address_identity_issues
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD

QUOTE_PROBE_NOTIONAL_USD = 1000.0
DEFAULT_FLASH_LOAN_FEE_BPS = 5.0
DEFAULT_GAS_RESERVE_USD = 1.0
ROBINHOOD_CHAIN_ID = _ROBINHOOD_CHAIN_ID
ROBINHOOD_TESTNET_CHAIN_ID = _ROBINHOOD_TESTNET_CHAIN_ID
_ROBINHOOD_CHAIN_IDS = {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}


def _chain_network_name(chain_id: int | None) -> str:
    if chain_id is None:
        return ""
    row = chain_data_by_chain_id(chain_id) or {}
    return str(row.get("network") or "").strip().lower()


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


def _v4_live_quote_enabled(chain_id: int | None = None) -> bool:
    try:
        from market.binance_market import dex_surfaces

        compatibility = getattr(dex_surfaces, "_v4_live_quote_enabled", None)
        if callable(compatibility) and getattr(compatibility, "__module__", "") != __name__:
            if chain_id is None:
                return bool(compatibility())
            try:
                return bool(compatibility(chain_id=chain_id))
            except TypeError:
                return bool(compatibility())
    except (AttributeError, ImportError, TypeError):
        pass
    import os
    raw = os.getenv("DEX_V4_LIVE_QUOTE_ENABLED")
    if raw is None:
        return int(chain_id or 0) in _ROBINHOOD_CHAIN_IDS
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_v4_sdk_adapter(*args: Any, **kwargs: Any):
    compatibility = None
    try:
        from market.binance_market import dex_surfaces
        compatibility = getattr(dex_surfaces, "load_v4_sdk_adapter", None)
    except (AttributeError, ImportError, TypeError):
        compatibility = None
    if callable(compatibility) and compatibility is not load_v4_sdk_adapter:
        return compatibility(*args, **kwargs)
    return _default_load_v4_sdk_adapter(*args, **kwargs)


def quote_v4_exact_input_single(*args: Any, **kwargs: Any):
    compatibility = None
    try:
        from market.binance_market import dex_surfaces
        compatibility = getattr(dex_surfaces, "quote_v4_exact_input_single", None)
    except (AttributeError, ImportError, TypeError):
        compatibility = None
    if callable(compatibility) and compatibility is not quote_v4_exact_input_single:
        return compatibility(*args, **kwargs)
    return _default_quote_v4_exact_input_single(*args, **kwargs)


def _route_quote_analysis(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
    notional_usd: float = QUOTE_PROBE_NOTIONAL_USD,
    token_basis_input_amount: float | None = None,
) -> dict[str, Any]:
    strict_live_price_only = chain_id in _ROBINHOOD_CHAIN_IDS
    overview_route = route.get("overviewRoute") or {}
    route_tokens = [token for token in route.get("routeTokenPath") or overview_route.get("pathTokens") or [] if isinstance(token, dict)]
    route_symbols = _route_symbols_from_tokens(route_tokens) if route_tokens else list(overview_route.get("pathSymbols") or route.get("routeSymbols") or [])
    route_addresses = _route_addresses_from_tokens(route_tokens) if route_tokens else [
        str(item or "").strip()
        for item in route.get("routeAddresses") or overview_route.get("pathAddresses") or []
        if str(item or "").strip()
    ]
    pools = _route_pools(route)
    static_depth_available = bool(pools) and all(_static_depth_available(pool) for pool in pools)
    warnings: list[str] = []
    if route_symbols and not route_tokens:
        warnings.append("route_identity_symbol_fallback")
    invalid_symbols = [symbol for symbol in route_symbols if not _route_symbol_allowed(symbol)]
    route_address_issues = route_address_identity_issues(route_symbols, route_addresses)
    if route_address_issues and not route_tokens:
        return missing_route_identity_analysis(
            route_symbols=route_symbols,
            route_addresses=route_addresses,
            warnings=warnings,
            notional_usd=notional_usd,
        )
    if invalid_symbols:
        warnings.append(f"invalid_route_symbol:{','.join(invalid_symbols)}")
        return {
            "method": "static_amm_depth_probe",
            "notionalUsd": notional_usd,
            "inputSymbol": route_symbols[0] if route_symbols else None,
            "outputSymbol": route_symbols[-1] if route_symbols else None,
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "complete": False,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "stepCount": 0,
            "steps": [],
            "finalOutputUsd": None,
            "referenceFinalOutputUsd": None,
            "grossProfitUsd": None,
            "referenceGrossProfitUsd": None,
            "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
            "flashLoanFeeUsd": notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0,
            "gasReserveUsd": DEFAULT_GAS_RESERVE_USD,
            "minProfitUsd": DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD,
            "offchainMinProfitUsd": DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD,
            "submissionMinProfitUsd": notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0,
            "submissionNetProfitUsd": None,
            "offchainNetProfitUsd": None,
            "netProfitUsd": None,
            "referenceNetProfitUsd": None,
            "netProfitBps": None,
            "referenceNetProfitBps": None,
            "profitable": False,
            "executableCandidate": False,
            "sdkAdapterRequiredStepCount": 0,
            "recommendation": {
                "level": "blocked",
                "action": "拒绝污染路径",
                "reasons": ["路径包含明显的污染符号，不能进入报价或链上复核。"],
            },
            "warnings": warnings,
        }
    if len(route_symbols) < 2:
        warnings.append("route_symbols_missing")
    input_symbol = route_symbols[0] if route_symbols else None
    output_symbol = route_symbols[-1] if route_symbols else None
    current_usd = float(notional_usd)
    closed_loop_same_symbol = bool(input_symbol and output_symbol == input_symbol)
    requested_token_basis_input_amount = token_basis_input_amount
    if closed_loop_same_symbol and requested_token_basis_input_amount is not None:
        token_basis_input_amount = requested_token_basis_input_amount
    elif closed_loop_same_symbol and input_symbol and price_map.get(input_symbol):
        token_basis_input_amount = current_usd / price_map[input_symbol]
    elif closed_loop_same_symbol:
        token_basis_input_amount = 1.0
    else:
        token_basis_input_amount = None
    current_amount = (
        token_basis_input_amount
        if token_basis_input_amount is not None
        else current_usd / price_map[input_symbol]
        if input_symbol and price_map.get(input_symbol)
        else token_basis_input_amount
    )
    steps: list[dict[str, Any]] = []
    for index, (from_symbol, to_symbol) in enumerate(zip(route_symbols, route_symbols[1:]), start=1):
        from_address = route_addresses[index - 1] if index - 1 < len(route_addresses) else ""
        to_address = route_addresses[index] if index < len(route_addresses) else ""
        quote = _best_edge_quote(
            pools=pools,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            from_address=from_address,
            to_address=to_address,
            input_amount=current_amount,
            input_usd=current_usd,
            price_map=price_map,
            chain_id=chain_id,
        )
        if quote is None:
            steps.append(
                {
                    "hop": index,
                    "fromSymbol": from_symbol,
                    "toSymbol": to_symbol,
                    "tokenIn": from_address,
                    "tokenOut": to_address,
                    "status": "missing_pool_or_depth",
                    "inputAmount": current_amount,
                    "inputUsd": current_usd,
                    "outputAmount": None,
                    "outputUsd": None,
                }
            )
            warnings.append(f"hop_{index}_quote_missing")
            break
        quote["hop"] = index
        quote["status"] = "quoted"
        quote.setdefault("tokenIn", from_address)
        quote.setdefault("tokenOut", to_address)
        steps.append(quote)
        trust = str(quote.get("executionTrust") or "").strip().lower()
        has_reference_output = quote.get("referenceOutputUsd") is not None or quote.get("referenceOutputAmount") is not None
        if trust in {
            "live_quote_failed",
            "not_enough_liquidity",
            "pool_not_initialized",
            "rpc_verification_failed",
            "rpc_verification_timeout",
        }:
            failure_warning = (
                f"hop_{index}_not_enough_liquidity"
                if trust == "not_enough_liquidity"
                else f"hop_{index}_pool_not_initialized"
                if trust == "pool_not_initialized"
                else f"hop_{index}_rpc_verification_timeout"
                if trust == "rpc_verification_timeout"
                else f"hop_{index}_rpc_verification_failed"
                if trust == "rpc_verification_failed"
                else f"hop_{index}_live_quote_failed"
            )
            warnings.append(failure_warning)
            if quote.get("liveQuoteError"):
                warnings.append(f"hop_{index}_live_quote_error:{quote['liveQuoteError']}")
            warnings.append(f"route_truncated_at_hop_{index}_{trust}")
            break
        if trust in {"reference_only", "sdk_adapter_required"} and not has_reference_output:
            warnings.append(f"hop_{index}_requires_live_quoter")
            warnings.append(f"route_truncated_at_hop_{index}_requires_live_quoter")
            break
        if trust in {"reference_only", "sdk_adapter_required"}:
            warnings.append(f"hop_{index}_reference_quote_requires_live_quoter")
        if chain_id is None and trust == "sdk_adapter_required" and quote.get("trustedUsdPrice") is False:
            warnings.append(f"route_truncated_at_hop_{index}_requires_live_quoter")
            break
        step_output_usd = quote.get("outputUsd")
        if step_output_usd is None:
            step_output_usd = quote.get("referenceOutputUsd")
        current_usd = _number(step_output_usd)
        step_output_amount = quote.get("outputAmount")
        if step_output_amount is None:
            step_output_amount = quote.get("referenceOutputAmount")
        current_amount = step_output_amount if step_output_amount is not None else (
            current_usd / price_map[to_symbol] if price_map.get(to_symbol) else None
        )
        if current_amount is not None and _number(current_amount) <= 0:
            warnings.append(f"route_truncated_at_hop_{index}_zero_output")
            break
    complete = len(steps) == max(0, len(route_symbols) - 1) and all(step.get("status") == "quoted" for step in steps)
    requires_live_quoter = any(
        step.get("executionTrust")
        in {
            "reference_only",
            "live_quote_failed",
            "not_enough_liquidity",
            "pool_not_initialized",
            "rpc_verification_failed",
            "rpc_verification_timeout",
        }
        for step in steps
    )
    adapter_required = any(step.get("executionTrust") == "sdk_adapter_required" for step in steps)
    requires_live_quoter = requires_live_quoter or adapter_required
    untrusted_usd_reference = any(step.get("trustedUsdPrice") is False for step in steps)
    assumed_token_decimals = any(step.get("tokenDecimalsTrusted") is not True for step in steps)
    if requires_live_quoter:
        warnings.append("reference_price_step_requires_live_quoter")
    if untrusted_usd_reference:
        warnings.append("reference_usd_price_missing_or_untrusted")
    if assumed_token_decimals:
        warnings.append("v4_token_decimals_missing_requires_metadata")
    reference_final_output_usd = current_usd if complete and not untrusted_usd_reference else None
    reference_gross_profit_usd = (reference_final_output_usd - notional_usd) if reference_final_output_usd is not None else None
    flash_loan_fee_usd = notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
    submission_min_profit_usd = flash_loan_fee_usd
    submission_net_profit_usd = (
        reference_gross_profit_usd - flash_loan_fee_usd if reference_gross_profit_usd is not None else None
    )
    reference_net_profit_usd = (
        submission_net_profit_usd - DEFAULT_GAS_RESERVE_USD if submission_net_profit_usd is not None else None
    )
    reference_net_profit_bps = (
        reference_net_profit_usd / notional_usd * 10_000.0
        if reference_net_profit_usd is not None and notional_usd > 0
        else None
    )
    token_basis_complete = bool(
        complete
        and token_basis_input_amount is not None
        and current_amount is not None
        and input_symbol
        and output_symbol == input_symbol
    )
    reference_final_output_amount = current_amount if token_basis_complete else None
    reference_gross_profit_amount = (
        reference_final_output_amount - token_basis_input_amount
        if reference_final_output_amount is not None and token_basis_input_amount is not None
        else None
    )
    reference_flash_loan_fee_amount = (
        token_basis_input_amount * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
        if token_basis_input_amount is not None
        else None
    )
    reference_net_profit_amount = (
        reference_gross_profit_amount - reference_flash_loan_fee_amount
        if reference_gross_profit_amount is not None and reference_flash_loan_fee_amount is not None
        else None
    )
    reference_gross_return_bps = (
        reference_gross_profit_amount / token_basis_input_amount * 10_000.0
        if reference_gross_profit_amount is not None and token_basis_input_amount
        else None
    )
    reference_net_return_bps = (
        reference_net_profit_amount / token_basis_input_amount * 10_000.0
        if reference_net_profit_amount is not None and token_basis_input_amount
        else None
    )
    depth_impact_bps = [
        _optional_number(step.get("priceImpactBps"))
        for step in steps
        if step.get("priceImpactBps") is not None
    ]
    max_static_depth_impact_bps = max(depth_impact_bps) if depth_impact_bps else None
    depth_status = "available" if static_depth_available else "unavailable"
    if static_depth_available and max_static_depth_impact_bps is not None and max_static_depth_impact_bps > 10.0:
        depth_status = "exceeds_10_bps"
    live_price_verified_now = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    if not static_depth_available and not live_price_verified_now:
        warnings.append("static_depth_evidence_unavailable")
    elif depth_status == "exceeds_10_bps" and not live_price_verified_now:
        warnings.append("static_price_impact_exceeds_10_bps_reduce_borrow_amount")
    borrow_asset_is_stable = bool(input_symbol and _is_stable_anchor(input_symbol))
    borrow_asset_class = "stable" if borrow_asset_is_stable else "non_stable"
    token_basis_trusted = bool(token_basis_complete and not assumed_token_decimals)
    token_basis_reference_profitable = bool(reference_net_profit_amount is not None and reference_net_profit_amount > 0)
    token_basis_profitable = bool(token_basis_trusted and token_basis_reference_profitable)
    non_stable_chain_submission_candidate = bool(token_basis_profitable and not borrow_asset_is_stable)
    executable_ready = bool(complete and not requires_live_quoter)
    final_output_usd = reference_final_output_usd if executable_ready else None
    gross_profit_usd = reference_gross_profit_usd if executable_ready else None
    net_profit_usd = (
        gross_profit_usd - flash_loan_fee_usd - DEFAULT_GAS_RESERVE_USD if gross_profit_usd is not None else None
    )
    net_profit_bps = net_profit_usd / notional_usd * 10_000.0 if net_profit_usd is not None and notional_usd > 0 else None
    min_profit_usd = float(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD)
    fully_live_adapter = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    if strict_live_price_only and not fully_live_adapter:
        warnings.append("robinhood_live_price_only_enforced")
        requires_live_quoter = True
        executable_ready = False
        final_output_usd = None
        gross_profit_usd = None
        net_profit_usd = None
        net_profit_bps = None
        non_stable_chain_submission_candidate = False
    all_live_adapter_steps = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    live_price_verified = bool(
        all_live_adapter_steps
        and (
            (token_basis_complete and token_basis_trusted)
            or all(step.get("trustedUsdPrice") is True for step in steps)
        )
    )
    live_quote_failures = [
        f"第 {step.get('hop') or '?'} 跳链上报价失败（{step.get('executionTrust') or 'live_quote_failed'}）：{step.get('liveQuoteError')}"
        for step in steps
        if step.get("executionTrust")
        in {
            "live_quote_failed",
            "not_enough_liquidity",
            "pool_not_initialized",
            "rpc_verification_failed",
            "rpc_verification_timeout",
        }
        and step.get("liveQuoteError")
    ]
    if not complete:
        action = "等待真实报价"
        level = "blocked"
        reasons = live_quote_failures or ["部分跳数缺少可计算池子或深度，不能进入自动交易"]
    elif requires_live_quoter:
        action = (
            f"{input_symbol} 本位参考收益为正，待链上复核"
            if untrusted_usd_reference and token_basis_profitable and input_symbol
            else f"{input_symbol} 本位收益未确认，缺少精确 decimals，不能按默认值假设"
            if untrusted_usd_reference and token_basis_reference_profitable and assumed_token_decimals and input_symbol
            else f"{input_symbol} 本位参考收益为负，待链上复核"
            if untrusted_usd_reference and token_basis_complete and input_symbol
            else "缺少可信 USD 锚点，待链上复核"
            if untrusted_usd_reference
            else "参考测算完成，待链上复核"
            if reference_final_output_usd is not None
            else "等待链上 Quoter 复核"
        )
        level = "requires_live_quote"
        reasons = (
            [
                "已基于本地 JSON 的 PoolKey、sqrtPriceX96、fee 和池内方向完成参考测算",
                "路径包含 V3/V4 或参考价报价跳，参考测算不能作为自动交易依据",
                "必须用链上 Quoter、协议 SDK 或 eth_call 复核每一跳输出后才能进入链上复核",
            ]
            if reference_final_output_usd is not None
            else ["路径包含 V3/V4 或参考价报价跳，静态余额不能作为自动交易依据", "必须用链上 Quoter 或协议 SDK 复核每一跳输出后才能进入链上复核"]
        )
        if strict_live_price_only:
            reasons = live_quote_failures or [
                "Robinhood Chain 强制链上实时价格，静态参考价已禁用",
                "路由框架可以保留，但任何价格、收益和 bps 都必须来自 live quoter / onchain evidence",
            ]
        if depth_status == "unavailable":
            action = "已生成保守初始量，继续探索深度"
            reasons = [
                "当前池子缺少可用于离线深度计算的 liquidity 或 TVL，收益只保留为参考值",
                "初始借贷量采用交易量阶梯中的最小探针，继续观察各跳输出和滑点",
                "补充深度快照后，按 10 bps 滑点限制重新放大量化",
            ]
        elif depth_status == "exceeds_10_bps":
            action = "滑点超过 10 bps，降低初始交易额度"
            reasons = [
                "离线深度估算发现至少一跳超过 10 bps 滑点限制",
                "应沿交易量阶梯降低初始借贷量，直到所有跳数满足 10 bps",
                *reasons,
            ]
        elif untrusted_usd_reference:
            reasons = [
                "当前路径缺少可信 USD 价格锚点，美元利润不可判定",
                "闭环主评估已统一改用初始借入 token X 作为计价基准，计算最终返回 X 的参考百分点",
                "X 本位测算仍来自 V4 PoolKey / sqrtPriceX96 静态参考，必须链上复核真实 amountOut",
                *reasons,
            ]
        if assumed_token_decimals:
            reasons = [
                "部分 V4 token decimals 未在本地 PoolKey 数据中明示，当前 X 本位数量不按 18 位默认值换算，必须先补齐精确 decimals",
                *reasons,
            ]
        if non_stable_chain_submission_candidate:
            reasons = [
                "非 U 资金池 X 不套用 6U 固定利润门槛；X 本位净收益为正即可进入链上利润控制候选",
                *reasons,
            ]
    elif token_basis_profitable and token_basis_complete and not borrow_asset_is_stable and reference_net_profit_usd is None:
        action = f"{input_symbol} 本位闭环已验证正收益，进入链上复核"
        level = "ready_for_precheck"
        reasons = [
            "链上 live quote 已验证闭环 token 本位正收益",
            "当前缺少可信 USD 锚点，但这不应阻断非稳定 X 本位候选",
            "仍需要链上复核确认链上可执行性与回款约束后再执行",
        ]
    elif net_profit_usd is not None and net_profit_usd >= min_profit_usd:
        action = "进入链上复核"
        level = "ready_for_precheck"
        reasons = ["1000U 静态探针净利润超过最低阈值", "仍需链上 Quoter、滑点、gas 与闪电贷费复核"]
    elif net_profit_usd is not None and net_profit_usd > 0:
        action = "继续观察"
        level = "watch"
        reasons = ["扣除闪电贷费和 gas 储备后仍为正，但低于自动交易阈值"]
    else:
        action = "不建议执行"
        level = "reject"
        reasons = ["1000U 静态探针未覆盖交易成本"]
    executable_candidate = bool(
        executable_ready
        and not assumed_token_decimals
        and net_profit_usd is not None
        and net_profit_usd >= min_profit_usd
    )
    testnet_live_only = bool(chain_id == ROBINHOOD_TESTNET_CHAIN_ID and requires_live_quoter and not live_price_verified)
    if testnet_live_only:
        executable_candidate = False

    # Ensure steps are ordered by hop number and match route_symbols order
    steps = sorted([s for s in steps if isinstance(s, dict)], key=lambda s: int(s.get("hop") or 0))
    truncated = any(str(warning).startswith("route_truncated_at_") for warning in warnings)

    # Validate route-step continuity
    if steps and route_symbols:
        # Rebuild steps in route order by symbol pair lookup to avoid reversed live hops
        step_map = {(str(s.get("fromSymbol") or "").upper(), str(s.get("toSymbol") or "").upper()): s for s in steps}
        ordered_steps = []
        for i, (frm, to) in enumerate(zip(route_symbols, route_symbols[1:]), start=1):
            key = (str(frm).upper(), str(to).upper())
            s = step_map.get(key)
            if s:
                s = dict(s)  # copy
            elif complete:
                # fallback to any step that matches hop number
                s = next((x for x in steps if int(x.get("hop") or 0) == i), {})
            else:
                # Keep truncated routes to the steps that were actually quoted or failed.
                continue
            s["hop"] = i
            s["fromSymbol"] = frm
            s["toSymbol"] = to
            ordered_steps.append(s)
        steps = ordered_steps
        # Fallback reference input amount from first step if not set
        if token_basis_input_amount is None and steps and steps[0].get("inputAmount") is not None:
            token_basis_input_amount = steps[0].get("inputAmount")
        # Validate after reorder
        if not truncated and steps and (steps[0].get("fromSymbol") != route_symbols[0] or steps[-1].get("toSymbol") != route_symbols[-1]):
            warnings.append("route_steps_mismatch")

    payload = {
        "method": "static_amm_depth_probe",
        "notionalUsd": notional_usd,
        "inputSymbol": input_symbol,
        "outputSymbol": output_symbol,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeFrameworkSource": "market_observation",
        "priceEvidenceSource": "chain_live_quote" if live_price_verified else ("chain_live_quote_required" if strict_live_price_only else "static_reference_probe"),
        "priceEvidenceMode": "live_verified" if live_price_verified else ("live_required" if strict_live_price_only else "reference_only"),
        "livePriceVerified": live_price_verified,
        "complete": complete,
        "requiresLiveQuoter": requires_live_quoter,
        "adapterRequired": adapter_required,
        "stepCount": len(steps),
        "steps": steps,
        "finalOutputUsd": final_output_usd,
        "referenceFinalOutputUsd": reference_final_output_usd,
        "grossProfitUsd": gross_profit_usd,
        "referenceGrossProfitUsd": reference_gross_profit_usd,
        "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
        "flashLoanFeeUsd": flash_loan_fee_usd,
        "gasReserveUsd": DEFAULT_GAS_RESERVE_USD,
        "minProfitUsd": min_profit_usd,
        "offchainMinProfitUsd": min_profit_usd,
        "submissionMinProfitUsd": submission_min_profit_usd,
        "submissionNetProfitUsd": submission_net_profit_usd,
        "offchainNetProfitUsd": net_profit_usd,
        "netProfitUsd": net_profit_usd,
        "referenceNetProfitUsd": reference_net_profit_usd,
        "netProfitBps": net_profit_bps,
        "referenceNetProfitBps": reference_net_profit_bps,
        "referenceInputAmount": token_basis_input_amount,
        "referenceInputSymbol": input_symbol if token_basis_input_amount is not None else None,
        "referenceFinalOutputAmount": reference_final_output_amount,
        "referenceFinalOutputSymbol": output_symbol if reference_final_output_amount is not None else None,
        "referenceGrossProfitAmount": reference_gross_profit_amount,
        "referenceFlashLoanFeeAmount": reference_flash_loan_fee_amount,
        "referenceNetProfitAmount": reference_net_profit_amount,
        "referenceGrossReturnBps": reference_gross_return_bps,
        "referenceNetReturnBps": reference_net_return_bps,
        "staticDepthStatus": depth_status,
        "staticDepthAvailable": static_depth_available,
        "maxStaticDepthImpactBps": max_static_depth_impact_bps,
        "staticDepthImpactLimitBps": 10.0,
        "referenceGasExcluded": token_basis_complete,
        "referenceReturnBasis": "borrow_token" if token_basis_complete else ("usd" if reference_net_profit_usd is not None else None),
        "referenceReturnTrusted": bool(
            reference_final_output_usd is not None
            and not assumed_token_decimals
            and (token_basis_trusted or reference_net_profit_usd is not None or live_price_verified)
        ),
        "borrowAssetClass": borrow_asset_class,
        "borrowAssetIsStable": borrow_asset_is_stable,
        "tokenBasisReferenceProfitable": token_basis_reference_profitable,
        "tokenBasisProfitable": token_basis_profitable,
        "chainSubmissionCandidate": bool(
            not testnet_live_only
            and not assumed_token_decimals
            and (
                non_stable_chain_submission_candidate
                or (executable_ready and net_profit_usd is not None and net_profit_usd >= min_profit_usd)
            )
        ),
        "chainSubmissionPolicy": (
            "non_stable_x_positive_token_profit_onchain_control"
            if non_stable_chain_submission_candidate
            else "stable_x_usd_min_profit"
            if borrow_asset_is_stable
            else "requires_positive_x_profit"
        ),
        "chainSubmissionMinProfitAmount": 0.0 if not borrow_asset_is_stable else None,
        "chainSubmissionMinProfitSymbol": input_symbol if not borrow_asset_is_stable else None,
        "profitable": bool(
            (not untrusted_usd_reference and not assumed_token_decimals and net_profit_usd is not None and net_profit_usd > 0)
            or token_basis_profitable
        ),
        "executableCandidate": executable_candidate,
        "sdkAdapterRequiredStepCount": sum(1 for step in steps if step.get("executionTrust") == "sdk_adapter_required"),
        "trustedUsdReference": bool(
            reference_final_output_usd is not None
            and not assumed_token_decimals
            and (not untrusted_usd_reference or live_price_verified)
        ),
        "tokenDecimalsTrusted": not assumed_token_decimals,
        "recommendation": {"level": level, "action": action, "reasons": reasons},
        "warnings": warnings,
    }
    return payload
