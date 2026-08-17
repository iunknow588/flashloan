from execution.liquidation_protocols import liquidation_protocol_adapter


def test_liquidation_protocol_adapter_selects_venus_observation_mode():
    adapter = liquidation_protocol_adapter("venus_core")
    capability = adapter.capability()

    assert capability.protocol == "venus_core"
    assert capability.observation_supported is True
    assert capability.execution_supported is False


def test_liquidation_protocol_adapter_selects_existing_aave():
    adapter = liquidation_protocol_adapter("aave_v3")

    assert adapter.capability().execution_supported is True


def test_venus_adapter_exposes_account_discovery(monkeypatch):
    adapter = liquidation_protocol_adapter("venus_core")

    def fake_discover(*_args, **_kwargs):
        return {"accounts": ["0x0000000000000000000000000000000000000001"]}

    monkeypatch.setattr("execution.liquidation_protocols.venus_core.discover_venus_borrower_accounts", fake_discover)

    assert adapter.discover_accounts(object(), [], from_block=1, to_block=2) == ["0x0000000000000000000000000000000000000001"]
