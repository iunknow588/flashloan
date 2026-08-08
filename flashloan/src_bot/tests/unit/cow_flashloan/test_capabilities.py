from cow_flashloan.capabilities import (
    assess_cow_atomic_settlement_evidence,
    assess_cow_flashloan_sdk_plan,
)


def test_multi_hop_sdk_flashloan_plan_builds_no_contract_intent_order():
    capability = assess_cow_flashloan_sdk_plan(
        route=["USDC", "WAVAX", "WETH.E", "USDC"],
        tokens=[
            {"symbol": "USDC", "address": "0x" + "1" * 40},
            {"symbol": "WAVAX", "address": "0x" + "2" * 40},
            {"symbol": "WETH.E", "address": "0x" + "3" * 40},
        ],
        steps=[
            {"step": 1, "from_symbol": "USDC"},
            {"step": 2, "from_symbol": "WAVAX"},
            {"step": 3, "from_symbol": "WETH.E"},
        ],
        hops=[
            {"sell_amount_units": "1000000"},
            {"sell_amount_units": "100000000000000000"},
            {"sell_amount_units": "1000000000000000"},
        ],
    )

    assert capability["submission_model"] == "cow_sdk_intent_order"
    assert capability["requires_custom_contract_deployment"] is False
    assert capability["custom_router_required"] is False
    assert capability["deployment_required"] is False
    assert capability["sdk_managed_flashloan"] is True
    assert capability["periphery_contract"] is None
    assert capability["periphery_function"] is None
    assert capability["requires_loan_array"] is False
    assert capability["requires_settlement_calldata"] is False
    assert capability["multi_step_route"] is True
    assert capability["three_hop_route"] is True
    assert capability["closed_cycle"] is True
    assert capability["current_implementation"] == "single_flashloan_collateralSwap_solver_path"
    assert capability["supports_multi_step_atomic_settlement"] is True
    assert capability["submission_safe"] is True
    assert capability["blockers"] == []
    assert capability["pending_fields"] == []
    assert "@cowprotocol/sdk-flash-loans" in capability["required_runtime_dependencies"]
    assert capability["quote_probe_reliability"]["per_hop_quotes_are_not_atomicity_proof"] is True
    assert capability["quote_probe_reliability"]["must_not_post_independent_hop_orders"] is True

    intent_order = capability["intent_order"]
    assert intent_order["submission_model"] == "cow_sdk_intent_order"
    assert intent_order["requires_custom_contract_deployment"] is False
    assert intent_order["settlement_model"] == "official_cow_solver_settlement"
    assert intent_order["flashloan_router_call_count"] == 0
    assert intent_order["cow_solver_order_count"] == 1
    assert intent_order["cow_settlement_transaction_count"] == 1
    assert intent_order["atomicity_evidence"]["atomicity_proven"] is False
    assert intent_order["borrowed_asset_symbol"] == "USDC"
    assert intent_order["repaid_asset_symbol"] == "USDC"
    assert intent_order["independent_per_hop_orders"] == 0
    assert intent_order["solver_path_symbols"] == ["USDC", "WAVAX", "WETH.E", "USDC"]
    assert intent_order["solver_intermediate_symbols"] == ["WAVAX", "WETH.E"]
    assert intent_order["closed_cycle"] is True
    assert intent_order["order"]["sell_token_symbol"] == "USDC"
    assert intent_order["order"]["sell_amount_before_fee"] == "1000000"
    assert intent_order["order"]["route"] == ["USDC", "WAVAX", "WETH.E", "USDC"]
    assert capability["router_payload"]["deprecated"] is True
    assert capability["router_payload"]["custom_router_required"] is False


def test_multi_hop_no_contract_plan_has_no_pending_contract_fields():
    capability = assess_cow_flashloan_sdk_plan(
        route=["USDC", "WAVAX", "WETH.E", "USDC"],
        tokens=[
            {"symbol": "USDC", "address": "0x" + "1" * 40},
            {"symbol": "WAVAX", "address": "0x" + "2" * 40},
            {"symbol": "WETH.E", "address": "0x" + "3" * 40},
        ],
        steps=[
            {"step": 1, "from_symbol": "USDC"},
            {"step": 2, "from_symbol": "WAVAX"},
            {"step": 3, "from_symbol": "WETH.E"},
        ],
        hops=[
            {"sell_amount_units": "1000000"},
            {"sell_amount_units": "100000000000000000"},
            {"sell_amount_units": "1000000000000000"},
        ],
    )

    assert capability["supports_multi_step_atomic_settlement"] is True
    assert capability["submission_safe"] is True
    assert capability["pending_fields"] == []
    assert capability["intent_order"]["order_count"] == 1
    assert capability["intent_order"]["order"]["sell_token_symbol"] == "USDC"
    assert capability["intent_order"]["independent_per_hop_orders"] == 0


def test_single_hop_is_not_a_supported_cow_flashloan_arbitrage_plan():
    capability = assess_cow_flashloan_sdk_plan(
        route=["USDC", "WETH"],
        steps=[{"step": 1, "from_symbol": "USDC", "to_symbol": "WETH"}],
    )

    assert capability["multi_step_route"] is False
    assert capability["three_hop_route"] is False
    assert capability["supports_multi_step_atomic_settlement"] is False
    assert capability["submission_safe"] is False
    assert capability["blockers"] == [
        "requires_at_least_three_hops",
        "route_must_return_to_borrowed_asset",
    ]
    assert capability["intent_order"]["cow_solver_order_count"] == 0


def test_empty_route_capability_is_not_treated_as_blocking_three_hop_support():
    capability = assess_cow_flashloan_sdk_plan(route=[], steps=[])

    assert capability["multi_step_route"] is False
    assert capability["supports_multi_step_atomic_settlement"] is False
    assert capability["submission_safe"] is False
    assert capability["intent_order"]["order_count"] == 0


def test_atomic_settlement_evidence_requires_one_order_and_one_settlement():
    incomplete = assess_cow_atomic_settlement_evidence(
        {
            "order_uids": ["0xorder1", "0xorder2"],
            "settlement_tx_hash": "0xtx",
            "flashloan_metadata_in_app_data": True,
            "solver_settlement_interactions_cover_requested_path_or_better": True,
            "final_repayment_of_flashloan_principal_plus_fee": True,
        }
    )
    assert incomplete["atomicity_proven"] is False
    assert incomplete["status"] == "not_proven"
    assert incomplete["missing_evidence"] == ["single_order_uid"]

    complete = assess_cow_atomic_settlement_evidence(
        {
            "order_uid": "0xorder1",
            "settlement_tx_hash": "0xtx",
            "flashloan_metadata_in_app_data": True,
            "solver_settlement_interactions_cover_requested_path_or_better": True,
            "final_repayment_of_flashloan_principal_plus_fee": True,
        }
    )
    assert complete["atomicity_proven"] is True
    assert complete["status"] == "proven"
    assert complete["order_uid_count"] == 1
