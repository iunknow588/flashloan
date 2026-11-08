"""Unit tests for the read-only mainnet executor gate evidence tool."""

from __future__ import annotations

import types
from types import SimpleNamespace

import dex.tools.collect_robinhood_mainnet_readonly_gate as gate_tool


MAINNET_CONTRACTS = {
    "permit2": "0x000000000022D473030F116dDEE9F6B43aC78BA3",
    "v4_pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
    "v4_quoter": "0x8Dc178eFB8111BB0973Dd9d722ebeFF267c98F94",
    "position_manager": "0x58DaEC3116AAe6D93017BAAEA7749052E8a04fa7",
    "universal_router": "0x8876789976dEcBfCbBbe364623C63652db8C0904",
    "unified_executor": "",
}


class FakeWeb3:
    def __init__(self) -> None:
        self.eth = SimpleNamespace(
            chain_id=4663,
            block_number=95253810,
            get_code=lambda address, block_identifier=None: (
                b"\x00" if address not in MAINNET_CONTRACTS.values() else b"\x60\x00"
            ),
        )


def test_build_mainnet_readonly_gate_records_fail_closed_executor(
    monkeypatch,
) -> None:
    chain_row = {
        "chain_id": 4663,
        "contracts": dict(MAINNET_CONTRACTS),
        "rpc": {
            "primary": "https://rpc.mainnet.chain.robinhood.com",
            "fallbacks": ["https://robinhood-rpc.publicnode.com"],
        },
    }
    monkeypatch.setattr(gate_tool, "_chain_row", lambda network: chain_row)
    monkeypatch.setattr(
        gate_tool,
        "_connect",
        lambda chain_id, urls: (FakeWeb3(), urls[0]),
    )

    report = gate_tool.build_mainnet_readonly_gate()

    assert report["chainId"] == 4663
    assert report["blockNumber"] == 95253810
    assert report["executor"]["configured"] is False
    assert report["executor"]["hasCode"] is False
    assert report["readback"]["attempted"] is True
    assert report["readback"]["result"] == "mainnet_executor_not_configured_or_no_code"
    assert report["staticCall"]["attempted"] is False
    assert report["staticCall"]["reason"] == "mainnet_executor_not_deployed_or_configured"
    assert "mainnet_executor_not_configured" in report["blockingReasons"]
    assert report["broadcastEnabled"] is False
    assert report["readyForBroadcast"] is False
    assert report["writesSqlite"] is False
    assert report["v4Contracts"]["permit2"]["hasCode"] is True


def test_build_mainnet_readonly_gate_rejects_testnet_chain_id(
    monkeypatch,
) -> None:
    chain_row = {
        "chain_id": 46630,
        "contracts": {},
        "rpc": {"primary": ""},
    }
    monkeypatch.setattr(gate_tool, "_chain_row", lambda network: chain_row)

    try:
        gate_tool.build_mainnet_readonly_gate()
    except ValueError as exc:
        assert "expected mainnet chainId 4663" in str(exc)
    else:
        raise AssertionError("testnet chain id must be rejected")
