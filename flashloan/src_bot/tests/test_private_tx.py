from execution.private_tx import (
    configured_private_relays,
    private_relay_research_summary,
    send_raw_transaction_private_first,
)


class FakeHash:
    def __init__(self, value: str):
        self.value = value

    def hex(self):
        return self.value


class FakeEth:
    def __init__(self, tx_hash: str):
        self.tx_hash = tx_hash
        self.sent = []

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        return FakeHash(self.tx_hash)


class FakeWeb3:
    def __init__(self, tx_hash: str):
        self.eth = FakeEth(tx_hash)


def test_configured_private_relays_parses_named_and_unnamed_urls():
    relays = configured_private_relays("fast=https://relay.example, https://relay2.example")

    assert relays[0].name == "fast"
    assert relays[0].rpc_url == "https://relay.example"
    assert relays[1].name == "private_2"


def test_private_relay_summary_documents_optional_avalanche_channel():
    summary = private_relay_research_summary()

    assert summary["chain"] == "Avalanche C-Chain"
    assert summary["status"] == "optional_endpoint_required"
    assert "LIQUIDATION_PRIVATE_RPC_URLS" in summary["config"]


def test_private_broadcast_falls_back_to_public_rpc_when_no_relays():
    public_w3 = FakeWeb3("0xpublic")

    result = send_raw_transaction_private_first(
        b"raw",
        public_w3=public_w3,
        relay_urls="",
    )

    assert result["tx_hash"] == "0xpublic"
    assert result["broadcast_channel"] == "public_rpc"
    assert public_w3.eth.sent == [b"raw"]
