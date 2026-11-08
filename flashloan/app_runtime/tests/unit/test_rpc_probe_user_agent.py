from __future__ import annotations

import json
from unittest import mock

from core.validation.rpc_probe import _http_json_rpc


def test_http_json_rpc_uses_non_python_urllib_user_agent() -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x1"}).encode("utf-8")

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
    with mock.patch("core.validation.rpc_probe.urlopen", side_effect=fake_urlopen):
        result = _http_json_rpc("https://rpc.example", payload, 5.0)

    assert result == {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
    request = captured["request"]
    header_items = dict(request.header_items())
    user_agent = str(
        header_items.get("User-agent")
        or header_items.get("User-Agent")
        or ""
    )
    assert user_agent == "flashloan-robinhood-rpc-probe/1.0"
    assert not user_agent.startswith("Python-urllib/")
