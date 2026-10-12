"""Robinhood mainnet V4 pool liquidity evidence helpers.

The mainnet flow is shadow/read-only for now. This module loads only
mainnet-scoped evidence and provides an explicit builder stub that returns
``evidence_required`` instead of inventing liquidity from testnet data.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from web3 import Web3

from core.chain_data_registry import chain_data_file_path
from dex.intent_trade.chains.robinhood.config import CHAIN_ID as ROBINHOOD_CHAIN_ID
from dex.intent_trade.chains.robinhood.config import NETWORK as ROBINHOOD_NETWORK
from dex.intent_trade.chains.robinhood.v4_pool_index import (
    _connect_rpc,
    _contract_address,
    _enrich_pool_liquidity,
    default_v4_pool_index_path,
)
from dex.intent_trade.chains.robinhood.v4_quote_adapter import _connect_chain_rpc


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    index_path: Path | None = None,
    rpc_url: str | None = None,
    hyperrpc_url: str | None = None,
    pool_manager: str | None = None,
    pool_ids: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    resolved_index = index_path or default_v4_pool_index_path()
    if resolved_index is None:
        raise ValueError("Robinhood mainnet v4_pool_index data file is not configured")
    index_payload = _read_json(resolved_index)
    raw_pools = index_payload.get("pools") or []
    if not raw_pools:
        raise ValueError(f"Robinhood mainnet v4_pool_index file has no pools: {resolved_index}")
    resolved_pool_manager = Web3.to_checksum_address(pool_manager or _contract_address("v4_pool_manager"))

    if rpc_url or hyperrpc_url:
        resolved_rpc_url = str(rpc_url or hyperrpc_url or "").strip()
        w3 = _connect_rpc(resolved_rpc_url)
    else:
        w3, resolved_rpc_url = _connect_chain_rpc(ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID)
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != ROBINHOOD_CHAIN_ID:
        raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {ROBINHOOD_CHAIN_ID}")

    pools = [deepcopy(row) for row in raw_pools if isinstance(row, dict)]
    scan_ids = [
        str(pool_id).strip().lower()
        for pool_id in (pool_ids or [])
        if str(pool_id or "").strip().startswith("0x")
    ]
    if scan_ids:
        wanted = set(scan_ids)
        pools = [pool for pool in pools if str(pool.get("poolId") or "").lower() in wanted]
    activity = _enrich_pool_liquidity(
        w3,
        pools,
        pool_manager=resolved_pool_manager,
        rpc_url=resolved_rpc_url,
        pool_ids=scan_ids,
    )
    payload = {
        "schemaVersion": "robinhood-v4-pool-liquidity-v1",
        "mode": "robinhood_mainnet_v4_pool_liquidity",
        "network": ROBINHOOD_NETWORK,
        "chainId": ROBINHOOD_CHAIN_ID,
        "poolManager": resolved_pool_manager,
        "indexSource": str(resolved_index),
        "indexGeneratedBlockNumber": index_payload.get("generatedBlockNumber"),
        "indexPoolCount": len(raw_pools),
        "generatedAt": _stamp(),
        "generatedBlockNumber": int(w3.eth.block_number),
        **activity,
        "poolCount": len(pools),
        "pools": pools,
        "poolKeys": list(index_payload.get("poolKeys") or index_payload.get("pool_keys") or []),
        "flashAccountingSources": [],
        "blockingReasons": [],
    }
    resolved = path or default_v4_pool_liquidity_path()
    if resolved is None:
        raise ValueError("Robinhood mainnet v4_pool_liquidity data file is not configured")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


__all__ = [
    "_known_token_pool_ids",
    "build_v4_pool_liquidity_from_contract_evidence",
    "default_v4_pool_liquidity_path",
    "load_robinhood_v4_pool_liquidity",
    "write_v4_pool_liquidity_from_contract_evidence",
]
