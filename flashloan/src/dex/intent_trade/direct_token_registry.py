from __future__ import annotations

from pathlib import Path
from typing import Any

from dex.intent_trade.direct_constants import DIRECT_ONCHAIN_NETWORKS
from dex.intent_trade.direct_onchain_config import (
    normalize_direct_onchain_network as _normalize_direct_onchain_network,
    normalize_pair_address as _normalize_pair_address,
)
from dex.intent_trade.direct_unified_cache import (
    load_unified_token_registry as _load_unified_token_registry,
    parse_utc_datetime as _parse_utc_datetime,
    unified_token_registry_path as _unified_token_registry_path,
    utc_now as _utc_now,
)

def _registry_token_risks_for_trades(
    trades: list[dict[str, Any]],
    *,
    network: str,
    registry: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Resolve reviewed token metadata for one dynamically generated route group."""
    payload = registry if isinstance(registry, dict) else _load_unified_token_registry()
    expected_chain_id = DIRECT_ONCHAIN_NETWORKS.get(
        _normalize_direct_onchain_network(network),
        {},
    ).get("chain_id")
    try:
        registry_chain_id = int(payload.get("chainId") or payload.get("chain_id") or 0)
    except (TypeError, ValueError):
        registry_chain_id = 0
    if not payload or not expected_chain_id or registry_chain_id != expected_chain_id:
        return None, {
            "source": "registry",
            "available": False,
            "reason": "token_registry_chain_mismatch_or_unavailable",
            "registryPath": str(_unified_token_registry_path()),
        }
    if payload.get("_integrityRequired") and not payload.get("_integrityOk"):
        return None, {
            "source": "registry",
            "available": False,
            "reason": "token_registry_hash_mismatch",
            "registryVersion": str(payload.get("registryVersion") or ""),
            "registryPath": str(_unified_token_registry_path()),
            "canonicalHash": str(payload.get("_canonicalHash") or ""),
            "expectedCanonicalHash": str(payload.get("_expectedCanonicalHash") or ""),
        }

    now = _utc_now()
    by_address: dict[str, dict[str, Any]] = {}
    expired_by_address: dict[str, dict[str, Any]] = {}
    for item in payload.get("tokens") or []:
        if not isinstance(item, dict):
            continue
        token = _normalize_pair_address(item.get("token") or item.get("address"))
        if not token:
            continue
        expires_at = _parse_utc_datetime(item.get("expiresAt") or item.get("expires_at"))
        if expires_at and expires_at <= now:
            expired_by_address[token] = item
            continue
        by_address[token] = item

    route_tokens = sorted(
        {
            _normalize_pair_address(token)
            for trade in trades
            if isinstance(trade, dict)
            for token in (trade.get("tokenX"), trade.get("tokenY"))
            if _normalize_pair_address(token)
        }
    )
    missing = [token for token in route_tokens if token not in by_address]
    if missing:
        expired = [token for token in missing if token in expired_by_address]
        if expired:
            return None, {
                "source": "registry",
                "available": False,
                "reason": "token_registry_metadata_expired",
                "expiredTokens": expired,
                "missingTokens": missing,
                "registryVersion": str(payload.get("registryVersion") or ""),
                "registryPath": str(_unified_token_registry_path()),
                "automaticDowngrade": "route_group_diagnostic_only_until_registry_refresh",
            }
        return None, {
            "source": "registry",
            "available": False,
            "reason": "token_registry_metadata_missing",
            "missingTokens": missing,
            "registryVersion": str(payload.get("registryVersion") or ""),
            "registryPath": str(_unified_token_registry_path()),
        }

    token_risks: list[dict[str, Any]] = []
    for token in route_tokens:
        source = by_address[token]
        token_risks.append(
            {
                "token": token,
                "symbol": str(source.get("symbol") or ""),
                "decimals": source.get("decimals"),
                "approveBehavior": str(source.get("approveBehavior") or source.get("approve_behavior") or ""),
                "transferBehavior": str(source.get("transferBehavior") or source.get("transfer_behavior") or ""),
                "aaveBorrowEnabled": source.get("aaveBorrowEnabled", source.get("aave_borrow_enabled")),
                "poolCacheVerifiedAt": str(
                    source.get("poolCacheVerifiedAt") or source.get("pool_cache_verified_at") or ""
                ),
                "role": str(source.get("role") or ""),
                "reentrancyRisk": str(
                    source.get("reentrancyRisk") or source.get("reentrancy_risk") or "none"
                ).lower(),
                "decimalTruncationRisk": str(
                    source.get("decimalTruncationRisk")
                    or source.get("decimal_truncation_risk")
                    or "none"
                ).lower(),
                "reviewedAt": str(source.get("reviewedAt") or source.get("reviewed_at") or ""),
                "expiresAt": str(source.get("expiresAt") or source.get("expires_at") or ""),
            }
        )
    return token_risks, {
        "source": "registry",
        "available": True,
        "registryVersion": str(payload.get("registryVersion") or ""),
        "registryPath": str(_unified_token_registry_path()),
        "chainId": str(registry_chain_id),
        "canonicalHash": str(payload.get("_canonicalHash") or ""),
        "integrityRequired": bool(payload.get("_integrityRequired")),
        "integrityOk": bool(payload.get("_integrityOk")),
    }


def _attach_registry_token_risk_metadata(
    groups: list[dict[str, Any]],
    *,
    network: str,
    registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    for group in groups:
        if not isinstance(group, dict) or group.get("tokenRisks") or group.get("token_risks"):
            continue
        trades = group.get("trades") if isinstance(group.get("trades"), list) else []
        token_risks, report = _registry_token_risks_for_trades(
            trades,
            network=network,
            registry=registry,
        )
        group["tokenRiskRegistry"] = report
        if token_risks:
            group["tokenRisks"] = token_risks
    return groups
