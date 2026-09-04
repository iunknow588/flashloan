import pytest

from db.scoped_adapter import validate_scoped_row, validate_scoped_rows


def test_db_row_scope_is_validated_before_domain_use():
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    assert validate_scoped_row({"scope": scope, "value": 1}, scope)["value"] == 1
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        validate_scoped_row({"scope": {**scope, "chain_id": 1}}, scope)


def test_db_result_set_fails_closed_with_row_index():
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    rows = [{"scope": scope, "value": 1}, {"scope": {**scope, "chain_id": 1}, "value": 2}]
    with pytest.raises(ValueError, match="index 1.*scope mismatch on chain_id"):
        validate_scoped_rows(rows, scope)
