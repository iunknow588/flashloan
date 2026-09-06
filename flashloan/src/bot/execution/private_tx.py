from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from web3 import Web3

from core.sensitive_data import redact_sensitive_text


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
        "status": "deferred_research_optional",
        "finding": (
            "Private relay / private deployment is not the current research direction for this project. "
            "The current direct-onchain path keeps freshness and net-profit gates, while privacy handling is deferred "
            "to later CoW or intent-layer work. Operator-supplied private RPC support remains an optional research tool."
        ),
        "config": "LIQUIDATION_PRIVATE_RPC_URLS=name=https://private-rpc.example,...",
        "activation": "requires explicit UNIFIED_EXECUTOR_PRIVATE_RELAY_RESEARCH_ENABLED=true",
        "currentDelivery": "public_rpc_direct_after_fresh_gates",
        "fallback": "public_fallback_requires_explicit_opt_in_and_revalidation",
    }


def send_raw_transaction_private_first(
    raw_transaction: bytes,
    *,
    public_w3: Web3,
    relay_urls: str | None = None,
    timeout_seconds: int = 10,
    allow_public_fallback: bool = False,
    target_block: int | None = None,
    defer_public_fallback: bool = False,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    metrics: list[dict[str, Any]] = []
    for relay in configured_private_relays(relay_urls):
        started = time.monotonic()
        try:
            relay_w3 = Web3(Web3.HTTPProvider(relay.rpc_url, request_kwargs={"timeout": timeout_seconds}))
            tx_hash = relay_w3.eth.send_raw_transaction(raw_transaction)
            response_ms = max(0, int((time.monotonic() - started) * 1000))
            metrics.append(
                {
                    "channel": "private_rpc",
                    "relay": relay.name,
                    "responseMs": response_ms,
                    "submittedAt": datetime.now(timezone.utc).isoformat(),
                    "targetBlock": str(target_block or ""),
                    "status": "accepted_by_private_rpc",
                }
            )
            return {
                "tx_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
                "broadcast_channel": "private_rpc",
                "relay": relay.name,
                "private_relay_metrics": metrics,
            }
        except Exception as exc:
            response_ms = max(0, int((time.monotonic() - started) * 1000))
            errors.append({"relay": relay.name, "error": redact_sensitive_text(exc)})
            metrics.append(
                {
                    "channel": "private_rpc",
                    "relay": relay.name,
                    "responseMs": response_ms,
                    "submittedAt": datetime.now(timezone.utc).isoformat(),
                    "targetBlock": str(target_block or ""),
                    "status": "relay_error",
                }
            )

    if not allow_public_fallback:
        return {
            "tx_hash": None,
            "broadcast_channel": "not_broadcast",
            "status": "private_relay_failed_public_fallback_disabled",
            "relay_errors": errors,
            "private_relay_metrics": metrics,
        }

    if defer_public_fallback:
        return {
            "tx_hash": None,
            "broadcast_channel": "not_broadcast",
            "status": "public_fallback_revalidation_required",
            "relay_errors": errors,
            "private_relay_metrics": metrics,
        }

    public_started = time.monotonic()
    tx_hash = public_w3.eth.send_raw_transaction(raw_transaction)
    metrics.append(
        {
            "channel": "public_rpc",
            "relay": "",
            "responseMs": max(0, int((time.monotonic() - public_started) * 1000)),
            "submittedAt": datetime.now(timezone.utc).isoformat(),
            "targetBlock": str(target_block or ""),
            "status": "public_fallback_submitted",
        }
    )
    return {
        "tx_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
        "broadcast_channel": "public_rpc",
        "relay_errors": errors,
        "private_relay_metrics": metrics,
    }
