from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_network, chain_data_file_path
from intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        return {"_error": f"invalid_json:{exc.msg}"}
    return payload if isinstance(payload, dict) else {"_error": "invalid_json_root"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object) -> str:
    return str(value or "").strip()


def _int_or_none(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _latest_v4_liquidity_path() -> Path | None:
    path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_liquidity")
    if path is not None:
        return path
    return Path(__file__).resolve().parents[3] / "runtime" / "evidence" / ROBINHOOD_TESTNET_NETWORK / "v4_pool_liquidity" / "latest.json"


def capture_robinhood_testnet_trigger_context(
    *,
    chain_id: int | None,
    network: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed = observed_at or _utc_now_iso()
    normalized_network = _text(network or ROBINHOOD_TESTNET_NETWORK).lower()
    context: dict[str, Any] = {
        "source": "testnet_local_v4_pool_liquidity_cache",
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

    path = _latest_v4_liquidity_path()
    if path is None:
        context["error"] = "testnet v4 liquidity evidence path is not configured"
        return context
    payload = _read_json(path)
    if payload.get("_error"):
        context["error"] = str(payload["_error"])
        context["evidencePath"] = str(path)
        return context
    block_number = _int_or_none(payload.get("generatedBlockNumber") or payload.get("block_number"))
    if block_number is None:
        context["error"] = "missing generatedBlockNumber in robinhood-testnet v4 liquidity evidence"
        context["evidencePath"] = str(path)
        return context

    rpc_url = ""
    try:
        chain_row = chain_data_by_network(ROBINHOOD_TESTNET_NETWORK) or {}
        rpc_url = _text(chain_row.get("rpc_url"))
    except Exception:
        rpc_url = ""

    context.update(
        {
            "available": True,
            "blockNumber": block_number,
            "triggerBlockNumber": block_number,
            "rpcUrl": rpc_url or None,
            "evidencePath": str(path),
            "evidenceBlockNumber": block_number,
            "evidenceGeneratedAt": payload.get("generatedAt"),
            "evidenceSource": payload.get("discoveryMethod"),
        }
    )
    return context


__all__ = ["capture_robinhood_testnet_trigger_context"]
