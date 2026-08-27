from importlib import import_module
from inspect import Parameter, signature

from core.chain_data_registry import load_chain_flow_specs
from core.chain_flow_dispatcher import chain_flow_request, dispatch_chain_flow


def test_chain_flow_request_resolves_chain_owned_flash_loan_pool_handler():
    request = chain_flow_request(chain_id=46630, flow_name="flash_loan_pools", protocol="uniswap_v4")

    assert request.global_chain_id == "chain:46630"
    assert request.network == "robinhood-testnet"
    assert request.handler == "intent_trade.chains.robinhood_testnet.flash_loan_pools:flash_loan_pool_protocol_view"
    assert request.flow["requires_scope_match"] is True
    assert request.chain_config["processing_independence"]["all_flows_require_scope_match"] is True


def test_dispatch_chain_flow_returns_processing_metadata():
    view = dispatch_chain_flow(chain_id=46630, flow_name="flash_loan_pools", protocol="uniswap_v4")

    assert view["protocol"] == "uniswap_v4"
    assert view["cache"]["chain_id"] == 46630
    assert view["processing"]["global_chain_id"] == "chain:46630"
    assert view["processing"]["handler"] == "intent_trade.chains.robinhood_testnet.flash_loan_pools:flash_loan_pool_protocol_view"
    assert view["processing"]["requires_scope_match"] is True


def test_dispatch_chain_flow_allows_chain_level_pool_discovery_without_protocol():
    request = chain_flow_request(chain_id=56, flow_name="pool_discovery")
    result = dispatch_chain_flow(chain_id=56, flow_name="pool_discovery")

    assert request.protocol == ""
    assert result["processing"]["flow_name"] == "pool_discovery"
    assert result["processing"]["chain_id"] == 56
    assert "protocol_summaries" in result


def test_registered_flow_handlers_are_chain_owned_dispatch_targets():
    for spec in load_chain_flow_specs():
        expected_prefix = f"intent_trade.chains.{spec['network'].replace('-', '_')}."
        for flow_name, flow in spec["flows"].items():
            handler_path = str(flow.get("handler") or "")
            if handler_path == "chain_specific_placeholder":
                continue

            assert handler_path.startswith(expected_prefix), (spec["network"], flow_name, handler_path)
            module_name, _, attr_name = handler_path.partition(":")
            handler = getattr(import_module(module_name), attr_name)
            parameters = signature(handler).parameters
            accepts_request = (
                "request" in parameters
                or any(parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values())
            )
            assert accepts_request, handler_path
