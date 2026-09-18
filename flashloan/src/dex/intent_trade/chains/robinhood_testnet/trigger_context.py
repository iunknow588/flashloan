from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object) -> str:
    return str(value or "").strip()


def _block_timestamp(block: Any) -> int | None:
    value = block.get("timestamp") if isinstance(block, dict) else getattr(block, "timestamp", None)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _live_trigger_context(*, chain_id: int | None, network: str, observed_at: str) -> dict[str, Any] | None:
    try:
        from dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter import _connect_chain_rpc

        w3, rpc_url = _connect_chain_rpc(network, chain_id)
        head_block_number = int(w3.eth.block_number)
        confirmations = max(0, int(os.getenv("DEX_ONCHAIN_TRIGGER_CONFIRMATIONS", "2") or 2))
        block_number = max(1, head_block_number - confirmations)
        block = w3.eth.get_block(block_number)
    except Exception:
        return None
    return {
        "source": "scan_trigger_chain_head_confirmed",
        "observedAt": observed_at,
        "network": network,
        "chainId": chain_id or ROBINHOOD_TESTNET_CHAIN_ID,
        "available": True,
        "blockNumber": block_number,
        "triggerBlockNumber": block_number,
        "blockTimestamp": _block_timestamp(block),
        "rpcUrl": rpc_url,
        "error": None,
        "headBlockNumber": head_block_number,
        "confirmations": confirmations,
        "evidencePath": None,
    }


def capture_robinhood_testnet_trigger_context(
    *,
    chain_id: int | None,
    network: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed = observed_at or _utc_now_iso()
    normalized_network = _text(network or ROBINHOOD_TESTNET_NETWORK).lower()
    context: dict[str, Any] = {
        "source": "testnet_live_trigger_unavailable",
        "observedAt": observed,
        "network": normalized_network,
        "chainId": chain_id or ROBINHOOD_TESTNET_CHAIN_ID,
        "available": False,
        "blockNumber": None,
        "triggerBlockNumber": None,
        "blockTimestamp": None,
        "rpcUrl": None,
        "error": None,
        "evidencePath": None,
    }
    if normalized_network != ROBINHOOD_TESTNET_NETWORK and int(chain_id or 0) != ROBINHOOD_TESTNET_CHAIN_ID:
        context["error"] = "testnet trigger context requested outside robinhood-testnet scope"
        return context

    live_context = _live_trigger_context(
        chain_id=chain_id,
        network=ROBINHOOD_TESTNET_NETWORK,
        observed_at=observed,
    )
    if live_context is not None:
        return live_context

    context["error"] = "live trigger block capture unavailable; refusing cached evidence fallback"
    return context


__all__ = ["capture_robinhood_testnet_trigger_context"]
