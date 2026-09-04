import pytest

from runtime.ui_signal_pool_evidence import write_ui_signal_pool_evidence


def test_ui_signal_evidence_writer_rejects_wrong_scope(tmp_path):
    scope = {"market_id": "m", "chain_id": 43114, "network": "avalanche", "protocol": "uniswap_v3"}
    evidence = {"scope": {**scope, "chain_id": 1}, "candidates": []}
    with pytest.raises(ValueError, match="scope mismatch on chain_id"):
        write_ui_signal_pool_evidence(
            evidence,
            evidence_dir=tmp_path / "evidence",
            latest_output=tmp_path / "latest.json",
            expected_scope=scope,
        )
    assert not (tmp_path / "latest.json").exists()
