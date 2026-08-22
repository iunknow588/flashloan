from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib import request

from intent_trade.chains.robinhood import config as robinhood


_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
HttpTransport = Callable[[str, float], tuple[int, dict[str, Any]]]


def _default_transport(url: str, timeout_seconds: float) -> tuple[int, dict[str, Any]]:
    req = request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "flashloan-robinhood-blockscout-probe/1.0",
        },
        method="GET",
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return int(response.status), payload if isinstance(payload, dict) else {}


def probe_robinhood_blockscout_contract(
    address: str,
    *,
    explorer_url: str = robinhood.EXPLORER_URL,
    timeout_seconds: float = 8,
    transport: HttpTransport | None = None,
) -> dict[str, Any]:
    normalized = str(address or "").strip()
    report: dict[str, Any] = {
        "ok": False,
        "network": robinhood.NETWORK,
        "chainId": robinhood.CHAIN_ID,
        "address": normalized,
        "explorerUrl": str(explorer_url or "").rstrip("/"),
        "verified": False,
        "contractName": None,
        "endpoint": None,
    }
    if not _ADDRESS_RE.fullmatch(normalized):
        report["error"] = "invalid_contract_address"
        return report
    base_url = report["explorerUrl"]
    if not base_url.startswith(("https://", "http://")):
        report["error"] = "invalid_explorer_url"
        return report
    endpoint = f"{base_url}/api/v2/smart-contracts/{normalized}"
    report["endpoint"] = endpoint
    try:
        status, payload = (transport or _default_transport)(endpoint, float(timeout_seconds))
    except Exception as exc:
        report["error"] = str(exc)
        return report
    verified = bool(payload.get("is_verified", payload.get("isVerified", False)))
    report.update(
        {
            "httpStatus": int(status),
            "ok": int(status) == 200,
            "verified": verified,
            "contractName": payload.get("name") or payload.get("contract_name"),
            "compilerVersion": payload.get("compiler_version") or payload.get("compilerVersion"),
            "language": payload.get("language"),
        }
    )
    if int(status) != 200:
        report["error"] = payload.get("message") or payload.get("error") or "blockscout_contract_lookup_failed"
    return report


def probe_robinhood_official_contracts(
    *,
    timeout_seconds: float = 8,
    transport: HttpTransport | None = None,
) -> dict[str, Any]:
    contracts = {
        "weth": robinhood.WETH_ADDRESS,
        "multicall": robinhood.MULTICALL_ADDRESS,
    }
    reports = {
        label: probe_robinhood_blockscout_contract(
            address,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        for label, address in contracts.items()
    }
    return {
        "network": robinhood.NETWORK,
        "chainId": robinhood.CHAIN_ID,
        "ok": all(bool(item.get("ok")) for item in reports.values()),
        "contracts": reports,
        "executionMode": "readonly_blockscout_probe",
    }
