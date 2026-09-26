"""Robinhood mainnet trigger context capture.

This is a read-only helper used to timestamp review evidence. It never signs
or broadcasts a transaction.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dex.intent_trade.chains.robinhood.config import CHAIN_ID as ROBINHOOD_CHAIN_ID
from dex.intent_trade.chains.robinhood.config import NETWORK as ROBINHOOD_NETWORK


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _block_timestamp(block: Any) -> int | None:
    value = block.get("timestamp") if isinstance(block, dict) else getattr(block, "timestamp", None)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def capture_robinhood_trigger_context(
    *,
    chain_id: int | None,
    network: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed = observed_at or _utc_now_iso()
    normalized_network = str(network or ROBINHOOD_NETWORK).strip().lower()
    context: dict[str, Any] = {
        "source": "mainnet_live_trigger_unavailable",
        "observedAt": observed,
        "network": normalized_network,
        "chainId": chain_id or ROBINHOOD_CHAIN_ID,
        "available": False,
        "blockNumber": None,
        "triggerBlockNumber": None,
        "blockTimestamp": None,
        "rpcUrl": None,
        "error": None,
        "evidencePath": None,
    }
    if normalized_network != ROBINHOOD_NETWORK or int(chain_id or 0) != ROBINHOOD_CHAIN_ID:
        context["error"] = "robinhood mainnet trigger context requested outside mainnet scope"
        return context
    try:
        from dex.intent_trade.chains.robinhood.v4_pool_rpc import _connect_chain_rpc

        w3, rpc_url = _connect_chain_rpc()
        head_block_number = int(w3.eth.block_number)
        confirmations = max(0, int(os.getenv("DEX_ONCHAIN_TRIGGER_CONFIRMATIONS", "2") or 2))
        block_number = max(1, head_block_number - confirmations)
        block = w3.eth.get_block(block_number)
    except Exception as exc:
        context["error"] = str(exc)
        return context
    context.update(
        {
            "source": "mainnet_scan_trigger_chain_head_confirmed",
            "available": True,
            "blockNumber": block_number,
            "triggerBlockNumber": block_number,
            "blockTimestamp": _block_timestamp(block),
            "rpcUrl": rpc_url,
            "headBlockNumber": head_block_number,
            "confirmations": confirmations,
        }
    )
    return context


__all__ = ["capture_robinhood_trigger_context"]
