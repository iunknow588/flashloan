"""Robinhood mainnet V4 pool liquidity evidence helpers.

The mainnet flow is shadow/read-only for now. This module loads only
mainnet-scoped evidence and provides an explicit builder stub that returns
``evidence_required`` instead of inventing liquidity from testnet data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from dex.intent_trade.chains.robinhood.config import CHAIN_ID as ROBINHOOD_CHAIN_ID
from dex.intent_trade.chains.robinhood.config import NETWORK as ROBINHOOD_NETWORK


def default_v4_pool_liquidity_path() -> Path | None:
    return chain_data_file_path(ROBINHOOD_NETWORK, "v4_pool_liquidity", chain_id=ROBINHOOD_CHAIN_ID)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _known_token_pool_ids() -> dict[str, list[str]]:
    """Return token-symbol -> mainnet V4 pool ids from the mainnet index.

    This is a shadow helper. It only reads mainnet evidence and is not allowed
    to substitute testnet pool ids.
    """
    index_path = chain_data_file_path(ROBINHOOD_NETWORK, "v4_pool_index", chain_id=ROBINHOOD_CHAIN_ID)
    payload = _read_json(index_path)
    grouped: dict[str, list[str]] = {}
    for row in payload.get("pools") or []:
        if not isinstance(row, dict):
            continue
        for key in ("token0Symbol", "token1Symbol"):
            symbol = str(row.get(key) or "").strip().upper()
            pool_id = str(row.get("poolId") or row.get("pool") or "").strip()
            if symbol and pool_id:
                grouped.setdefault(symbol, [])
                if pool_id not in grouped[symbol]:
                    grouped[symbol].append(pool_id)
    return grouped


def load_robinhood_v4_pool_liquidity(path: Path | None = None) -> dict[str, Any]:
    resolved = path or default_v4_pool_liquidity_path()
    payload = _read_json(resolved)
    if payload:
        payload.setdefault("mode", "robinhood_mainnet_v4_pool_liquidity")
        payload.setdefault("chainId", ROBINHOOD_CHAIN_ID)
        payload.setdefault("network", ROBINHOOD_NETWORK)
        return payload
    return {
        "mode": "robinhood_mainnet_v4_pool_liquidity",
        "chainId": ROBINHOOD_CHAIN_ID,
        "network": ROBINHOOD_NETWORK,
        "status": "evidence_required",
        "pools": [],
        "flashAccountingSources": [],
    }


def build_v4_pool_liquidity_from_contract_evidence(
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "mode": "robinhood_mainnet_v4_pool_liquidity_shadow",
        "chainId": ROBINHOOD_CHAIN_ID,
        "network": ROBINHOOD_NETWORK,
        "status": "evidence_required",
        "pools": [],
        "flashAccountingSources": [],
        "detail": "Mainnet liquidity evidence remains shadow/read-only; no testnet fallback is allowed.",
    }


def write_v4_pool_liquidity_from_contract_evidence(
    *,
    path: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return build_v4_pool_liquidity_from_contract_evidence(**kwargs)


__all__ = [
    "_known_token_pool_ids",
    "build_v4_pool_liquidity_from_contract_evidence",
    "default_v4_pool_liquidity_path",
    "load_robinhood_v4_pool_liquidity",
    "write_v4_pool_liquidity_from_contract_evidence",
]
