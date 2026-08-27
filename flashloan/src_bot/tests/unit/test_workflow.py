import pytest

from core.workflow import (
    EXECUTION_MODE_COW,
    HARD_WORKFLOW_BLOCKS,
    SCOPE_MULTI_CHAIN,
    SCOPE_SINGLE_CHAIN,
    WORKFLOW_DEX_ARBITRAGE,
    WORKFLOW_LIQUIDATION,
    WorkflowContextError,
    resolve_workflow_context,
)


def test_liquidation_defaults_to_direct_single_chain():
    context = resolve_workflow_context("/bot/borrow-pool", {})

    assert context.workflow == WORKFLOW_LIQUIDATION
    assert context.execution_mode == "direct"
    assert context.scope_type == SCOPE_SINGLE_CHAIN
    assert context.execution_capable is True
    assert context.settlement_model == "direct_on_chain"
    assert context.atomic_cross_chain is False


def test_cow_dex_with_two_networks_is_multi_chain():
    context = resolve_workflow_context(
        "/dex/quotes",
        {
            "market_id": "cow-dex-arbitrage",
            "chain_id": "0",
            "cow_networks": "bnb,avalanche",
            "execution_mode": EXECUTION_MODE_COW,
        },
    )

    assert context.workflow == WORKFLOW_DEX_ARBITRAGE
    assert context.scope_type == SCOPE_MULTI_CHAIN
    assert context.selected_networks == ("bnb", "avalanche")
    assert context.execution_capable is True
    assert context.settlement_model == "independent_per_chain"
    assert context.atomic_cross_chain is False
    assert "atomic_cross_chain_settlement_not_implemented" in context.blocked_reasons


def test_liquidation_cow_is_explicitly_blocked_until_adapter_exists():
    context = resolve_workflow_context(
        "/bot/execution",
        {"execution_mode": EXECUTION_MODE_COW},
    )

    assert context.execution_capable is False
    assert "cow_liquidation_adapter_not_implemented" in context.blocked_reasons


def test_robinhood_shadow_is_observation_only():
    context = resolve_workflow_context("/dex/robinhood-shadow", {})

    assert context.execution_capable is False
    assert "robinhood_shadow_observation_only" in context.blocked_reasons
    assert "robinhood_shadow_observation_only" in HARD_WORKFLOW_BLOCKS


def test_robinhood_market_selection_blocks_generic_dex_execution():
    context = resolve_workflow_context(
        "/dex/quotes",
        {
            "market_id": "robinhood-dex-shadow",
            "chain_id": "4663",
            "workflow": "dex_arbitrage",
            "execution_mode": "cow",
            "scope": "single_chain",
        },
    )

    assert context.execution_capable is False
    assert "robinhood_shadow_observation_only" in context.blocked_reasons


def test_robinhood_market_rejects_bnb_cow_network_mismatch():
    with pytest.raises(WorkflowContextError, match="does not accept CoW network selection"):
        resolve_workflow_context(
            "/dex/quotes",
            {
                "market_id": "robinhood-dex-shadow",
                "chain_id": "4663",
                "workflow": "dex_arbitrage",
                "execution_mode": "cow",
                "scope": "single_chain",
                "cow_networks": "bnb",
            },
        )


def test_robinhood_market_rejects_robinhood_as_cow_network():
    with pytest.raises(WorkflowContextError, match="does not accept CoW network selection"):
        resolve_workflow_context(
            "/dex/quotes",
            {
                "market_id": "robinhood-dex-shadow",
                "chain_id": "4663",
                "workflow": "dex_arbitrage",
                "execution_mode": "cow",
                "scope": "single_chain",
                "cow_networks": "robinhood",
            },
        )


def test_dex_workflow_rejects_unknown_cow_network():
    with pytest.raises(WorkflowContextError, match="unsupported CoW network"):
        resolve_workflow_context(
            "/dex/quotes",
            {"market_id": "cow-dex-arbitrage", "chain_id": "0", "cow_networks": "robinhood"},
        )


def test_single_chain_dex_rejects_cow_network_scope():
    with pytest.raises(WorkflowContextError) as exc_info:
        resolve_workflow_context(
            "/dex/quotes",
            {"market_id": "bnb-dex-arbitrage", "chain_id": "56", "cow_networks": "bnb,avalanche"},
        )

    assert exc_info.value.code == "execution_scope_mismatch"


def test_single_chain_dex_rejects_multi_chain_scope():
    with pytest.raises(WorkflowContextError) as exc_info:
        resolve_workflow_context(
            "/dex/quotes",
            {"market_id": "polygon-dex-arbitrage", "chain_id": "137", "scope": SCOPE_MULTI_CHAIN},
        )

    assert exc_info.value.code == "multi_chain_requires_cow"


def test_direct_mode_cannot_claim_multi_chain_scope():
    with pytest.raises(WorkflowContextError, match="requires execution_mode=cow"):
        resolve_workflow_context(
            "/dex-arbitrage",
            {"execution_mode": "direct", "scope": SCOPE_MULTI_CHAIN},
        )


def test_analytics_page_is_chain_neutral():
    context = resolve_workflow_context("/analytics/opportunity-health", {})

    assert context.workflow == "chain_neutral"
    assert context.execution_mode is None
    assert context.execution_capable is False
    assert context.settlement_model == "analytics_only"
    assert context.blocked_reasons == ("chain_neutral_execution_blocked",)


def test_legacy_routes_remain_development_aliases():
    assert resolve_workflow_context("/liquidation", {}).workflow == WORKFLOW_LIQUIDATION
    assert resolve_workflow_context("/dex-arbitrage", {}).workflow == WORKFLOW_DEX_ARBITRAGE


