from __future__ import annotations

from typing import Any


# Legacy names are kept only for old imports/history readers. The active
# submission path below is the no-self-deployed-contract CoW SDK intent model.
FLASH_LOAN_ROUTER_INTERFACE = "legacy_custom_router_disabled"
FLASH_LOAN_AND_SETTLE_SIGNATURE = "legacy_custom_router_call_disabled"
FLASH_LOAN_ROUTER_ADDRESS = ""
AAVE_BORROWER_ADDRESS = ""
ERC3156_BORROWER_ADDRESS = ""
SDK_FLASH_LOANS_PACKAGE = "@cowprotocol/sdk-flash-loans"
SDK_FLASH_LOANS_FLOW = "AaveCollateralSwapSdk"
SDK_FLASH_LOANS_METHOD = "postSwapOrderFromQuote"
SDK_INTENT_SUBMISSION_MODEL = "cow_sdk_intent_order"
SDK_INTENT_SUBMISSION_METHOD = (
    "TradingSdk.getQuote + AaveCollateralSwapSdk.getOrderPostingSettings + "
    "quoteAndPost.postSwapOrderFromQuote"
)
REQUIRED_RUNTIME_DEPENDENCIES = [
    "@cowprotocol/sdk-common",
    "@cowprotocol/sdk-viem-adapter",
    "@cowprotocol/sdk-config",
    "@cowprotocol/sdk-order-book",
    "@cowprotocol/sdk-trading",
    "@cowprotocol/sdk-flash-loans",
    "viem",
    "dotenv",
]
MIN_ATOMIC_SOLVER_HOP_COUNT = 3


def _route_symbols(route: Any) -> list[str]:
    if not isinstance(route, list):
        return []
    return [str(item or "").strip().upper() for item in route if str(item or "").strip()]


def _hop_count(route: list[str], steps: list[dict[str, Any]]) -> int:
    if len(route) >= 2:
        return len(route) - 1
    return len(steps)


def _closed_cycle(route: list[str]) -> bool:
    return len(route) >= 2 and route[0] == route[-1]


def assess_cow_atomic_settlement_evidence(evidence: Any) -> dict[str, Any]:
    """Validate runtime evidence for one CoW settlement, not quote-time intent fields."""
    payload = evidence if isinstance(evidence, dict) else {}
    order_uids = payload.get("order_uids")
    if not isinstance(order_uids, list):
        order_uids = [payload.get("order_uid")] if payload.get("order_uid") else []
    checks = {
        "single_order_uid": len([item for item in order_uids if str(item or "").strip()]) == 1,
        "single_settlement_tx_hash": bool(str(payload.get("settlement_tx_hash") or "").strip()),
        "flashloan_metadata_in_app_data": bool(payload.get("flashloan_metadata_in_app_data")),
        "solver_settlement_interactions_cover_requested_path_or_better": bool(
            payload.get("solver_settlement_interactions_cover_requested_path_or_better")
        ),
        "final_repayment_of_flashloan_principal_plus_fee": bool(
            payload.get("final_repayment_of_flashloan_principal_plus_fee")
        ),
    }
    missing = [name for name, passed in checks.items() if not passed]
    return {
        "atomicity_proven": not missing,
        "status": "proven" if not missing else "not_proven",
        "checks": checks,
        "missing_evidence": missing,
        "order_uid_count": len([item for item in order_uids if str(item or "").strip()]),
    }


def _token_by_symbol(tokens: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(tokens, list):
        return {}
    result = {}
    for token in tokens:
        if not isinstance(token, dict):
            continue
        symbol = str(token.get("symbol") or "").strip().upper()
        if symbol:
            result[symbol] = token
    return result


def _hop_amount_units(hop: dict[str, Any] | None, step: dict[str, Any]) -> str | None:
    if isinstance(hop, dict):
        value = hop.get("sell_amount_units")
        if value not in (None, ""):
            return str(value)
    sdk = step.get("cow_sdk_parameters") if isinstance(step.get("cow_sdk_parameters"), dict) else {}
    value = sdk.get("sell_amount_before_fee") or step.get("query_sell_amount_before_fee")
    return str(value) if value not in (None, "") else None


def build_cow_sdk_intent_order_draft(
    *,
    route: Any,
    steps: Any,
    tokens: Any = None,
    hops: Any = None,
) -> dict[str, Any]:
    route_items = _route_symbols(route)
    step_items = [item for item in steps or [] if isinstance(item, dict)] if isinstance(steps, list) else []
    hop_items = [item for item in hops or [] if isinstance(item, dict)] if isinstance(hops, list) else []
    token_map = _token_by_symbol(tokens)
    first_step = step_items[0] if step_items else {}
    start_symbol = route_items[0] if route_items else str(first_step.get("from_symbol") or "").strip().upper()
    token = token_map.get(start_symbol, {})
    amount = _hop_amount_units(hop_items[0] if hop_items else None, step_items[0] if step_items else {})
    hop_count = _hop_count(route_items, step_items)
    closed_cycle = _closed_cycle(route_items)
    three_hop_route = hop_count >= MIN_ATOMIC_SOLVER_HOP_COUNT
    order = {
        "sell_token": token.get("address"),
        "sell_token_symbol": start_symbol or None,
        "sell_amount_before_fee": amount,
        "sell_amount_source": "first_hop.sell_amount_units",
        "buy_token_symbol": route_items[-1] if route_items else None,
        "route": route_items,
        "route_hop_count": hop_count,
        "closed_cycle": closed_cycle,
        "solver_path_symbols": route_items,
        "solver_intermediate_symbols": route_items[1:-1] if len(route_items) > 2 else [],
        "ready": bool(start_symbol and amount and three_hop_route and closed_cycle),
    }
    atomicity_evidence = assess_cow_atomic_settlement_evidence({})
    return {
        "submission_model": SDK_INTENT_SUBMISSION_MODEL,
        "submission_method": SDK_INTENT_SUBMISSION_METHOD,
        "sdk_method": SDK_FLASH_LOANS_METHOD,
        "requires_custom_contract_deployment": False,
        "custom_router_required": False,
        "deployment_required": False,
        "sdk_managed_flashloan": True,
        "order": order,
        "order_count": 1 if three_hop_route and closed_cycle else 0,
        "loan_count": 0,
        "loan_model": "sdk_managed_flashloan_no_user_loan_array",
        "loan_order_matches_route_order": False,
        "borrowed_asset_symbol": start_symbol or None,
        "repaid_asset_symbol": route_items[-1] if route_items else None,
        "route_hop_count": hop_count,
        "required_min_hop_count": MIN_ATOMIC_SOLVER_HOP_COUNT,
        "three_hop_route": three_hop_route,
        "settlement_model": "official_cow_solver_settlement",
        "flashloan_router_call_count": 0,
        "cow_solver_order_count": 1 if three_hop_route and closed_cycle else 0,
        "cow_settlement_transaction_count": 1 if three_hop_route and closed_cycle else 0,
        "solver_path_symbols": route_items,
        "solver_intermediate_symbols": route_items[1:-1] if len(route_items) > 2 else [],
        "closed_cycle": closed_cycle,
        "independent_per_hop_orders": 0,
        "per_hop_quotes_allowed_for_diagnostics": True,
        "settlement_calldata": None,
        "settlement_calldata_status": "not_required_for_sdk_intent_order",
        "route": route_items,
        "ready": bool(order["ready"]),
        "atomicity_evidence": atomicity_evidence,
        "reliability_evidence_required": [
            "single_order_uid",
            "single_settlement_tx_hash",
            "flashloan_metadata_in_app_data",
            "solver_settlement_interactions_cover_requested_path_or_better",
            "final_repayment_of_flashloan_principal_plus_fee",
        ],
    }


def build_flashloan_and_settle_draft(
    *,
    route: Any,
    steps: Any,
    tokens: Any = None,
    hops: Any = None,
    lender_address: str | None = None,
    borrower_address: str | None = None,
    router_address: str | None = None,
    settlement_calldata: str | None = None,
) -> dict[str, Any]:
    draft = build_cow_sdk_intent_order_draft(route=route, steps=steps, tokens=tokens, hops=hops)
    return {
        "deprecated": True,
        "interface": None,
        "function": None,
        "router": None,
        "borrower_default": None,
        "requires_custom_contract_deployment": False,
        "custom_router_required": False,
        "deployment_required": False,
        "replacement": SDK_INTENT_SUBMISSION_MODEL,
        **draft,
    }


def assess_cow_flashloan_sdk_plan(
    *,
    route: Any,
    steps: Any,
    tokens: Any = None,
    hops: Any = None,
    lender_address: str | None = None,
    borrower_address: str | None = None,
    router_address: str | None = None,
    settlement_calldata: str | None = None,
) -> dict[str, Any]:
    """Describe the no-contract CoW SDK intent submission path."""
    route_items = _route_symbols(route)
    step_items = [item for item in steps or [] if isinstance(item, dict)] if isinstance(steps, list) else []
    hop_items = [item for item in hops or [] if isinstance(item, dict)] if isinstance(hops, list) else []
    hop_count = _hop_count(route_items, step_items)
    closed_cycle = _closed_cycle(route_items)
    three_hop_route = hop_count >= MIN_ATOMIC_SOLVER_HOP_COUNT
    supports_three_hop = three_hop_route and closed_cycle
    intent_order = build_cow_sdk_intent_order_draft(
        route=route_items,
        steps=step_items,
        tokens=tokens,
        hops=hop_items,
    )
    blockers = [
        name
        for name, blocked in {
            "requires_at_least_three_hops": hop_count < MIN_ATOMIC_SOLVER_HOP_COUNT,
            "route_must_return_to_borrowed_asset": bool(route_items) and not closed_cycle,
        }.items()
        if blocked
    ]
    return {
        "sdk_package": SDK_FLASH_LOANS_PACKAGE,
        "sdk_flow": SDK_FLASH_LOANS_FLOW,
        "sdk_method": SDK_FLASH_LOANS_METHOD,
        "submission_model": SDK_INTENT_SUBMISSION_MODEL,
        "submission_method": SDK_INTENT_SUBMISSION_METHOD,
        "sdk_order_scope": "single_cow_order_solver_settlement",
        "periphery_contract": None,
        "periphery_function": None,
        "requires_custom_contract_deployment": False,
        "custom_router_required": False,
        "deployment_required": False,
        "sdk_managed_flashloan": True,
        "required_runtime_dependencies": REQUIRED_RUNTIME_DEPENDENCIES,
        "route": route_items,
        "hop_count": hop_count,
        "multi_step_route": hop_count > 1,
        "three_hop_route": three_hop_route,
        "closed_cycle": closed_cycle,
        "current_implementation": (
            "single_flashloan_collateralSwap_solver_path"
            if supports_three_hop
            else "not_supported_single_or_two_hop_flashloan_arb"
        ),
        "requires_loan_array": False,
        "requires_settlement_calldata": False,
        "intent_order": intent_order,
        "router_payload": {
            "deprecated": True,
            "custom_router_required": False,
            "deployment_required": False,
            "replacement": SDK_INTENT_SUBMISSION_MODEL,
        },
        "loan_count": 0,
        "settlement_calldata_present": False,
        "periphery_flashloan_and_settle_ready": False,
        "single_order_hooks_ready": bool(step_items) and supports_three_hop,
        "supports_multi_step_atomic_settlement": supports_three_hop,
        "tested_as_one_atomic_settlement": bool(intent_order.get("ready")),
        "atomicity_evidence": intent_order["atomicity_evidence"],
        "submission_safe": supports_three_hop,
        "blockers": blockers,
        "quote_probe_reliability": {
            "per_hop_quotes_are_not_atomicity_proof": True,
            "collateralSwap_order_must_be_single_order": True,
            "must_not_post_independent_hop_orders": True,
            "minimum_reliable_live_evidence": intent_order["reliability_evidence_required"],
        },
        "pending_fields": [],
    }
