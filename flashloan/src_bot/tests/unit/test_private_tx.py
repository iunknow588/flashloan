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
    assert summary["status"] == "deferred_research_optional"
    assert "LIQUIDATION_PRIVATE_RPC_URLS" in summary["config"]
    assert summary["currentDelivery"] == "public_rpc_direct_after_fresh_gates"
    assert summary["fallback"] == "public_fallback_requires_explicit_opt_in_and_revalidation"


def test_private_broadcast_does_not_public_fallback_by_default():
    public_w3 = FakeWeb3("0xpublic")

    result = send_raw_transaction_private_first(
        b"raw",
        public_w3=public_w3,
        relay_urls="",
    )

    assert result["tx_hash"] is None
    assert result["broadcast_channel"] == "not_broadcast"
    assert public_w3.eth.sent == []


def test_private_broadcast_public_fallback_requires_explicit_opt_in():
    public_w3 = FakeWeb3("0xpublic")

    result = send_raw_transaction_private_first(
        b"raw",
        public_w3=public_w3,
        relay_urls="",
        allow_public_fallback=True,
    )

    assert result["tx_hash"] == "0xpublic"
    assert result["broadcast_channel"] == "public_rpc"
    assert public_w3.eth.sent == [b"raw"]


def test_private_broadcast_redacts_relay_errors(monkeypatch):
    from execution import private_tx

    relay_url = "https://relay.example/rpc?token=abc123"
    private_key = "0x" + "3" * 64
    monkeypatch.setenv("LIQUIDATION_PRIVATE_RPC_URLS", f"fast={relay_url}")

    class FailingEth:
        def send_raw_transaction(self, raw):
            raise RuntimeError(f"relay failed: {relay_url} private_key={private_key}")

    class FailingRelayWeb3:
        def __init__(self, provider):
            self.eth = FailingEth()

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return {"url": url, "request_kwargs": request_kwargs}

    monkeypatch.setattr(private_tx, "Web3", FailingRelayWeb3)
    public_w3 = FakeWeb3("0xpublic")

    result = send_raw_transaction_private_first(
        b"raw",
        public_w3=public_w3,
        allow_public_fallback=True,
    )
    error = result["relay_errors"][0]["error"]

    assert result["tx_hash"] == "0xpublic"
    assert relay_url not in error
    assert private_key not in error
    assert "abc123" not in error
    assert "[REDACTED]" in error
    assert result["private_relay_metrics"][0]["status"] == "relay_error"


def test_private_broadcast_defers_public_fallback_for_fresh_revalidation():
    public_w3 = FakeWeb3("0xpublic")

    result = send_raw_transaction_private_first(
        b"raw",
        public_w3=public_w3,
        relay_urls="",
        allow_public_fallback=True,
        defer_public_fallback=True,
        target_block=123,
    )

    assert result["tx_hash"] is None
    assert result["status"] == "public_fallback_revalidation_required"
    assert public_w3.eth.sent == []
