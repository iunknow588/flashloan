from cow_flashloan.capabilities import (
    FLASH_LOAN_AND_SETTLE_SIGNATURE,
    assess_cow_atomic_settlement_evidence,
    assess_cow_flashloan_sdk_plan,
)


def test_multi_hop_sdk_flashloan_plan_builds_router_payload_draft():
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
        lender_address="0x" + "4" * 40,
        borrower_address="0x" + "5" * 40,
        router_address="0x" + "6" * 40,
    )

    assert capability["periphery_function"] == FLASH_LOAN_AND_SETTLE_SIGNATURE
    assert capability["multi_step_route"] is True
    assert capability["three_hop_route"] is True
    assert capability["closed_cycle"] is True
    assert capability["current_implementation"] == "single_flashloan_collateralSwap_solver_path"
    assert capability["supports_multi_step_atomic_settlement"] is True
    assert capability["submission_safe"] is True
    assert capability["blockers"] == []
    assert capability["pending_fields"] == ["settlement"]
    assert capability["quote_probe_reliability"]["per_hop_quotes_are_not_atomicity_proof"] is True
    assert capability["quote_probe_reliability"]["must_not_post_independent_hop_orders"] is True
    router_payload = capability["router_payload"]
    assert router_payload["function"] == FLASH_LOAN_AND_SETTLE_SIGNATURE
    assert router_payload["loan_model"] == "single_flashloan_for_solver_settlement"
    assert router_payload["loan_count"] == 1
    assert router_payload["settlement_model"] == "single_flashloan_router_call_with_single_cow_solver_settlement"
    assert router_payload["flashloan_router_call_count"] == 1
    assert router_payload["cow_solver_order_count"] == 1
    assert router_payload["cow_settlement_transaction_count"] == 1
    assert router_payload["atomicity_evidence"]["atomicity_proven"] is False
    assert router_payload["borrowed_asset_symbol"] == "USDC"
    assert router_payload["repaid_asset_symbol"] == "USDC"
    assert router_payload["independent_per_hop_orders"] == 0
    assert router_payload["solver_path_symbols"] == ["USDC", "WAVAX", "WETH.E", "USDC"]
    assert router_payload["solver_intermediate_symbols"] == ["WAVAX", "WETH.E"]
    assert router_payload["closed_cycle"] is True
    assert router_payload["loans"][0]["token_symbol"] == "USDC"
    assert router_payload["loans"][0]["amount"] == "1000000"
    assert router_payload["loans"][0]["covers_solver_path"] == ["USDC", "WAVAX", "WETH.E", "USDC"]


def test_multi_hop_router_payload_tracks_unconfigured_live_fields_without_blocking_support():
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
    assert capability["pending_fields"] == ["router", "loan.borrower", "loan.lender", "settlement"]
    assert capability["router_payload"]["loan_count"] == 1
    assert capability["router_payload"]["loans"][0]["token_symbol"] == "USDC"
    assert capability["router_payload"]["independent_per_hop_orders"] == 0


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
    assert capability["router_payload"]["cow_solver_order_count"] == 0


def test_empty_route_capability_is_not_treated_as_blocking_three_hop_support():
    capability = assess_cow_flashloan_sdk_plan(route=[], steps=[])

    assert capability["multi_step_route"] is False
    assert capability["supports_multi_step_atomic_settlement"] is False
    assert capability["submission_safe"] is False
    assert capability["router_payload"]["loan_count"] == 0


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
