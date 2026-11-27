#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the ERC-20 metadata of the addresses two registries disagree on.

The funding registry and the stock registry publish **different**
addresses for the same symbol, and the audit reads the funding registry
as the authority.  A mismatch is therefore a *fact* about the store, and
the only thing that can settle it is the contract itself: the chain
knows which address is a token and what its decimals are.

The script reads ``symbol()``, ``decimals()``, ``name()`` and
``totalSupply()`` of every address in question over every RPC endpoint
that answers, and reports which endpoints were reachable.  It writes
nothing; it only produces the evidence for the decision.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / "flashloan" / "app_runtime" / ".env"

#: The ERC-20 fragments the script needs.
SELECTORS = {
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
    "name": "0x06fdde03",
    "totalSupply": "0x18160ddd",
}

#: The addresses the two registries disagree on, with the symbol they
#: claim for them.
CANDIDATES = [
    {"symbol": "NET", "registry": "v4_token_allowlist (funding)",
     "address": "0xca9c78dd337a67f6e0077f65f5e9218719d30edf",
     "claimedDecimals": 9, "claimedSource": "onchain-erc20-metadata"},
    {"symbol": "NET", "registry": "robinhood_stock_tokens (stock)",
     "address": "0x116F00968269B7bfbaD4109cE591d6E74c0601d4",
     "claimedDecimals": 18, "claimedSource": "robinhood_assets_api"},
    {"symbol": "DJT", "registry": "v4_token_allowlist (funding)",
     "address": "0x820012a9bfb19db161a1a059a6d83c2777fc0777",
     "claimedDecimals": 18, "claimedSource": "runtime/evidence/robinhood/yc_zc_tokens.json"},
    {"symbol": "DJT", "registry": "robinhood_stock_tokens (stock)",
     "address": "0x1D11f0496982706C5e14A514D4E79F2e6BdE4516",
     "claimedDecimals": 18, "claimedSource": "robinhood_assets_api"},
]


def rpc_urls() -> list[str]:
    """The RPC endpoints of the chain, out of its own environment file."""
    text = ENV_FILE.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^ROBINHOOD_RPCS=(.+)$", text, re.MULTILINE)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def call(url: str, method: str, params: list, timeout: int = 15) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as error:  # noqa: BLE001 - the endpoint may fail in any way
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}
    if "error" in payload:
        return {"ok": False, "error": str(payload["error"])[:200]}
    return {"ok": True, "result": payload.get("result")}


def decode_string(value: str | None) -> str | None:
    """Decode an ABI-encoded string, or ``None`` when it is not one."""
    if not value or not value.startswith("0x") or len(value) < 130:
        return None
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError:
        return None
    # The first word is the offset of the string, the second its length.
    offset = int.from_bytes(raw[0:32], "big")
    length = int.from_bytes(raw[offset:offset + 32], "big")
    return raw[offset + 32:offset + 32 + length].decode("utf-8", errors="replace")


def decode_uint(value: str | None) -> int | None:
    if not value or not value.startswith("0x"):
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def main() -> int:
    urls = rpc_urls()
    report: dict = {"rpcCount": len(urls), "candidates": [], "endpoints": []}
    alive: list[str] = []
    for url in urls:
        probe = call(url, "eth_chainId", [])
        chain = probe.get("result") if probe.get("ok") else None
        report["endpoints"].append({"url": url.split("/")[2], "alive": bool(probe.get("ok")),
                                     "chainId": int(chain, 16) if chain else None,
                                     "error": probe.get("error")})
        if probe.get("ok"):
            alive.append(url)

    for candidate in CANDIDATES:
        entry = dict(candidate)
        entry["readings"] = []
        for url in alive:
            reading: dict = {"endpoint": url.split("/")[2]}
            for field, selector in SELECTORS.items():
                result = call(url, "eth_call", [
                    {"to": candidate["address"], "data": selector}, "latest"])
                if not result.get("ok"):
                    reading[field] = {"error": result.get("error")}
                    continue
                raw = result.get("result")
                if field in ("symbol", "name"):
                    reading[field] = decode_string(raw)
                else:
                    reading[field] = decode_uint(raw)
            entry["readings"].append(reading)
        report["candidates"].append(entry)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
