from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.robinhood_chain_test_policy import robinhood_chain_test_scope


DEFAULT_ROBINHOOD_CHAIN_ID = 4663
DEFAULT_ROBINHOOD_NETWORK = "robinhood"
DEFAULT_ROBINHOOD_MARKET_ID = "robinhood-dex-shadow"


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = _text(value)
        if item and item not in result:
            result.append(item)
    return result


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_number(value: object) -> bool | None:
    parsed = _number(value)
    return None if parsed is None else parsed > 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _route_path(attempt: dict[str, Any]) -> list[str]:
    quote = _as_dict(attempt.get("quote"))
    precheck = _as_dict(attempt.get("precheck"))
    evidence = _as_dict(quote.get("live_onchain_evidence"))
    candidates = [
        attempt.get("route_path"),
        attempt.get("route_path_symbols"),
        attempt.get("path"),
        attempt.get("pathSymbols"),
        attempt.get("route_symbols"),
        attempt.get("routeSymbols"),
        quote.get("route_path"),
        quote.get("route_symbols"),
        evidence.get("routeSymbols"),
        precheck.get("route_symbols"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            route = [_text(item) for item in candidate if _text(item)]
            if route:
                return route
    pair = _text(attempt.get("pair") or quote.get("pair"))
    if pair:
        return [part.strip() for part in pair.split("->") if part.strip()]
    return []


def _hop_summaries(evidence: dict[str, Any], *, slippage_bps: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hop in _as_list(evidence.get("hops")):
        if not isinstance(hop, dict):
            continue
        amount_out_raw = _int_or_none(hop.get("amountOutRaw"))
        min_amount_out_raw = (
            amount_out_raw * max(0, 10_000 - int(slippage_bps)) // 10_000
            if amount_out_raw is not None
            else None
        )
        rows.append(
            {
                "hop": hop.get("hop"),
                "protocol": _text(hop.get("protocol")),
                "quoteMethod": _text(hop.get("quoteMethod")),
                "pool": _text(hop.get("pool")),
                "fromSymbol": _text(hop.get("fromSymbol")),
                "toSymbol": _text(hop.get("toSymbol")),
                "tokenIn": _text(hop.get("tokenIn")),
                "tokenOut": _text(hop.get("tokenOut")),
                "amountInRaw": _text(hop.get("amountInRaw")),
                "amountOutRaw": _text(hop.get("amountOutRaw")),
                "minAmountOutRaw": str(min_amount_out_raw) if min_amount_out_raw is not None else None,
                "amountIn": _text(hop.get("amountIn")),
                "amountOut": _text(hop.get("amountOut")),
                "inputDecimals": hop.get("inputDecimals"),
                "outputDecimals": hop.get("outputDecimals"),
                "fee": hop.get("fee") or hop.get("feeBps"),
                "quoterGasEstimate": hop.get("quoterGasEstimate"),
            }
        )
    return rows


def _repayment_constraint(evidence: dict[str, Any]) -> dict[str, Any]:
    hops = _as_list(evidence.get("hops"))
    first_hop = hops[0] if hops and isinstance(hops[0], dict) else {}
    last_hop = hops[-1] if hops and isinstance(hops[-1], dict) else {}
    principal_raw = _int_or_none(first_hop.get("amountInRaw"))
    flash_fee_bps = _number(_as_dict(evidence.get("costModel")).get("flashLoanFeeBps")) or 0.0
    repay_raw = None
    if principal_raw is not None:
        repay_raw = principal_raw + int(principal_raw * flash_fee_bps // 10_000)
    return {
        "borrowToken": _text(first_hop.get("tokenIn")),
        "borrowSymbol": _text(first_hop.get("fromSymbol")),
        "borrowAmountRaw": str(principal_raw) if principal_raw is not None else None,
        "flashLoanFeeBps": flash_fee_bps,
        "repayAmountRaw": str(repay_raw) if repay_raw is not None else None,
        "finalToken": _text(last_hop.get("tokenOut")),
        "finalSymbol": _text(last_hop.get("toSymbol")),
        "finalAmountRaw": _text(last_hop.get("amountOutRaw")),
    }


def _token_continuity_issues(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index in range(1, len(hops)):
        previous = hops[index - 1]
        current = hops[index]
        previous_token_out = _text(previous.get("tokenOut"))
        current_token_in = _text(current.get("tokenIn"))
        if previous_token_out.lower() != current_token_in.lower():
            issues.append(
                {
                    "fromHop": previous.get("hop") or index,
                    "toHop": current.get("hop") or index + 1,
                    "fromSymbol": previous.get("toSymbol"),
                    "toSymbol": current.get("fromSymbol"),
                    "previousTokenOut": previous_token_out,
                    "currentTokenIn": current_token_in,
                }
            )
    return issues


def _attempt_evidence(attempt: dict[str, Any]) -> dict[str, Any]:
    quote = _as_dict(attempt.get("quote"))
    review_summary = _as_dict(attempt.get("review_summary"))
    precheck = _as_dict(attempt.get("precheck"))
    return (
        _as_dict(quote.get("live_onchain_evidence"))
        or _as_dict(review_summary.get("onchain_evidence"))
        or _as_dict(precheck.get("onchain_evidence"))
    )


def _attempt_status(attempt: dict[str, Any]) -> str:
    for key in ("state", "priority_reason", "execution_phase"):
        text = _text(attempt.get(key))
        if text:
            return text
    evidence = _attempt_evidence(attempt)
    return _text(evidence.get("status")) or "unknown"


def _checklist_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = plan["evidence"]
    decision = plan["decision"]
    route = plan["route"]
    contract_dir = plan["contractHandoff"]["contractDir"]
    route_closed = bool(evidence.get("routeClosed"))
    ready_for_preflight = bool(decision.get("readyForTransactionPreflight"))
    trust_gap = not bool(decision.get("trustedForExecution"))
    return [
        {
            "id": "freeze_snapshot",
            "title": "冻结链上复核快照",
            "status": "done",
            "done": True,
            "detail": f"锁定 {route['pathText']} / 触发区块 {evidence.get('triggerBlockNumber')}",
        },
        {
            "id": "build_contract_dir",
            "title": "准备链别合约信息目录",
            "status": "done" if contract_dir else "pending",
            "done": bool(contract_dir),
            "detail": "在 contract/robinhood 下保留链别信息与后续部署占位",
        },
        {
            "id": "calldata_builder",
            "title": "生成交易 calldata",
            "status": "pending",
            "done": False,
            "detail": "按链上复核路径生成 flashloan / AMM 交换参数",
        },
        {
            "id": "risk_guard",
            "title": "补齐滑点与回款约束",
            "status": "pending" if route_closed else "blocked",
            "done": False,
            "detail": "加入 minOut、滑点保护、回款余额与最小利润阈值",
        },
        {
            "id": "fork_staticcall",
            "title": "执行 fork/static-call 仿真",
            "status": "pending" if ready_for_preflight else "blocked",
            "done": False,
            "detail": "只读 Quoter 已复核后仍需本地仿真",
        },
        {
            "id": "broadcast_gate",
            "title": "保持广播闸门关闭",
            "status": "blocked" if trust_gap else "pending",
            "done": False,
            "detail": "trustedForExecution 仍为否，不能进入签名发送",
        },
        {
            "id": "archive_outputs",
            "title": "归档实施文档与测试包",
            "status": "pending",
            "done": False,
            "detail": "完成后把方案、清单、测试包归档到 back",
        },
    ]


def build_robinhood_chain_test_plan(attempt: dict[str, Any], *, contract_dir: Path | None = None) -> dict[str, Any]:
    evidence = _attempt_evidence(attempt)
    quote = _as_dict(attempt.get("quote"))
    precheck = _as_dict(attempt.get("precheck"))
    route_path = _route_path(attempt)
    blockers = _unique(
        [
            *(str(item) for item in evidence.get("blockers") or []),
            *(str(item) for item in precheck.get("reasons") or []),
            *(str(item) for item in attempt.get("blocked_reasons") or []),
        ]
    )
    route_closed = bool(route_path and route_path[0] == route_path[-1])
    requested_ready_for_preflight = bool(
        evidence.get("readyForTransactionPreflight")
        or evidence.get("ready_for_transaction_preflight")
        or precheck.get("ready_for_transaction_preflight")
    )
    trusted_for_execution = bool(
        evidence.get("trustedForExecution")
        or evidence.get("trusted_for_execution")
        or precheck.get("trustedForExecution")
    )
    slippage_bps = 50
    hop_summaries = _hop_summaries(evidence, slippage_bps=slippage_bps)
    token_continuity_issues = _token_continuity_issues(hop_summaries)
    token_continuity_ok = not token_continuity_issues
    validation = _as_dict(evidence.get("validation"))
    cost_model = _as_dict(evidence.get("costModel"))
    if token_continuity_issues:
        blockers = _unique(
            [
                *blockers,
                "链上复核跳间 token 地址不连续：存在同名不同合约地址，不能进入交易 calldata 或 fork/static-call 预检。",
            ]
        )
    all_hops_quoted = bool(validation.get("allHopsQuoted")) and token_continuity_ok
    inferred_net_positive = _positive_number(cost_model.get("netProfitUsd"))
    net_positive_after_costs = bool(
        validation.get("netPositiveAfterCosts")
        if validation.get("netPositiveAfterCosts") is not None
        else (inferred_net_positive if inferred_net_positive is not None else validation.get("profitableAfterCosts"))
    )
    net_profit = _number(cost_model.get("netProfitUsd"))
    min_profit = _number(cost_model.get("minProfitUsd"))
    inferred_meets_threshold = net_profit is not None and min_profit is not None and net_profit >= min_profit
    meets_min_profit_threshold = bool(
        validation.get("meetsMinProfitThreshold")
        if validation.get("meetsMinProfitThreshold") is not None
        else (inferred_meets_threshold if min_profit is not None else requested_ready_for_preflight)
    )
    ready_for_preflight = (
        requested_ready_for_preflight
        and meets_min_profit_threshold
        and route_closed
        and token_continuity_ok
        and all_hops_quoted
    )
    chain_scope = robinhood_chain_test_scope(
        evidence.get("network") or attempt.get("network") or DEFAULT_ROBINHOOD_NETWORK,
        evidence.get("chainId") or attempt.get("chain_id") or DEFAULT_ROBINHOOD_CHAIN_ID,
    )
    if token_continuity_issues:
        preflight_status = "blocked_token_discontinuity"
    elif ready_for_preflight and not trusted_for_execution:
        preflight_status = "ready_for_fork_staticcall"
    else:
        preflight_status = "not_ready"
    plan = {
        "generatedAt": _utc_now_iso(),
        "network": _text(evidence.get("network") or attempt.get("network") or DEFAULT_ROBINHOOD_NETWORK).lower(),
        "chainId": int(evidence.get("chainId") or attempt.get("chain_id") or DEFAULT_ROBINHOOD_CHAIN_ID),
        "marketId": _text(attempt.get("market_id") or DEFAULT_ROBINHOOD_MARKET_ID),
        "chainTestScope": chain_scope,
        "attempt": {
            "state": _attempt_status(attempt),
            "pair": _text(attempt.get("pair") or quote.get("pair")),
            "pathText": " -> ".join(route_path) if route_path else _text(attempt.get("pair")),
            "routePath": route_path,
            "observedAt": _text(attempt.get("observed_at") or attempt.get("observedAt") or evidence.get("observedAt")),
            "createdAt": _text(attempt.get("created_at") or attempt.get("createdAt")),
            "triggerBlockNumber": evidence.get("triggerBlockNumber") or attempt.get("trigger_block_number"),
            "blockNumber": evidence.get("blockNumber"),
            "reviewBlockNumber": evidence.get("blockNumber"),
            "status": _text(evidence.get("status") or precheck.get("status") or attempt.get("state")),
        },
        "evidence": {
            "status": _text(evidence.get("status")),
            "executionTrust": _text(evidence.get("executionTrust")),
            "triggerBlockNumber": evidence.get("triggerBlockNumber")
            or _as_dict(evidence.get("triggerContext")).get("triggerBlockNumber")
            or _as_dict(evidence.get("triggerContext")).get("blockNumber")
            or attempt.get("trigger_block_number"),
            "blockNumber": evidence.get("blockNumber"),
            "trustedForExecution": trusted_for_execution,
            "readyForTransactionPreflight": ready_for_preflight,
            "readyForTransactionPreflightRaw": requested_ready_for_preflight,
            "routeClosed": route_closed,
            "allHopsQuoted": all_hops_quoted,
            "tokenContinuity": token_continuity_ok,
            "tokenContinuityIssues": token_continuity_issues,
            "profitableAfterCosts": net_positive_after_costs,
            "netPositiveAfterCosts": net_positive_after_costs,
            "meetsMinProfitThreshold": meets_min_profit_threshold,
            "finalOutputSymbol": _text(validation.get("finalOutputSymbol")),
            "finalOutputUsd": cost_model.get("finalOutputUsd"),
            "grossProfitUsd": cost_model.get("grossProfitUsd"),
            "netProfitUsd": cost_model.get("netProfitUsd"),
            "netProfitBps": cost_model.get("netProfitBps"),
            "minProfitUsd": cost_model.get("minProfitUsd"),
            "liveProfitPercent": precheck.get("live_profit_percent"),
            "referenceNetProfitUsd": precheck.get("reference_profit_amount"),
            "blockers": blockers,
        },
        "route": {
            "path": route_path,
            "pathText": " -> ".join(route_path),
            "hopCount": len(_as_list(evidence.get("hops"))) or max(0, len(route_path) - 1),
            "closed": route_closed,
            "tokenContinuous": token_continuity_ok,
            "analysisStage": _text(
                _as_dict(attempt.get("route")).get("analysisStage")
                or _as_dict(quote.get("route")).get("analysisStage")
            ),
        },
        "decision": {
            "canBroadcast": False,
            "canSubmitOrder": bool(attempt.get("can_submit_order")),
            "canRunForkPreflight": bool(chain_scope.get("canRunForkStaticcall")) and ready_for_preflight and not trusted_for_execution,
            "canDeployTestnet": bool(chain_scope.get("canDeployTestnet")),
            "trustedForExecution": trusted_for_execution,
            "readyForTransactionPreflight": ready_for_preflight,
            "requiresTransactionDevelopment": not trusted_for_execution,
            "needsLiveQuoter": bool(precheck.get("requires_live_quoter")),
            "blockedReasons": blockers,
        },
        "preflightSpec": {
            "status": preflight_status,
            "slippageBps": slippage_bps,
            "routeHops": hop_summaries,
            "repaymentConstraint": _repayment_constraint(evidence),
            "requiredChecks": [
                "calldata_matches_verified_hops",
                "each_hop_min_amount_out_enforced",
                "flashloan_repay_amount_enforced",
                "final_token_balance_covers_repay_and_min_profit",
                "fork_staticcall_or_fork_transaction_simulation_passed",
                "broadcast_gate_remains_disabled_until_trusted_for_execution",
            ],
            "broadcastEnabled": False,
        },
    }
    plan["checklist"] = _checklist_items(
        {
            "evidence": plan["evidence"],
            "decision": plan["decision"],
            "route": plan["route"],
            "contractHandoff": {"contractDir": str(contract_dir) if contract_dir else ""},
        }
    )
    plan["contractHandoff"] = {
        "contractDir": str(contract_dir) if contract_dir else "",
        "expectedFiles": ["README.md", "contracts.json"] if contract_dir else [],
    }
    return plan


def render_robinhood_chain_test_summary(plan: dict[str, Any]) -> str:
    attempt = _as_dict(plan.get("attempt"))
    evidence = _as_dict(plan.get("evidence"))
    route = _as_dict(plan.get("route"))
    decision = _as_dict(plan.get("decision"))
    if decision.get("readyForTransactionPreflight"):
        conclusion = "当前记录适合进入 fork/static-call 预检，但不适合直接广播提交。"
        next_steps = [
            "1. 固定 route / block snapshot。",
            "2. 生成 calldata 与 minOut。",
            "3. 在 fork 上跑 static-call / 交易仿真。",
            "4. 通过后再考虑签名发送。",
        ]
    else:
        conclusion = "当前记录已被预检前置校验阻断，不能进入 calldata 或 fork/static-call。"
        next_steps = [
            "1. 回到候选池构建层，按 token 地址重建真实闭环。",
            "2. 重新执行链上 Quoter 复核。",
            "3. 审计通过后再生成 calldata 与 fork/static-call 任务。",
            "4. 广播闸门继续保持关闭。",
        ]
    lines = [
        "# Robinhood 链上次测试包",
        "",
        f"- 网络：`{plan.get('network')}`",
        f"- 链 ID：`{plan.get('chainId')}`",
        f"- 路径：`{route.get('pathText')}`",
        f"- 触发区块：`{attempt.get('triggerBlockNumber')}`",
        f"- 复核区块：`{attempt.get('reviewBlockNumber')}`",
        f"- 证据状态：`{evidence.get('status')}`",
        f"- 执行可信：`{'是' if decision.get('trustedForExecution') else '否'}`",
        f"- 预检就绪：`{'是' if decision.get('readyForTransactionPreflight') else '否'}`",
        "",
        "## 结论",
        "",
        conclusion,
        "",
        "## 关键阻断",
    ]
    blockers = evidence.get("blockers") or decision.get("blockedReasons") or []
    if blockers:
        lines.extend([f"- {item}" for item in blockers])
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            *next_steps,
        ]
    )
    return "\n".join(lines) + "\n"


def render_robinhood_chain_test_checklist(plan: dict[str, Any]) -> str:
    lines = ["# Robinhood 链上次测试 checklist", ""]
    for item in plan.get("checklist") or []:
        if not isinstance(item, dict):
            continue
        mark = "x" if item.get("done") else " "
        lines.append(f"- [{mark}] {item.get('title')}：{item.get('detail')}")
    return "\n".join(lines) + "\n"


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_robinhood_chain_test_package(
    output_dir: Path,
    attempt: dict[str, Any],
    *,
    contract_dir: Path | None = None,
) -> dict[str, Any]:
    plan = build_robinhood_chain_test_plan(attempt, contract_dir=contract_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _json_dump(output_dir / "attempt.json", attempt)
    _json_dump(output_dir / "plan.json", plan)
    _json_dump(output_dir / "checklist.json", plan.get("checklist") or [])
    _json_dump(
        output_dir / "manifest.json",
        {
            "schemaVersion": "robinhood-chain-test-package-v1",
            "generatedAt": plan["generatedAt"],
            "network": plan["network"],
            "chainId": plan["chainId"],
            "files": ["attempt.json", "plan.json", "checklist.json", "summary.md"],
            "contractDir": str(contract_dir) if contract_dir else None,
        },
    )
    (output_dir / "summary.md").write_text(render_robinhood_chain_test_summary(plan), encoding="utf-8")
    if contract_dir is not None:
        contract_dir.mkdir(parents=True, exist_ok=True)
        _json_dump(
            contract_dir / "contracts.json",
            {
                "network": plan["network"],
                "chainId": plan["chainId"],
                "marketId": plan["marketId"],
                "wrappedNative": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                "multicall3": "0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1",
                "uniswapV2Pool": "0x8803c117ccae7B5146297876c2A25DF135141C4d",
                "v3Quoter": "0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7",
                "routePreflightStatus": plan["preflightSpec"]["status"],
                "deploymentGate": "blocked_until_token_continuity_calldata_repayment_and_fork_staticcall_pass",
                "note": "read-only evidence package; no broadcast-ready executor has been deployed for this chain test",
            },
        )
        (contract_dir / "README.md").write_text(
            "\n".join(
                [
                    "# Robinhood Contract Notes",
                    "",
                    "- Chain: `robinhood`",
                    f"- Chain ID: `{plan['chainId']}`",
                    f"- Market: `{plan['marketId']}`",
                    "- Current state: read-only evidence and preflight preparation only",
                    f"- Current route preflight status: `{plan['preflightSpec']['status']}`",
                    "- Deployment gate: blocked until token continuity, calldata, repayment, and fork/static-call pass",
                    "- Deployment: no dedicated broadcast executor is added by this package",
                    "- Route test scope: calldata, slippage, repayment, and fork/static-call verification",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return plan
