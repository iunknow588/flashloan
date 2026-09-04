from src.core.capabilities import (
    Capability,
    CapabilityKey,
    CapabilityRegistry,
    CapabilityState,
)


def test_unknown_capability_is_unsupported_and_not_allowed():
    decision = CapabilityRegistry().resolve(CapabilityKey(46630, "robinhood", "v4", "dex"))
    assert decision.status == "unsupported"
    assert decision.reason == "capability_not_registered"
    assert not decision.allowed


def test_read_only_and_quote_ready_never_upgrade_to_execution():
    registry = CapabilityRegistry([
        Capability(CapabilityKey(1, "mainnet", "aave", "liquidation"), CapabilityState.READ_ONLY),
        Capability(CapabilityKey(46630, "robinhood", "v4", "dex"), CapabilityState.QUOTE_READY),
    ])
    assert registry.resolve(CapabilityKey(1, "MAINNET", "AAVE", "LIQUIDATION")).status == "not_ready"
    assert registry.resolve(CapabilityKey(46630, "robinhood", "v4", "dex")).status == "not_ready"


def test_only_explicit_execution_ready_is_allowed():
    key = CapabilityKey(46630, "robinhood", "v4", "dex")
    decision = CapabilityRegistry([Capability(key, CapabilityState.EXECUTION_READY)]).resolve(key)
    assert decision.status == "ok"
    assert decision.allowed
