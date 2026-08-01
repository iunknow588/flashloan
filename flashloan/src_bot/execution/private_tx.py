from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from web3 import Web3


@dataclass(frozen=True)
class PrivateRelayEndpoint:
    name: str
    rpc_url: str


def configured_private_relays(raw: str | None = None) -> list[PrivateRelayEndpoint]:
    value = os.getenv("LIQUIDATION_PRIVATE_RPC_URLS", "") if raw is None else raw
    relays: list[PrivateRelayEndpoint] = []
    for index, item in enumerate(str(value or "").split(","), start=1):
        token = item.strip()
        if not token:
            continue
        if "=" in token:
            name, rpc_url = token.split("=", 1)
        else:
            name, rpc_url = f"private_{index}", token
        rpc_url = rpc_url.strip()
        if rpc_url:
            relays.append(PrivateRelayEndpoint(name=name.strip() or f"private_{index}", rpc_url=rpc_url))
    return relays


def private_relay_research_summary() -> dict[str, Any]:
    return {
        "chain": "Avalanche C-Chain",
        "status": "optional_endpoint_required",
        "finding": (
            "Avalanche C-Chain does not expose a universally documented Flashbots-style public relay in this codebase. "
            "The bot supports operator-supplied private RPC endpoints when a provider offers one."
        ),
        "config": "LIQUIDATION_PRIVATE_RPC_URLS=name=https://private-rpc.example,...",
        "fallback": "public_rpc_parallel_wallet_submission",
    }


def send_raw_transaction_private_first(
    raw_transaction: bytes,
    *,
    public_w3: Web3,
    relay_urls: str | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    for relay in configured_private_relays(relay_urls):
        try:
            relay_w3 = Web3(Web3.HTTPProvider(relay.rpc_url, request_kwargs={"timeout": timeout_seconds}))
            tx_hash = relay_w3.eth.send_raw_transaction(raw_transaction)
            return {
                "tx_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
                "broadcast_channel": "private_rpc",
                "relay": relay.name,
            }
        except Exception as exc:
            errors.append({"relay": relay.name, "error": str(exc)})

    tx_hash = public_w3.eth.send_raw_transaction(raw_transaction)
    return {
        "tx_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
        "broadcast_channel": "public_rpc",
        "relay_errors": errors,
    }
