from types import SimpleNamespace

from dex.intent_trade.direct_submission_preflight import prepare_submission_inputs


class FakeDeps:
    direct_onchain_networks = {"avalanche": {"chain_id": 43114}}

    def normalize_direct_onchain_network(self, value):
        return str(value).strip().lower()

    def executor_adapter_unverified_status(self, network):
        return f"{network}_executor_unverified"

    def executor_adapter_unverified_error(self, network):
        return f"{network} executor evidence is missing"

    def resolve_direct_onchain_network(self, protocol):
        if protocol.get("network") == "unsupported":
            raise ValueError("unsupported network")
        return "avalanche", 43114, False

    def unified_executor_supported_for_network(self, network):
        return network == "avalanche"

    def env(self, *names):
        return "https://rpc.example" if "RPC" in names[0] else ""

    def env_bool(self, *names):
        return False

    def usdc_address_for_network(self, network):
        return "0x0000000000000000000000000000000000000001"

    def allow_single_pair_diagnostic(self, protocol, opportunity):
        return False

    def allow_stable_exercise_targets(self, protocol, opportunity):
        return False

    def runtime_route_group_candidates(self, protocol, opportunity):
        return protocol.get("route_groups") or []

    def prepare_unified_route_groups(self, route_groups, **kwargs):
        return route_groups, []

    def direct_rpc_url(self, network):
        return (None, ("AVALANCHE_RPC_URL",)) if network == "avalanche_missing_rpc" else ("https://rpc.example", ("AVALANCHE_RPC_URL",))


def _protocol(**values):
    return {
        "enabled": True,
        "network": "avalanche",
        "unified_executor_address": "0x0000000000000000000000000000000000000002",
        "route_groups": [{"trades": [{"tokenX": "0x1"}]}],
        **values,
    }


def test_preflight_blocks_missing_protocol():
    result = prepare_submission_inputs(FakeDeps(), quote_payload={}, opportunity={})

    assert result["blocked"]["blocked_reason"] == "direct_protocol_missing"


def test_preflight_blocks_unsupported_network():
    result = prepare_submission_inputs(
        FakeDeps(),
        quote_payload={"cow_flashloan_intent": {"direct_onchain_protocol": _protocol(network="unsupported")}},
        opportunity={},
    )

    assert result["blocked"]["blocked_reason"] == "order_submission_network_unsupported"


def test_preflight_blocks_empty_route_groups():
    result = prepare_submission_inputs(
        FakeDeps(),
        quote_payload={"cow_flashloan_intent": {"direct_onchain_protocol": _protocol(route_groups=[])}},
        opportunity={},
    )

    assert result["blocked"]["error"] == "runtime_trades_empty"


def test_preflight_returns_normalized_submission_inputs():
    result = prepare_submission_inputs(
        FakeDeps(),
        quote_payload={"cow_flashloan_intent": {"direct_onchain_protocol": _protocol()}},
        opportunity=SimpleNamespace(),
    )

    assert result["blocked"] is None
    assert result["network"] == "avalanche"
    assert result["expected_chain_id"] == 43114
    assert result["prepared_groups"]
    assert result["broadcast_enabled"] is False
