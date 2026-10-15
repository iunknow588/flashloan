from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from core.contracts import source_metadata
from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import load_chain_tokens
from core.db.storage_chain_markets import load_current_dex_arbitrage_candidates
from core.db.storage_v4_exchange import (
    load_v4_exchange_pool_manager,
    load_v4_exchange_pools,
    load_v4_pool_liquidity,
    load_v4_pool_liquidity_refreshed_at,
)
from core.kv_rows import collection_key, get_row
from core.market_config import (
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    liquidation_market_config_for,
)
from core.market_identity import canonical_market_id_from_legacy
from dex.intent_trade.chains.robinhood.chain_health import probe_robinhood_rpc
from dex.intent_trade.chains.robinhood.readiness import robinhood_deployment_status


SRC_ROOT = Path(__file__).resolve().parents[2] / "app_runtime"
ROBINHOOD_PROTOCOL = "uniswap_v4"
ROBINHOOD_MARKET_SNAPSHOT_KV_COLLECTION = "robinhood_market_snapshot"


def _chain_id_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: object) -> str:
    return str(value or "").strip()


def _tri_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _text(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return None


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _database_path(database_url: str | None) -> str:
    return str(absolute_database_path(database_url))


def _database_freshness(database_url: str | None) -> dict[str, Any]:
    try:
        stat = absolute_database_path(database_url).stat()
    except OSError:
        return {"status": "missing", "observedAt": None, "ageSeconds": None}
    observed_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - stat.st_mtime)
    return {
        "status": "present",
        "observedAt": observed_at.isoformat(),
        "ageSeconds": round(age_seconds, 3),
    }


def _candidate_view(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    pool_evidence = evidence.get("pools") if isinstance(evidence.get("pools"), list) else []
    pool_evidence = [item for item in pool_evidence if isinstance(item, dict)]
    evidence_rows = pool_evidence or [evidence]
    discovery_methods = sorted(
        {
            str(item.get("discoveryMethod") or "").strip()
            for item in evidence_rows
            if str(item.get("discoveryMethod") or "").strip()
        }
    )
    return {
        "routeKey": str(row.get("route_key") or row.get("routeKey") or ""),
        "routeSymbols": list(row.get("route_symbols") or row.get("routeSymbols") or []),
        "poolAddresses": list(row.get("pool_addresses") or row.get("poolAddresses") or []),
        "status": str(row.get("status") or "observed"),
        "observedBlock": row.get("observed_block") or row.get("observedBlock"),
        "updatedAt": row.get("updated_at") or row.get("updatedAt"),
        "expiresAt": row.get("expires_at") or row.get("expiresAt"),
        "evidence": {
            "factory": evidence.get("factory") or next(
                (item.get("factory") for item in evidence_rows if item.get("factory")),
                None,
            ),
            "poolCodeVerified": bool(evidence_rows) and all(bool(item.get("poolCodeVerified")) for item in evidence_rows),
            "eventVerified": bool(evidence_rows) and all(bool(item.get("eventVerified")) for item in evidence_rows),
            "factoryQueryVerified": bool(evidence_rows) and all(bool(item.get("factoryQueryVerified")) for item in evidence_rows),
            "poolStateVerified": bool(evidence_rows) and all(item.get("poolStateVerified") is not False for item in evidence_rows),
            "discoveryMethod": ", ".join(discovery_methods) or None,
            "quoteStatus": evidence.get("quoteStatus") or "not_started",
        },
        "executionEnabled": False,
        "broadcastEnabled": False,
        "blockedReasons": [
            "robinhood_executor_unverified",
            "robinhood_flashloan_provider_unverified",
            "robinhood_live_quote_unverified",
            "robinhood_fork_simulation_unverified",
        ],
    }


def _candidate_has_verified_pool_evidence(row: dict[str, Any]) -> bool:
    """Only promote candidates backed by verified pool evidence from SQLite."""

    pool_addresses = row.get("pool_addresses") or row.get("poolAddresses") or []
    if not isinstance(pool_addresses, list) or not pool_addresses or any(not str(address).strip() for address in pool_addresses):
        return False
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    pool_evidence = evidence.get("pools")
    if not isinstance(pool_evidence, list) or not pool_evidence:
        pool_evidence = [evidence]
    if len(pool_evidence) < len(pool_addresses):
        return False
    return all(
        isinstance(item, dict)
        and (bool(item.get("eventVerified")) or bool(item.get("factoryQueryVerified")))
        and bool(item.get("poolCodeVerified"))
        and (item.get("poolStateVerified") is not False)
        for item in pool_evidence[: len(pool_addresses)]
    )


def _pool_view(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    return {
        "address": str(row.get("pool") or ""),
        "pool": str(row.get("pool") or ""),
        "poolId": str(row.get("pool") or ""),
        "token0": row.get("token0"),
        "token1": row.get("token1"),
        "token0Symbol": row.get("token0Symbol"),
        "token1Symbol": row.get("token1Symbol"),
        "symbolPair": row.get("symbolPair"),
        "fee": row.get("fee"),
        "tickSpacing": row.get("tickSpacing"),
        "hooks": row.get("hooks"),
        "liquidity": row.get("liquidity"),
        "hasLiquidity": row.get("hasLiquidity"),
        "liquidityIndex": row.get("liquidityIndex"),
        "estimatedTvlUsd": row.get("estimatedTvlUsd"),
        "depthStatus": row.get("depthStatus") or "",
        "observedBlock": row.get("observedBlock"),
        "status": str(row.get("status") or "observable"),
        "source": "sqlite_db",
        "evidence": {
            "factory": evidence.get("factory"),
            "factoryCodeVerified": bool(evidence.get("factoryCodeVerified")),
            "factoryQueryVerified": bool(evidence.get("factoryQueryVerified")),
            "eventVerified": bool(evidence.get("eventVerified")),
            "poolCodeVerified": bool(evidence.get("poolCodeVerified")),
            "poolStateVerified": bool(evidence.get("poolStateVerified")),
            "discoveryMethod": evidence.get("discoveryMethod"),
            "sourceUrl": evidence.get("sourceUrl"),
        },
    }


def load_robinhood_sqlite_pool_rows(
    database_url: str | None,
    *,
    network: str,
    chain_id: int,
) -> list[dict[str, Any]]:
    """Load V4 pools and liquidity entirely from SQLite tables."""
    db = _database_path(database_url)
    pool_rows = load_v4_exchange_pools(
        db,
        network=network,
        chain_id=int(chain_id),
        protocol=ROBINHOOD_PROTOCOL,
    )
    liquidity_rows = load_v4_pool_liquidity(
        db,
        network=network,
        chain_id=int(chain_id),
        protocol=ROBINHOOD_PROTOCOL,
    )
    liquidity_by_id = {
        _text(row.get("pool_id")).lower(): row
        for row in liquidity_rows
        if _text(row.get("pool_id"))
    }
    token_rows = load_chain_tokens(
        db,
        network=network,
        chain_id=int(chain_id),
    )
    symbol_by_address = {
        _text(row.get("token_address")).lower(): _text(row.get("token_symbol"))
        for row in token_rows
        if _text(row.get("token_address"))
    }
    rows: list[dict[str, Any]] = []
    for pool in pool_rows:
        pool_id = _text(pool.get("pool_id"))
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if not pool_id or not token0 or not token1:
            continue
        liquidity = liquidity_by_id.get(pool_id.lower(), {})
        has_liquidity = _tri_bool(liquidity.get("has_liquidity"))
        pool_key = _json_loads(pool.get("pool_key_json") or {})
        observed_block = liquidity.get("liquidity_block_number")
        rows.append(
            {
                "pool": pool_id,
                "poolId": pool_id,
                "poolManager": _text(pool.get("pool_manager")),
                "poolKey": pool_key if isinstance(pool_key, dict) else {},
                "token0": token0,
                "token1": token1,
                "token0Symbol": symbol_by_address.get(token0.lower(), ""),
                "token1Symbol": symbol_by_address.get(token1.lower(), ""),
                "symbolPair": (
                    f"{symbol_by_address.get(token0.lower(), token0)}/"
                    f"{symbol_by_address.get(token1.lower(), token1)}"
                ),
                "fee": pool.get("fee"),
                "tickSpacing": pool.get("tick_spacing"),
                "hooks": _text(pool.get("hooks")),
                "liquidity": liquidity.get("liquidity") or "",
                "hasLiquidity": has_liquidity,
                "liquidityIndex": liquidity.get("liquidity_index"),
                "estimatedTvlUsd": liquidity.get("estimated_tvl_usd"),
                "depthStatus": liquidity.get("depth_status") or "",
                "sqrtPriceX96": liquidity.get("sqrt_price_x96") or "",
                "tick": liquidity.get("tick"),
                "observedBlock": observed_block,
                "status": (
                    "observable"
                    if has_liquidity is True
                    else "no_liquidity"
                    if has_liquidity is False
                    else "initialized_pool"
                ),
                "evidence": {
                    "factory": "",
                    "factoryCodeVerified": True,
                    "factoryQueryVerified": True,
                    "eventVerified": True,
                    "poolCodeVerified": True,
                    "poolStateVerified": has_liquidity is not None,
                    "discoveryMethod": "sqlite_v4_import",
                    "blockNumber": observed_block,
                    "sourceUrl": None,
                },
            }
        )
    return rows


def load_robinhood_sqlite_market_snapshot(
    database_url: str | None,
    *,
    network: str,
    chain_id: int,
) -> dict[str, Any] | None:
    """Read the latest market snapshot from the SQLite KV store only."""
    db = _database_path(database_url)
    keys = (
        collection_key(network, chain_id),
        "latest",
        f"{network}-{chain_id}",
    )
    for key in keys:
        payload = get_row(db, ROBINHOOD_MARKET_SNAPSHOT_KV_COLLECTION, key)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("network") or "").lower() != network:
            continue
        try:
            if int(payload.get("chainId") or 0) != int(chain_id):
                continue
        except (TypeError, ValueError):
            continue
        return payload
    return None


def _robinhood_market(market_id: str, chain_id: int):
    market = liquidation_market_config_for(
        market_id=market_id,
        chain_id=chain_id,
        feature="dex_arbitrage",
    )
    robinhood_market_id = canonical_market_id_from_legacy(DEFAULT_ROBINHOOD_DEX_MARKET_ID) or DEFAULT_ROBINHOOD_DEX_MARKET_ID
    if market.market_id != robinhood_market_id or market.network != "robinhood":
        raise ValueError("Robinhood DEX observation requires the dex-arbitrage-4663-uniswap-v4 market scope")
    return market


def _sqlite_discovery_report(
    database_url: str | None,
    market: Any,
    pools: list[dict[str, Any]],
) -> dict[str, Any]:
    db = _database_path(database_url)
    manager_rows = load_v4_exchange_pool_manager(
        db,
        network=market.network,
        chain_id=market.chain_id,
        protocol=ROBINHOOD_PROTOCOL,
    )
    refreshed_at = load_v4_pool_liquidity_refreshed_at(
        db,
        network=market.network,
        chain_id=market.chain_id,
        protocol=ROBINHOOD_PROTOCOL,
    )
    return {
        "schemaVersion": "robinhood-v4-sqlite-discovery-v1",
        "network": market.network,
        "chainId": market.chain_id,
        "poolManager": _text(manager_rows.get("pool_manager")),
        "poolCount": len(pools),
        "factoryCodeVerified": bool(pools),
        "verified": bool(pools),
        "generatedAt": refreshed_at,
        "source": "sqlite_db",
    }


def robinhood_dex_readiness_payload(
    *,
    database_url: str | None = None,
    market_id: str = DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    chain_id: int = DEFAULT_ROBINHOOD_CHAIN_ID,
    rpc_status: dict[str, Any] | None = None,
    run_rpc_probe: bool = True,
) -> dict[str, Any]:
    market = _robinhood_market(market_id, chain_id)
    resolved_rpc_status: dict[str, Any] = rpc_status or {
        "ok": False,
        "network": market.network,
        "expectedChainId": market.chain_id,
        "rpcUrl": market.rpc_urls[0] if market.rpc_urls else "",
        "checks": [],
        "executionMode": "readonly_probe_not_run",
    }
    if rpc_status is None and run_rpc_probe:
        try:
            resolved_rpc_status = probe_robinhood_rpc(resolved_rpc_status["rpcUrl"] or None)
        except Exception as exc:
            resolved_rpc_status["error"] = str(exc)
    pools = load_robinhood_sqlite_pool_rows(
        database_url,
        network=market.network,
        chain_id=market.chain_id,
    )
    discovery_report = _sqlite_discovery_report(database_url, market, pools)
    readiness = robinhood_deployment_status(
        market=market.as_dict(),
        rpc_status=resolved_rpc_status,
        discovery_report=discovery_report,
        quote_report=None,
    )
    evidence = {
        "root": "",
        "directory": "",
        "discoveryReport": discovery_report,
        "quoteReport": None,
        "source": "sqlite_db",
        "dataSource": source_metadata("database"),
    }
    return {
        "market": market.as_dict(),
        "chainStatus": resolved_rpc_status,
        "evidence": evidence,
        "deploymentStatus": readiness,
        "executionEnabled": False,
        "broadcastEnabled": False,
    }


def robinhood_dex_observation_payload(
    *,
    database_url: str | None = None,
    market_id: str = DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    chain_id: int = DEFAULT_ROBINHOOD_CHAIN_ID,
    run_rpc_probe: bool = True,
) -> dict[str, Any]:
    market = _robinhood_market(market_id, chain_id)
    db = _database_path(database_url)

    candidates: list[dict[str, Any]] = []
    database_error = None
    fallback_reason = None
    try:
        candidates = load_current_dex_arbitrage_candidates(
            db,
            market_id=market.market_id,
        )
        candidates = [
            row
            for row in candidates
            if _chain_id_value(row.get("chain_id") or row.get("chainId")) == market.chain_id
        ]
    except Exception as exc:
        database_error = str(exc)
        fallback_reason = "database_unavailable"
    if not candidates and database_error is None:
        fallback_reason = "database_empty"

    rpc_status: dict[str, Any] = {
        "ok": False,
        "network": market.network,
        "expectedChainId": market.chain_id,
        "rpcUrl": market.rpc_urls[0] if market.rpc_urls else "",
        "checks": [],
        "executionMode": "readonly_probe_not_run",
    }
    if run_rpc_probe:
        try:
            rpc_status = probe_robinhood_rpc(rpc_status["rpcUrl"] or None)
        except Exception as exc:
            rpc_status["error"] = str(exc)

    raw_candidate_count = len(candidates)
    candidates = [row for row in candidates if _candidate_has_verified_pool_evidence(row)]
    views = [_candidate_view(row) for row in candidates]
    readiness_payload = robinhood_dex_readiness_payload(
        database_url=database_url,
        market_id=market_id,
        chain_id=chain_id,
        rpc_status=rpc_status,
        run_rpc_probe=False,
    )
    readiness = readiness_payload["deploymentStatus"]
    pool_rows = load_robinhood_sqlite_pool_rows(
        database_url,
        network=market.network,
        chain_id=market.chain_id,
    )
    pool_views = [_pool_view(row) for row in pool_rows]
    liquid_pool_count = sum(1 for row in pool_rows if row.get("hasLiquidity") is True)
    database_state = _database_freshness(database_url)
    raw_market_snapshot = load_robinhood_sqlite_market_snapshot(
        database_url,
        network=market.network,
        chain_id=market.chain_id,
    )
    market_snapshot = raw_market_snapshot if pool_views else None
    latest_block = next(
        (
            _chain_id_value(check.get("value"))
            for check in (rpc_status.get("checks") or [])
            if isinstance(check, dict) and check.get("name") == "eth_blockNumber"
        ),
        0,
    )
    snapshot_block = _chain_id_value(market_snapshot.get("blockNumber")) if market_snapshot else 0
    snapshot_block_lag = max(0, latest_block - snapshot_block) if latest_block and snapshot_block else None
    snapshot_stale = bool(
        market_snapshot
        and (
            (database_state.get("ageSeconds") or 0) > 1800
            or (snapshot_block_lag is not None and snapshot_block_lag > 1000)
        )
    )
    evidence_available = bool(
        pool_rows
        and readiness_payload["evidence"].get("source") == "sqlite_db"
    )
    observation_status = (
        "pools_available"
        if pool_views
        else "candidates_available"
        if views
        else "awaiting_discovery_evidence"
    )
    observation_message = (
        "Robinhood DEX pool records are available from SQLite."
        if pool_views
        else "Candidate routes exist in SQLite, but no pool record is available."
        if views
        else "RPC is healthy, but no Robinhood DEX pool record is available in SQLite."
    )
    return {
        "market": market.as_dict(),
        "chainStatus": rpc_status,
        "evidence": readiness_payload["evidence"],
        "deploymentStatus": readiness,
        "candidateSource": "database",
        "dataSource": source_metadata(
            "database",
            freshness=database_state,
            fallback_reason=fallback_reason,
        ),
        "databaseError": database_error,
        "databasePath": db,
        "observationStatus": observation_status,
        "observationMessage": observation_message,
        "observationDiagnostics": {
            "rpcHealthy": bool(rpc_status.get("ok")),
            "candidateCount": len(views),
            "poolCount": len(pool_views),
            "liquidPoolCount": liquid_pool_count,
            "rawCandidateCount": raw_candidate_count,
            "rejectedCandidateCount": max(0, raw_candidate_count - len(views)),
            "database": database_state,
            "evidenceAvailable": evidence_available,
            "evidenceSource": readiness_payload["evidence"].get("source"),
            "marketSnapshot": bool(raw_market_snapshot),
            "marketSnapshotSuppressed": bool(raw_market_snapshot and not pool_views),
            "marketSnapshotSuppressionReason": (
                "verified_candidate_pool_required"
                if raw_market_snapshot and not pool_views
                else None
            ),
            "marketSnapshotBlock": market_snapshot.get("blockNumber") if market_snapshot else None,
            "marketSnapshotBlockLag": snapshot_block_lag,
            "marketSnapshotStale": snapshot_stale,
            "bestRouteGrossProfit": (
                (market_snapshot.get("bestRoute") or {}).get("grossProfit")
                if market_snapshot
                else None
            ),
            "nextAction": (
                "Refresh the V4 pool/liquidity tables through the DB-only import tool."
                if not pool_views
                else "Review pool addresses, tokens, fees, liquidity, and evidence blocks."
            ),
        },
        "candidates": views,
        "pools": pool_views,
        "marketSnapshot": market_snapshot,
        "summary": {
            "candidateCount": len(views),
            "poolCount": len(pool_views),
            "liquidPoolCount": liquid_pool_count,
            "poolSource": "sqlite_db",
            "marketSnapshotAvailable": bool(market_snapshot),
            "executionEnabled": False,
            "broadcastEnabled": False,
            "mode": "shadow_observation_only",
            "deploymentLevel": readiness["deploymentLevel"],
        },
    }


def robinhood_dex_pool_observation_payload(
    *,
    database_url: str | None = None,
    market_id: str = DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    chain_id: int = DEFAULT_ROBINHOOD_CHAIN_ID,
    run_rpc_probe: bool = True,
) -> dict[str, Any]:
    """Return the pool-only contract used by the Robinhood candidate-pool page.

    Route candidates and market snapshots belong to separate observation
    surfaces. Keeping them out of this response prevents a stale quote or a
    route-level loss from being mistaken for pool-level data.
    """

    payload = robinhood_dex_observation_payload(
        database_url=database_url,
        market_id=market_id,
        chain_id=chain_id,
        run_rpc_probe=run_rpc_probe,
    )
    pools = payload.get("pools") if isinstance(payload.get("pools"), list) else []
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    return {
        "market": payload.get("market"),
        "chainStatus": payload.get("chainStatus"),
        "evidence": {
            "source": evidence.get("source"),
            "directory": evidence.get("directory"),
            "dataSource": evidence.get("dataSource"),
        },
        "observationStatus": "pools_available" if pools else "awaiting_discovery_evidence",
        "observationMessage": (
            "Robinhood DEX pools are available from SQLite."
            if pools
            else "No Robinhood DEX pools are available in SQLite yet."
        ),
        "pools": pools,
        "summary": {
            "poolCount": len(pools),
            "poolSource": "sqlite_db",
            "executionEnabled": False,
            "broadcastEnabled": False,
            "mode": "shadow_observation_only",
        },
    }


__all__ = [
    "load_robinhood_sqlite_market_snapshot",
    "load_robinhood_sqlite_pool_rows",
    "robinhood_dex_observation_payload",
    "robinhood_dex_pool_observation_payload",
    "robinhood_dex_readiness_payload",
]
