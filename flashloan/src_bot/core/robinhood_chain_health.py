from __future__ import annotations

import json
from typing import Any, Callable
from urllib import request

from intent_trade.chains.robinhood import config as robinhood


JsonRpcTransport = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _default_transport(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _rpc_call(
    rpc_url: str,
    method: str,
    params: list[Any] | None = None,
    *,
    timeout_seconds: float = 8,
    transport: JsonRpcTransport | None = None,
) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    result = (transport or _default_transport)(rpc_url, payload, float(timeout_seconds))
    if "error" in result:
        raise RuntimeError(f"{method} failed: {result['error']}")
    return result.get("result")


def _hex_int(value: Any) -> int:
    raw = str(value or "").strip()
    if raw.startswith(("0x", "0X")):
        return int(raw, 16)
    return int(raw, 10)


def _has_code(code: Any) -> bool:
    value = str(code or "").strip().lower()
    return value not in {"", "0x", "0x0"}


def probe_robinhood_rpc(
    rpc_url: str | None = None,
    *,
    testnet: bool = False,
    timeout_seconds: float = 8,
    transport: JsonRpcTransport | None = None,
) -> dict[str, Any]:
    urls = robinhood.testnet_rpc_urls_from_env() if testnet else robinhood.rpc_urls_from_env()
    selected_rpc = str(rpc_url or (urls[0] if urls else "")).strip()
    expected_chain_id = robinhood.TESTNET_CHAIN_ID if testnet else robinhood.CHAIN_ID
    network = robinhood.TESTNET_NETWORK if testnet else robinhood.NETWORK
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "ok": False,
        "network": network,
        "expectedChainId": expected_chain_id,
        "rpcUrl": selected_rpc,
        "checks": checks,
        "contracts": {
            "weth": robinhood.WETH_ADDRESS,
            "multicall": robinhood.MULTICALL_ADDRESS,
        },
        "executionMode": "readonly_probe",
    }
    if not selected_rpc:
        checks.append({"name": "rpc_url", "ok": False, "message": "Robinhood RPC URL is missing"})
        return report

    try:
        chain_id = _hex_int(
            _rpc_call(
                selected_rpc,
                "eth_chainId",
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
        )
        checks.append(
            {
                "name": "eth_chainId",
                "ok": chain_id == expected_chain_id,
                "value": chain_id,
                "message": f"chain id is {chain_id}, expected {expected_chain_id}",
            }
        )
        block_number = _hex_int(
            _rpc_call(
                selected_rpc,
                "eth_blockNumber",
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
        )
        checks.append(
            {
                "name": "eth_blockNumber",
                "ok": block_number > 0,
                "value": block_number,
                "message": f"latest block is {block_number}",
            }
        )
        for label, address in (
            ("weth_code", robinhood.WETH_ADDRESS),
            ("multicall_code", robinhood.MULTICALL_ADDRESS),
        ):
            code = _rpc_call(
                selected_rpc,
                "eth_getCode",
                [address, "latest"],
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
            checks.append(
                {
                    "name": label,
                    "ok": _has_code(code),
                    "address": address,
                    "message": "contract code is present" if _has_code(code) else "contract code is missing",
                }
            )
    except Exception as exc:
        checks.append({"name": "rpc_probe", "ok": False, "message": str(exc)})

    report["ok"] = bool(checks) and all(bool(check.get("ok")) for check in checks)
    return report

