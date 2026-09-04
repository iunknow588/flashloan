import pytest

from core.flash_loan_pool_store import load_flash_loan_pool_static, write_flash_loan_pool_static


def test_flash_loan_pool_static_store_checks_scope(tmp_path):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    view = {"protocol": "venus", "summary": {}, "sections": {}}
    write_flash_loan_pool_static(view, network="bnb", chain_id=56, root=tmp_path, expected_scope=scope)
    loaded = load_flash_loan_pool_static(network="bnb", chain_id=56, protocol="venus", root=tmp_path, expected_scope=scope)
    assert loaded["scope"] == scope
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        load_flash_loan_pool_static(network="bnb", chain_id=56, protocol="venus", root=tmp_path, expected_scope={**scope, "chain_id": 1})
