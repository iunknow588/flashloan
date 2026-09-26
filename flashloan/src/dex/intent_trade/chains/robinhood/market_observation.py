"""Robinhood mainnet market observation extensions.

This module is chain-owned. It exists so the generic DEX observation flow can
find mainnet-specific bridge/fund observation hooks without writing chain
branches in shared processing code. It never imports or reads testnet evidence.
"""

from __future__ import annotations

from typing import Any

from dex.intent_trade.chains.robinhood.config import CHAIN_ID as ROBINHOOD_CHAIN_ID
from dex.intent_trade.chains.common.dex_candidate_tables import pair_symbol_universe


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _route_symbol_allowed(symbol: object) -> bool:
    text = _symbol(symbol)
    return bool(text) and not text.startswith("ROBINHOOD")


def _protocol_in_scope(protocols: list[str] | None) -> bool:
    protocol_set = {
        str(protocol or "").strip().lower()
        for protocol in (protocols or [])
        if str(protocol or "").strip()
    }
    return not protocol_set or "uniswap_v4" in protocol_set


def _chain_in_scope(chain_id: int | None) -> bool:
    return int(chain_id or 0) == ROBINHOOD_CHAIN_ID


def observation_anchor_symbols(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return []
    anchors = {
        _symbol(group.get("anchorSymbol"))
        for group in pool_payload.get("anchorGroups") or []
        if isinstance(group, dict) and _route_symbol_allowed(group.get("anchorSymbol"))
    }
    if anchors:
        return sorted(anchors)
    return sorted(
        {
            symbol
            for symbol in pair_symbol_universe(pool_payload.get("pairs") or [])
            if _route_symbol_allowed(symbol)
        }
    )


def borrowable_symbols_from_pool_payload(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return []
    return sorted(
        {
            symbol
            for symbol in pair_symbol_universe(pool_payload.get("pairs") or [])
            if _route_symbol_allowed(symbol)
        }
    )


def bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return [], {"enabled": False, "status": "chain_scope_invalid"}
    # Mainnet pool-index evidence is still being assembled. Returning an empty
    # bridge set is the read-only shadow behavior; it never falls back to
    # testnet bridge identities.
    return [], {
        "enabled": True,
        "status": "robinhood_mainnet_v4_pool_index_bridge_observation",
        "bridgeCount": 0,
    }


def fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return {
            "enabled": False,
            "status": "chain_scope_invalid",
            "routeCount": 0,
            "associatedObservationPairCount": 0,
            "associatedObservationPoolCount": 0,
            "associatedObservationTokenCount": 0,
            "observationSequences": [],
            "associatedObservationPairs": [],
            "associatedObservationPools": [],
            "associatedObservationTokens": [],
        }
    return {
        "enabled": True,
        "status": "robinhood_mainnet_v4_shadow_observation",
        "routeCount": 0,
        "associatedObservationPairCount": 0,
        "associatedObservationPoolCount": 0,
        "associatedObservationTokenCount": 0,
        "observationSequences": [],
        "associatedObservationPairs": [],
        "associatedObservationPools": [],
        "associatedObservationTokens": [],
    }


__all__ = [
    "borrowable_symbols_from_pool_payload",
    "bridge_pairs_for_anchor_groups",
    "fund_anchor_pair_observation",
    "observation_anchor_symbols",
]
