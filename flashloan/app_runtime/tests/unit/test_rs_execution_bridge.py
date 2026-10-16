"""Unit tests for the Rust execution bridge using a fake flashloan_rs module."""

from __future__ import annotations

import json
from typing import Any

import pytest

import core.rs_bridge as rs_bridge
from dex.intent_trade.chains.robinhood import v4_quote_adapter
from dex.runtime import dex_route_rpc


class _FakeFlashloanRs:
    def __init__(self) -> None:
        self.sign_calls: list[tuple[str, str]] = []
        self.submit_calls: list[tuple[str, str, str | None]] = []
        self.receipt_calls: list[tuple[str, str, bool, int]] = []
        self.nonce_calls: list[tuple[str, str, int]] = []

    def resolve_private_key_hex(self, private_key: str) -> dict[str, Any]:
        return {"address": "0xfcad0b19bb29d4674531d6f115237e16afce377c"}

    def sign_typed_transaction_json(
        self,
        transaction_json: str,
        private_key: str,
    ) -> dict[str, Any]:
        self.sign_calls.append((transaction_json, private_key))
        return {
            "raw": "0x02f875",
            "hash": "0x6dff37dd1a996412e30723462231d2a67bc7d66702b498aa739746797c4302a3",
            "signer": "0xfcad0b19bb29d4674531d6f115237e16afce377c",
        }

    def build_v4_quote_calldata(self, request_json: str) -> dict[str, Any]:
        return {"calldata": "0x6501a3ce"}

    def quote_v4_exact_input_single(
        self,
        request_json: str,
        rpc_url: str,
        quoter_address: str,
        call_from: str | None = None,
        timeout_seconds: int = 20,
    ) -> dict[str, Any]:
        return {"ok": True, "amountOutRaw": "0x64", "gasEstimate": "0x5208"}

    def submit_v4_route(
        self,
        config_json: str,
        raw_transaction: str,
        relay_urls: str | None = None,
    ) -> dict[str, Any]:
        self.submit_calls.append((config_json, raw_transaction, relay_urls))
        return {
            "tx_hash": "0xabc",
            "broadcast_channel": "public_rpc",
            "relay": "",
            "private_relay_metrics": [],
        }

    def load_v4_receipt(
        self,
        rpc_urls_json: str,
        tx_hash: str,
        wait: bool,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        self.receipt_calls.append((rpc_urls_json, tx_hash, wait, timeout_seconds))
        return {
            "status": "confirmed",
            "rpcUrl": "http://rpc",
            "receipt": {
                "hash": tx_hash,
                "status": 1,
                "blockNumber": "0x1",
                "logs": [],
            },
            "transaction": {},
        }

    def get_v4_pending_nonce(
        self,
        rpc_url: str,
        signer_address: str,
        timeout_seconds: int = 20,
    ) -> dict[str, Any]:
        self.nonce_calls.append((rpc_url, signer_address, timeout_seconds))
        return {"nonce": 7}


@pytest.fixture
def fake_rust(monkeypatch: pytest.MonkeyPatch) -> _FakeFlashloanRs:
    fake = _FakeFlashloanRs()
    monkeypatch.setattr(rs_bridge, "_RUST_MODULE", fake)
    monkeypatch.setattr(rs_bridge, "_RUST_LOAD_ERROR", None)
    return fake


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: rs_bridge.resolve_private_key_hex("0x" + "11" * 32),
        lambda: rs_bridge.sign_typed_transaction_json({"chainId": 4663}, "0x" + "11" * 32),
        lambda: rs_bridge.build_v4_quote_calldata({"currency0": "0x" + "0" * 40}),
        lambda: rs_bridge.quote_v4_exact_input_single({}, "http://rpc", "0x" + "1" * 40),
        lambda: rs_bridge.submit_v4_route({"rpc_url": "http://rpc"}, "0x02"),
        lambda: rs_bridge.load_v4_receipt(["http://rpc"], "0xabc"),
        lambda: rs_bridge.get_v4_pending_nonce("http://rpc", "0xabc"),
    ],
)
def test_execution_bridge_requires_rust_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Any,
) -> None:
    monkeypatch.setattr(rs_bridge, "_RUST_MODULE", None)
    monkeypatch.setattr(rs_bridge, "_RUST_LOAD_ERROR", "unavailable")
    with pytest.raises(RuntimeError, match="Rust-owned"):
        invoke()


def test_execution_bridge_wrappers_call_rust(fake_rust: _FakeFlashloanRs) -> None:
    signed = rs_bridge.sign_typed_transaction_json({"chainId": 4663, "nonce": 7}, "0x" + "11" * 32)
    assert signed["raw"] == "0x02f875"
    assert json.loads(fake_rust.sign_calls[0][0])["chainId"] == 4663

    submitted = rs_bridge.submit_v4_route({"rpc_url": "http://rpc"}, "0x02")
    assert submitted["tx_hash"] == "0xabc"
    assert fake_rust.submit_calls[0][1] == "0x02"

    receipt = rs_bridge.load_v4_receipt(
        ["http://rpc"],
        "0xabc",
        wait=True,
        timeout_seconds=30,
    )
    assert receipt["status"] == "confirmed"
    assert fake_rust.receipt_calls[0][2] is True

    nonce = rs_bridge.get_v4_pending_nonce("http://rpc", "0xabc", timeout_seconds=30)
    assert nonce["nonce"] == 7
    assert fake_rust.nonce_calls[0][2] == 30


def test_fetch_dex_tx_receipt_prefers_rust(
    fake_rust: _FakeFlashloanRs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dex_route_rpc,
        "chain_data_by_chain_id",
        lambda chain_id: {
            "rpc_urls": ["http://rpc"],
            "network": "robinhood-testnet",
            "explorer_url": "",
        },
    )
    result = dex_route_rpc.fetch_dex_tx_receipt(chain_id=46630, tx_hash="0xabc")
    assert result["status"] == "confirmed"
    assert fake_rust.receipt_calls[0][2] is False


def test_v4_quote_adapter_uses_rust_revert_data(
    fake_rust: _FakeFlashloanRs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_quote(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "boom", "revertData": "0x486aa307"}

    monkeypatch.setattr(rs_bridge, "quote_v4_exact_input_single", fake_quote)
    monkeypatch.setattr(v4_quote_adapter, "_summarize_v4_revert_data", lambda data: "")

    class Provider:
        endpoint_uri = "http://rpc"

    class Eth:
        def contract(self, **kwargs: Any) -> Any:
            class Contract:
                class Functions:
                    def quoteExactInputSingle(self, params: Any) -> Any:
                        return object()

                functions = Functions()

            return Contract()

    class W3:
        provider = Provider()
        eth = Eth()

    with pytest.raises(RuntimeError) as exc_info:
        v4_quote_adapter._quote_v4_exact_input_single(
            w3=W3(),
            network="robinhood",
            chain_id=4663,
            quoter_address="0x" + "1" * 40,
            pool_key=(
                "0x" + "1" * 40,
                "0x" + "2" * 40,
                3000,
                60,
                "0x" + "0" * 40,
            ),
            zero_for_one=True,
            amount_in_raw=1,
            hook_data=b"",
        )
    assert getattr(exc_info.value, "data", None) == "0x486aa307"
