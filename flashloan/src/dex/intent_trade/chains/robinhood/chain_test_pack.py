from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from market.binance_market.dex_surfaces import (
    DEFAULT_FLASH_LOAN_FEE_BPS,
    DEFAULT_GAS_RESERVE_USD,
)
from core.market_identity import canonical_market_id_from_legacy
from dex.intent_trade.chains.robinhood.chain_test_policy import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    robinhood_chain_test_scope,
)
from dex.intent_trade.chains.robinhood.config import (
    MULTICALL_ADDRESS,
    PERMIT2_ADDRESS,
    TESTNET_PERMIT2_ADDRESS,
    TESTNET_V4_POOL_MANAGER_ADDRESS,
    TESTNET_V4_QUOTER_ADDRESS,
    TESTNET_V4_UNIVERSAL_ROUTER_ADDRESS,
    UNISWAP_V2_POOL_ADDRESS,
    V3_QUOTER_ADDRESS,
    V4_POOL_MANAGER_ADDRESS,
    V4_QUOTER_ADDRESS,
    V4_UNIVERSAL_ROUTER_ADDRESS,
    WETH_ADDRESS,
)
from dex.intent_trade.chains.robinhood.chain_test_helpers import (
    ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT,
    _as_dict,
    _as_list,
    _attempt_evidence,
    _attempt_status,
    _canonical_market_id,
    _checklist_items,
    _decimal,
    _execution_model,
    _execution_request,
    _hop_decimals,
    _hop_summaries,
    _human_from_raw,
    _int_or_none,
    _is_zero_address,
    _latest_snapshot_route,
    _latest_snapshot_route_size_advice,
    _normalized_hop_rows,
    _number,
    _positive_number,
    _profit_basis,
    _quote_pool_index,
    _raw_from_human,
    _read_json,
    _repayment_constraint,
    _route_path,
    _route_path_text,
    _symbol_decimals,
    _text,
    _token_continuity_issues,
    _unique,
    _utc_now_iso,
)
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD


DEFAULT_ROBINHOOD_CHAIN_ID = ROBINHOOD_CHAIN_ID
DEFAULT_ROBINHOOD_NETWORK = ROBINHOOD_NETWORK
LEGACY_ROBINHOOD_MARKET_ID = "robinhood-dex-shadow"
DEFAULT_ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v3"
LEGACY_ROBINHOOD_TESTNET_MARKET_ID = "robinhood-testnet-dex-paused"
DEFAULT_ROBINHOOD_TESTNET_MARKET_ID = "dex-arbitrage-46630-uniswap-v4"

def build_robinhood_chain_test_plan(attempt: dict[str, Any], *, contract_dir: Path | None = None) -> dict[str, Any]:
    evidence = _attempt_evidence(attempt)
    quote = _as_dict(attempt.get("quote"))
    precheck = _as_dict(attempt.get("precheck"))
    network = _text(evidence.get("network") or attempt.get("network") or DEFAULT_ROBINHOOD_NETWORK).lower()
    chain_id = int(evidence.get("chainId") or attempt.get("chain_id") or DEFAULT_ROBINHOOD_CHAIN_ID)
    default_market_id = (
        DEFAULT_ROBINHOOD_TESTNET_MARKET_ID
        if network == ROBINHOOD_TESTNET_NETWORK or chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else DEFAULT_ROBINHOOD_MARKET_ID
    )
    default_legacy_market_id = (
        LEGACY_ROBINHOOD_TESTNET_MARKET_ID
        if network == ROBINHOOD_TESTNET_NETWORK or chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else LEGACY_ROBINHOOD_MARKET_ID
    )
    requested_market_id = _text(attempt.get("market_id") or default_market_id)
    canonical_plan_market_id = _canonical_market_id(requested_market_id)
    legacy_plan_market_id = (
        requested_market_id
        if requested_market_id and requested_market_id != canonical_plan_market_id
        else default_legacy_market_id
    )
    route_path = _route_path(attempt)
    blockers = _unique(
        [
            *(str(item) for item in evidence.get("blockers") or []),
            *(str(item) for item in precheck.get("reasons") or []),
            *(str(item) for item in attempt.get("blocked_reasons") or []),
        ]
    )
    route_closed = bool(route_path and route_path[0] == route_path[-1])
    latest_snapshot_route, latest_snapshot = _latest_snapshot_route(
        route_path,
        chain_id=chain_id,
        network=network,
    )
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
    normalized_hops = _normalized_hop_rows(quote=quote, evidence=evidence, network=network)
    hop_summaries = _hop_summaries(normalized_hops, slippage_bps=slippage_bps)
    token_continuity_issues = _token_continuity_issues(hop_summaries)
    token_continuity_ok = not token_continuity_issues
    validation = _as_dict(evidence.get("validation"))
    cost_model = dict(_as_dict(evidence.get("costModel")))
    requested_borrow_amount_raw = _int_or_none(evidence.get("requestedBorrowInputAmountRaw"))
    selected_borrow_amount_raw = _int_or_none(evidence.get("selectedBorrowInputAmountRaw"))
    original_requested_borrow_amount_raw = requested_borrow_amount_raw
    repayment_borrow_amount_raw = None
    latest_snapshot_route_size_advice = {
        "latestSnapshotUpdatedAt": _text(latest_snapshot.get("updated_at")),
        "routeMaxExecutableAmountRaw": None,
        "routeExecutionSizeProbe": None,
        "routeExecutionSizeProbeStatus": "",
        "routeExecutableCandidate": None,
        "routeChainSubmissionCandidate": None,
        "routeRecommendedBorrowAmountRaw": None,
        "routeRequestedBorrowAmountRaw": None,
        "routeExecutionSizeProbeExceeded": False,
        "routeExecutionSizeShortfallRaw": None,
    }
    latest_snapshot_route_max_executable_raw = None
    latest_snapshot_route_recommended_raw = None
    latest_snapshot_route_execution_probe = {}
    latest_snapshot_route_updated_at = _text(latest_snapshot.get("updated_at"))
    if hop_summaries:
        latest_snapshot_route_size_advice = _latest_snapshot_route_size_advice(
            latest_snapshot_route,
            latest_snapshot,
            requested_borrow_amount_raw=requested_borrow_amount_raw,
        )
        latest_snapshot_route_max_executable_raw = _int_or_none(
            latest_snapshot_route_size_advice.get("routeMaxExecutableAmountRaw")
        )
        latest_snapshot_route_recommended_raw = _int_or_none(
            latest_snapshot_route_size_advice.get("routeRecommendedBorrowAmountRaw")
        )
        latest_snapshot_route_execution_probe = _as_dict(
            latest_snapshot_route_size_advice.get("routeExecutionSizeProbe")
        )
        latest_snapshot_route_updated_at = _text(latest_snapshot_route_size_advice.get("latestSnapshotUpdatedAt"))
        if selected_borrow_amount_raw is None and latest_snapshot_route_recommended_raw is not None:
            selected_borrow_amount_raw = latest_snapshot_route_recommended_raw
        if selected_borrow_amount_raw is None:
            selected_borrow_amount_raw = latest_snapshot_route_max_executable_raw
        if selected_borrow_amount_raw is None:
            selected_borrow_amount_raw = _int_or_none(hop_summaries[0].get("amountInRaw"))
        if selected_borrow_amount_raw is None:
            selected_borrow_amount_raw = requested_borrow_amount_raw
        repayment_borrow_amount_raw = selected_borrow_amount_raw
        if (
            repayment_borrow_amount_raw is not None
            and latest_snapshot_route_max_executable_raw is not None
            and repayment_borrow_amount_raw <= latest_snapshot_route_max_executable_raw
            and latest_snapshot_route_size_advice.get("routeExecutableCandidate") is False
        ):
            latest_snapshot_route_size_advice["routeExecutableCandidate"] = True
        if not cost_model:
            notional = _number(
                quote.get("input_amount")
                or quote.get("inputAmount")
                or _as_dict(quote.get("analysis")).get("notionalUsd")
                or hop_summaries[0].get("inputUsd")
            )
            if notional is None:
                notional = 1000.0
            final_hop = hop_summaries[-1]
            final_output_usd = _number(
                final_hop.get("referenceOutputUsd")
                or final_hop.get("outputUsd")
                or final_hop.get("amountOut")
            )
            flash_fee_bps = float(DEFAULT_FLASH_LOAN_FEE_BPS)
            flash_fee_usd = notional * flash_fee_bps / 10_000
            gas_reserve_usd = float(DEFAULT_GAS_RESERVE_USD)
            gross_profit_usd = final_output_usd - notional if final_output_usd is not None else None
            net_profit_usd = gross_profit_usd - flash_fee_usd - gas_reserve_usd if gross_profit_usd is not None else None
            min_profit_usd = max(float(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD), 0.0)
            cost_model = {
                "notionalUsd": notional,
                "finalOutputUsd": final_output_usd,
                "grossProfitUsd": gross_profit_usd,
                "flashLoanFeeBps": flash_fee_bps,
                "flashLoanFeeUsd": flash_fee_usd,
                "gasReserveUsd": gas_reserve_usd,
                "offchainMinProfitUsd": min_profit_usd,
                "minProfitUsd": min_profit_usd,
                "submissionMinProfitUsd": flash_fee_usd,
                "submissionNetProfitUsd": gross_profit_usd - flash_fee_usd if gross_profit_usd is not None else None,
                "offchainNetProfitUsd": net_profit_usd,
                "netProfitUsd": net_profit_usd,
                "netProfitBps": (net_profit_usd / notional * 10_000) if net_profit_usd is not None and notional else None,
            }
        else:
            cost_model.setdefault("notionalUsd", _number(quote.get("input_amount") or quote.get("inputAmount")))
            if cost_model.get("finalOutputUsd") is None:
                cost_model["finalOutputUsd"] = _number(
                    hop_summaries[-1].get("referenceOutputUsd")
                    or hop_summaries[-1].get("outputUsd")
                    or hop_summaries[-1].get("amountOut")
                )
    if token_continuity_issues:
        blockers = _unique(
            [
                *blockers,
                "On-chain reviewed route has token discontinuity; calldata and fork/static-call preflight are blocked.",
            ]
        )
    route_size_probe_blocked = False
    if latest_snapshot_route and latest_snapshot_route_max_executable_raw is not None:
        if repayment_borrow_amount_raw is not None and repayment_borrow_amount_raw > latest_snapshot_route_max_executable_raw:
            route_size_probe_blocked = True
            blockers = _unique(
                [
                    *blockers,
                    "latest_snapshot_route_execution_size_probe_exceeded",
                ]
            )
    if latest_snapshot_route and latest_snapshot_route_size_advice.get("routeExecutableCandidate") is False:
        route_size_probe_blocked = True
        blockers = _unique(
            [
                *blockers,
                "latest_snapshot_route_not_executable_candidate",
            ]
        )
    evidence_has_hops = bool(_as_list(evidence.get("hops")))
    all_hops_quoted = bool(validation.get("allHopsQuoted") if evidence_has_hops else hop_summaries) and token_continuity_ok
    profit_basis = _profit_basis(cost_model, route_path[0] if route_path else "")
    inferred_net_positive = _positive_number(
        cost_model.get("netProfitAmount") if profit_basis == "borrow_token" else cost_model.get("netProfitUsd")
    )
    net_positive_after_costs = bool(
        validation.get("netPositiveAfterCosts")
        if validation.get("netPositiveAfterCosts") is not None
        else (inferred_net_positive if inferred_net_positive is not None else validation.get("profitableAfterCosts"))
    )
    net_profit = _number(cost_model.get("netProfitUsd"))
    token_net_profit = _number(cost_model.get("netProfitAmount"))
    submission_net_profit = _number(cost_model.get("submissionNetProfitUsd"))
    token_submission_net_profit = _number(cost_model.get("submissionNetProfitAmount"))
    submission_min_profit = _number(cost_model.get("submissionMinProfitUsd"))
    offchain_min_profit = _number(cost_model.get("offchainMinProfitUsd"))
    inferred_meets_threshold = (
        token_submission_net_profit is not None and token_submission_net_profit > 0
        if profit_basis == "borrow_token"
        else submission_net_profit is not None and submission_net_profit > 0
    )
    validation_meets_submission = validation.get("meetsSubmissionProfitThreshold")
    if validation_meets_submission is None:
        validation_meets_submission = validation.get("meetsMinProfitThreshold")
    meets_min_profit_threshold = bool(
        validation_meets_submission
        if validation_meets_submission is not None
        else (inferred_meets_threshold if submission_min_profit is not None else requested_ready_for_preflight)
    )
    meets_offchain_profit_threshold = bool(
        validation.get("meetsOffchainProfitThreshold")
        if validation.get("meetsOffchainProfitThreshold") is not None
        else (net_profit is not None and offchain_min_profit is not None and net_profit >= offchain_min_profit)
    )
    ready_for_preflight = (
        requested_ready_for_preflight
        and meets_min_profit_threshold
        and route_closed
        and token_continuity_ok
        and all_hops_quoted
        and not route_size_probe_blocked
    )
    chain_scope = robinhood_chain_test_scope(
        network,
        chain_id,
    )
    if token_continuity_issues:
        preflight_status = "blocked_token_discontinuity"
    elif route_size_probe_blocked:
        preflight_status = "blocked_route_execution_size_probe"
    elif ready_for_preflight and not trusted_for_execution:
        preflight_status = "ready_for_fork_staticcall"
    else:
        preflight_status = "not_ready"
    repayment_constraint = _repayment_constraint(
        hop_summaries,
        flash_fee_bps=float(cost_model.get("flashLoanFeeBps") or DEFAULT_FLASH_LOAN_FEE_BPS),
        cost_model=cost_model,
    )
    execution_model = _execution_model(hop_summaries, repayment_constraint)
    execution_request = _execution_request(hop_summaries, repayment_constraint, execution_model=execution_model)
    plan = {
        "generatedAt": _utc_now_iso(),
        "network": network,
        "chainId": chain_id,
        "marketId": canonical_plan_market_id,
        "legacyMarketId": legacy_plan_market_id or None,
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
            "meetsSubmissionProfitThreshold": meets_min_profit_threshold,
            "meetsOffchainProfitThreshold": meets_offchain_profit_threshold,
            "finalOutputSymbol": _text(validation.get("finalOutputSymbol") or (hop_summaries[-1].get("toSymbol") if hop_summaries else "")),
            "finalOutputUsd": cost_model.get("finalOutputUsd"),
            "grossProfitUsd": cost_model.get("grossProfitUsd"),
            "netProfitUsd": cost_model.get("netProfitUsd"),
            "netProfitBps": cost_model.get("netProfitBps"),
            "minProfitUsd": cost_model.get("minProfitUsd"),
            "latestSnapshotUpdatedAt": latest_snapshot_route_updated_at or _text(latest_snapshot.get("updated_at")),
            "routeOriginalRequestedBorrowAmountRaw": original_requested_borrow_amount_raw,
            "routeRecommendedBorrowAmountRaw": latest_snapshot_route_recommended_raw,
            "routeRequestedBorrowAmountRaw": repayment_borrow_amount_raw or requested_borrow_amount_raw,
            "routeSelectedBorrowAmountRaw": selected_borrow_amount_raw or repayment_borrow_amount_raw,
            "routeMaxExecutableAmountRaw": latest_snapshot_route_max_executable_raw,
            "routeExecutionSizeProbeStatus": latest_snapshot_route_size_advice.get("routeExecutionSizeProbeStatus"),
            "routeExecutableCandidate": latest_snapshot_route_size_advice.get("routeExecutableCandidate"),
            "routeChainSubmissionCandidate": latest_snapshot_route_size_advice.get("routeChainSubmissionCandidate"),
            "routeExecutionSizeProbe": latest_snapshot_route_execution_probe or None,
            "routeExecutionSizeProbeExceeded": bool(
                repayment_borrow_amount_raw is not None
                and latest_snapshot_route_max_executable_raw is not None
                and repayment_borrow_amount_raw > latest_snapshot_route_max_executable_raw
            ),
            "routeExecutionSizeShortfallRaw": (
                repayment_borrow_amount_raw - latest_snapshot_route_max_executable_raw
                if repayment_borrow_amount_raw is not None
                and latest_snapshot_route_max_executable_raw is not None
                and repayment_borrow_amount_raw > latest_snapshot_route_max_executable_raw
                else None
            ),
            "routeOriginalExecutionSizeProbeExceeded": latest_snapshot_route_size_advice.get("routeExecutionSizeProbeExceeded"),
            "routeOriginalExecutionSizeShortfallRaw": latest_snapshot_route_size_advice.get("routeExecutionSizeShortfallRaw"),
            "profitBasis": profit_basis,
            "borrowInputAmount": cost_model.get("borrowInputAmount"),
            "borrowInputSymbol": cost_model.get("borrowInputSymbol"),
            "finalOutputAmount": cost_model.get("finalOutputAmount"),
            "grossProfitAmount": cost_model.get("grossProfitAmount"),
            "submissionNetProfitAmount": cost_model.get("submissionNetProfitAmount"),
            "netProfitAmount": token_net_profit,
            "netProfitAmountSymbol": cost_model.get("netProfitAmountSymbol") or (route_path[0] if route_path else ""),
            "netProfitAmountBps": cost_model.get("netProfitAmountBps"),
            "liveProfitPercent": precheck.get("live_profit_percent"),
            "referenceNetProfitUsd": precheck.get("reference_profit_amount"),
            "blockers": blockers,
        },
        "route": {
            "path": route_path,
            "pathText": " -> ".join(route_path),
            "hopCount": len(hop_summaries) or max(0, len(route_path) - 1),
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
            "repaymentConstraint": repayment_constraint,
            "executionModel": execution_model,
            "executionRequest": execution_request,
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
        "executionModel": execution_model,
    }
    return plan


def render_robinhood_chain_test_summary(plan: dict[str, Any]) -> str:
    attempt = _as_dict(plan.get("attempt"))
    evidence = _as_dict(plan.get("evidence"))
    route = _as_dict(plan.get("route"))
    decision = _as_dict(plan.get("decision"))
    if decision.get("readyForTransactionPreflight"):
        conclusion = "Current evidence can enter fork/static-call preflight, but it is not broadcast-ready."
        next_steps = [
            "1. Pin the route and block snapshot.",
            "2. Generate calldata and minOut constraints.",
            "3. Run static-call or transaction simulation on a fork.",
            "4. Consider signing and broadcast only after preflight passes.",
        ]
    else:
        conclusion = "Current evidence is blocked by preflight prerequisites and cannot enter calldata or fork/static-call."
        next_steps = [
            "1. Rebuild the real closed route from candidate pools and token addresses.",
            "2. Re-run read-only on-chain quoter validation.",
            "3. Generate calldata and fork/static-call tasks after audit passes.",
            "4. Keep the broadcast gate closed.",
        ]
    lines = [
        "# Robinhood 链上测试包",
        "",
        f"- 网络：`{plan.get('network')}`",
        f"- 链 ID：`{plan.get('chainId')}`",
        f"- 路径：`{route.get('pathText')}`",
        f"- Execution mode: `{_text(_as_dict(plan.get('preflightSpec')).get('executionModel', {}).get('routeExecutionMode'))}`",
        f"- 触发区块：`{attempt.get('triggerBlockNumber')}`",
        f"- 复核区块：`{attempt.get('reviewBlockNumber')}`",
        f"- 证据状态：`{evidence.get('status')}`",
        f"- Trusted for execution: `{'yes' if decision.get('trustedForExecution') else 'no'}`",
        f"- Ready for preflight: `{'yes' if decision.get('readyForTransactionPreflight') else 'no'}`",
        "",
        "## Size Gate",
        "",
        f"- Requested borrow raw: `{_text(evidence.get('routeRequestedBorrowAmountRaw'))}`",
        f"- Max executable raw: `{_text(evidence.get('routeMaxExecutableAmountRaw'))}`",
    ]
    if evidence.get("routeExecutionSizeProbeExceeded"):
        lines.append(f"- Shortfall raw: `{_text(evidence.get('routeExecutionSizeShortfallRaw'))}`")
    lines.extend(
        [
            "",
            "## 结论",
            "",
            conclusion,
            "",
            "## 关键阻断",
        ]
    )
    blockers = evidence.get("blockers") or decision.get("blockedReasons") or []
    if blockers:
        lines.extend([f"- {item}" for item in blockers])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Next Steps",
            "",
            *next_steps,
        ]
    )
    return "\n".join(lines) + "\n"


def render_robinhood_chain_test_checklist(plan: dict[str, Any]) -> str:
    lines = ["# Robinhood 链上测试 checklist", ""]
    for item in plan.get("checklist") or []:
        if not isinstance(item, dict):
            continue
        mark = "x" if item.get("done") else " "
        lines.append(f"- [{mark}] {item.get('title')}: {item.get('detail')}")
    return "\n".join(lines) + "\n"


def _chain_contract_handoff_metadata(plan: dict[str, Any]) -> dict[str, Any]:
    network = _text(plan.get("network")).lower()
    execution_model = _as_dict(_as_dict(plan.get("preflightSpec")).get("executionModel"))
    common = {
        "network": plan["network"],
        "chainId": plan["chainId"],
        "marketId": plan["marketId"],
        "routePreflightStatus": plan["preflightSpec"]["status"],
        "repaymentConstraint": _as_dict(plan["preflightSpec"].get("repaymentConstraint")),
        "executionRequest": _as_dict(plan["preflightSpec"].get("executionRequest")),
        "executionModel": execution_model,
        "deploymentGate": "blocked_until_token_continuity_calldata_repayment_and_fork_staticcall_pass",
        "broadcastEnabled": False,
        "note": "read-only evidence package; no broadcast-ready executor has been deployed for this chain test",
    }
    if network == ROBINHOOD_TESTNET_NETWORK or plan.get("chainId") == ROBINHOOD_TESTNET_CHAIN_ID:
        return {
            **common,
            "scope": "robinhood_testnet_46630_only",
            "flashSourceKind": "uniswap_v4_poolmanager_flash_accounting",
            "v4PoolManager": TESTNET_V4_POOL_MANAGER_ADDRESS,
            "defaultV4Quoter": TESTNET_V4_QUOTER_ADDRESS,
            "v4UniversalRouter": TESTNET_V4_UNIVERSAL_ROUTER_ADDRESS,
            "permit2": TESTNET_PERMIT2_ADDRESS,
            "candidatePoolSupplyEvidence": None,
            "morphoBlueStatus": "not_publicly_verified_on_testnet_46630",
            "aaveStatus": "not_publicly_verified_on_testnet_46630",
        }
    return {
        **common,
        "scope": "robinhood_mainnet_4663_only",
        "wrappedNative": WETH_ADDRESS,
        "multicall3": MULTICALL_ADDRESS,
        "uniswapV2Pool": UNISWAP_V2_POOL_ADDRESS,
        "v3Quoter": V3_QUOTER_ADDRESS,
        "v4PoolManager": V4_POOL_MANAGER_ADDRESS,
        "defaultV4Quoter": V4_QUOTER_ADDRESS,
        "v4UniversalRouter": V4_UNIVERSAL_ROUTER_ADDRESS,
        "permit2": PERMIT2_ADDRESS,
    }


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
        _json_dump(contract_dir / "contracts.json", _chain_contract_handoff_metadata(plan))
        (contract_dir / "README.md").write_text(
            "\n".join(
                [
                    "# Robinhood Contract Notes",
                    "",
                    f"- Chain: `{plan['network']}`",
                    f"- Chain ID: `{plan['chainId']}`",
                    f"- Market: `{plan['marketId']}`",
                    "- Current state: read-only evidence and preflight preparation only",
                    f"- Current route preflight status: `{plan['preflightSpec']['status']}`",
                    f"- Route execution mode: `{_text(plan['preflightSpec'].get('executionModel', {}).get('routeExecutionMode'))}`",
                    "- Default V4 quoter: `0x8dc178efb8111bb0973dd9d722ebeff267c98f94`",
                    "- Deployment gate: blocked until token continuity, calldata, repayment, and fork/static-call pass",
                    "- Deployment: no dedicated broadcast executor is added by this package",
                    "- Route test scope: calldata, slippage, repayment, and fork/static-call verification",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return plan
