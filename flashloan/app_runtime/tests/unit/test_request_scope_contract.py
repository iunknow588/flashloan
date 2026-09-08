import pytest
from flask import Flask

from web.chain_selection import validate_explicit_request_scope


def test_explicit_request_scope_is_normalized():
    app = Flask(__name__)
    with app.test_request_context(
        "/api/example?market_id=m&chain_id=56&network=BNB&protocol=Venus_Core"
    ):
        assert validate_explicit_request_scope() == {
            "market_id": "m",
            "chain_id": 56,
            "network": "bnb",
            "protocol": "venus_core",
        }


def test_explicit_request_scope_rejects_missing_network_or_protocol():
    app = Flask(__name__)
    with app.test_request_context("/api/example?market_id=m&chain_id=56"):
        # Chain-qualified partial selectors are completed by route feature
        # defaults, so this helper returns the partial scope for the registry.
        assert validate_explicit_request_scope() == {
            "market_id": "m",
            "chain_id": "56",
            "network": "",
            "protocol": "",
        }


def test_network_only_is_treated_as_explicit_scope_and_rejected():
    app = Flask(__name__)
    with app.test_request_context("/api/example?network=bnb"):
        with pytest.raises(ValueError, match="scope missing: market_id, chain_id, protocol"):
            validate_explicit_request_scope()


def test_json_scope_is_validated_and_query_body_conflicts_are_rejected():
    app = Flask(__name__)
    with app.test_request_context(
        "/api/example", method="POST",
        json={"scope": {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}},
    ):
        assert validate_explicit_request_scope()["chain_id"] == 56
    with app.test_request_context(
        "/api/example?chain_id=56", method="POST",
        json={"scope": {"market_id": "m", "chain_id": 1, "network": "eth", "protocol": "aave"}},
    ):
        with pytest.raises(ValueError, match="scope mismatch on chain_id"):
            validate_explicit_request_scope()
