from __future__ import annotations

from typing import Any


ERROR = "error"
WARNING = "warning"

_REQUIRED_HOP_FIELDS = ("tokenIn", "tokenOut", "pool", "amountInRaw", "amountOutRaw", "minAmountOutRaw")
_PASSING_FORK_STATUSES = {
    "passed",
    "pass",
    "success",
    "fork_staticcall_passed",
    "fork_transaction_simulation_passed",
    "staticcall_passed",
}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return str(value or "").strip()


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _issue(severity: str, code: str, detail: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "detail": detail}


def _expected_hop_count(plan: dict[str, Any], route: dict[str, Any]) -> int:
    route_path = _as_list(route.get("path"))
    if len(route_path) >= 2:
        return len(route_path) - 1
    route_hop_count = _int_or_none(route.get("hopCount"))
    if route_hop_count is not None:
        return route_hop_count
    return 0


def _has_passing_fork_evidence(plan: dict[str, Any]) -> bool:
    fork_evidence = _as_dict(plan.get("forkEvidence") or plan.get("simulationEvidence"))
    status = _text(fork_evidence.get("status")).lower()
    return bool(status in _PASSING_FORK_STATUSES or fork_evidence.get("passed") is True)


def audit_robinhood_chain_test_plan(plan: dict[str, Any]) -> dict[str, Any]:
    route = _as_dict(plan.get("route"))
    decision = _as_dict(plan.get("decision"))
    preflight = _as_dict(plan.get("preflightSpec"))
    chain_scope = _as_dict(plan.get("chainTestScope"))
    repayment = _as_dict(preflight.get("repaymentConstraint"))
    hops = [hop for hop in _as_list(preflight.get("routeHops")) if isinstance(hop, dict)]
    expected_hops = _expected_hop_count(plan, route)
    issues: list[dict[str, str]] = []

    if chain_scope and chain_scope.get("enabled") is not True:
        if decision.get("canRunForkPreflight") or decision.get("canDeployTestnet") or decision.get("canBroadcast"):
            issues.append(
                _issue(
                    ERROR,
                    "non_robinhood_chain_test_scope_enabled",
                    "非 Robinhood 链测范围内不能开启 fork、测试网部署或广播。",
                )
            )
    if chain_scope and chain_scope.get("canBroadcast") is True:
        issues.append(_issue(ERROR, "chain_test_scope_broadcast_enabled", "Robinhood 链测范围内广播闸门必须保持关闭。"))

    if expected_hops <= 0:
        issues.append(_issue(ERROR, "route_hop_count_missing", "route.path 或 route.hopCount 缺失，无法确认跳数。"))
    elif len(hops) != expected_hops:
        issues.append(
            _issue(
                ERROR,
                "route_hop_count_mismatch",
                f"preflightSpec.routeHops={len(hops)}，但路径期望跳数={expected_hops}。",
            )
        )

    for index, hop in enumerate(hops, start=1):
        missing = [field for field in _REQUIRED_HOP_FIELDS if not _text(hop.get(field))]
        if missing:
            issues.append(
                _issue(ERROR, "hop_required_field_missing", f"第 {index} 跳缺少字段：{', '.join(missing)}。")
            )
        for field in ("amountInRaw", "amountOutRaw", "minAmountOutRaw"):
            parsed = _int_or_none(hop.get(field))
            if parsed is None or parsed <= 0:
                issues.append(_issue(ERROR, "hop_raw_amount_invalid", f"第 {index} 跳 {field} 不是正整数。"))
        if index > 1:
            previous = hops[index - 2]
            if _text(previous.get("tokenOut")).lower() != _text(hop.get("tokenIn")).lower():
                issues.append(_issue(ERROR, "hop_token_chain_broken", f"第 {index - 1} 跳输出 token 与第 {index} 跳输入 token 不一致。"))

    borrow_token = _text(repayment.get("borrowToken")).lower()
    final_token = _text(repayment.get("finalToken")).lower()
    if not borrow_token or not final_token:
        issues.append(_issue(ERROR, "repayment_token_missing", "repaymentConstraint 缺少 borrowToken 或 finalToken。"))
    elif borrow_token != final_token:
        issues.append(_issue(ERROR, "repayment_token_mismatch", "闪电贷还款 token 与最终输出 token 不一致。"))

    for field in ("borrowAmountRaw", "repayAmountRaw", "finalAmountRaw"):
        parsed = _int_or_none(repayment.get(field))
        if parsed is None or parsed <= 0:
            issues.append(_issue(ERROR, "repayment_raw_amount_invalid", f"repaymentConstraint.{field} 不是正整数。"))

    if decision.get("trustedForExecution") is True and not _has_passing_fork_evidence(plan):
        issues.append(_issue(ERROR, "trusted_without_fork_evidence", "缺少通过的 fork/static-call 证据，trustedForExecution 不能为 true。"))

    if decision.get("canBroadcast") is True and decision.get("trustedForExecution") is not True:
        issues.append(_issue(ERROR, "broadcast_without_execution_trust", "执行可信为 false 时 canBroadcast 不能为 true。"))

    if preflight.get("broadcastEnabled") is True and decision.get("trustedForExecution") is not True:
        issues.append(_issue(ERROR, "preflight_broadcast_enabled_without_trust", "执行可信为 false 时 preflightSpec.broadcastEnabled 不能为 true。"))

    if decision.get("readyForTransactionPreflight") is True and preflight.get("status") != "ready_for_fork_staticcall":
        issues.append(_issue(WARNING, "preflight_status_unexpected", "已标记预检就绪，但 preflightSpec.status 不是 ready_for_fork_staticcall。"))

    error_count = sum(1 for issue in issues if issue["severity"] == ERROR)
    return {
        "ok": error_count == 0,
        "errorCount": error_count,
        "warningCount": sum(1 for issue in issues if issue["severity"] == WARNING),
        "expectedHopCount": expected_hops,
        "actualHopCount": len(hops),
        "issues": issues,
    }
