import pytest

from core.db.storage_liquidation_attempts import _execution_attempt_from_row


def test_execution_attempt_row_rejects_wrong_network():
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    row = ("m", 56, "ethereum", "venus", 1, "0xabc", "flashloan", "submission_blocked", "[]", "{}", "{}", "{}", None, "{}", "blocked", None, None)

    with pytest.raises(ValueError, match="scope mismatch on network"):
        _execution_attempt_from_row(row, scope)
