from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.market_config import (
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    liquidation_market_config_for,
)
from core.market_identity import canonical_market_id_from_legacy
from core.contracts import source_metadata
from dex.intent_trade.chains.robinhood.chain_health import probe_robinhood_rpc
from dex.intent_trade.chains.robinhood.readiness import robinhood_deployment_status
from core.db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from core.db.storage_chain_markets import load_current_dex_arbitrage_candidates


SRC_ROOT = Path(__file__).resolve().parents[2] / "src_bot"
ROBINHOOD_CANDIDATE_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "robinhood_dex_candidates.json"
ROBINHOOD_EVIDENCE_ROOT = SRC_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"
ROBINHOOD_MARKET_SNAPSHOT_PATH = SRC_ROOT / "runtime" / "evidence" / "robinhood" / "market_snapshot" / "latest.json"


def _read_candidate_cache(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


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
    """Only promote candidates backed by verified on-chain pool evidence."""

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


def _pool_has_verified_evidence(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict) or str(row.get("status") or "") != "observable":
        return False
    if not str(row.get("pool") or "").strip():
        return False
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    if not (bool(evidence.get("eventVerified")) or bool(evidence.get("factoryQueryVerified"))):
        return False
    if not bool(evidence.get("poolCodeVerified")):
        return False
    if evidence.get("poolStateVerified") is False:
        discovery_method = str(evidence.get("discoveryMethod") or "")
        if discovery_method != "factory_PoolCreated_event_scan":
            return False
    liquidity = row.get("liquidity")
    return liquidity in (None, "") or _chain_id_value(liquidity) > 0


def _pool_view(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    return {
        "address": str(row.get("pool") or ""),
        "token0": row.get("token0"),
        "token1": row.get("token1"),
        "token0Symbol": row.get("token0Symbol"),
        "token1Symbol": row.get("token1Symbol"),
        "symbolPair": row.get("symbolPair"),
        "fee": row.get("fee"),
        "tickSpacing": row.get("tickSpacing"),
        "liquidity": row.get("liquidity"),
        "observedBlock": evidence.get("blockNumber") or row.get("observedBlock"),
        "status": str(row.get("status") or "observable"),
        "evidence": {
            "factory": row.get("factory") or evidence.get("factory"),
            "factoryCodeVerified": bool(evidence.get("factoryCodeVerified")),
            "factoryQueryVerified": bool(evidence.get("factoryQueryVerified")),
            "eventVerified": bool(evidence.get("eventVerified")),
            "poolCodeVerified": bool(evidence.get("poolCodeVerified")),
            "poolStateVerified": bool(evidence.get("poolStateVerified")),
            "discoveryMethod": evidence.get("discoveryMethod"),
            "sourceUrl": evidence.get("sourceUrl"),
        },
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_latest_robinhood_market_snapshot(
    path: Path | None = None,
) -> dict[str, Any] | None:
    """Load the latest read-only market snapshot without probing or writing."""

    path = path or ROBINHOOD_MARKET_SNAPSHOT_PATH
    payload = _read_json_object(path)
    if not payload:
        return None
    if str(payload.get("network") or "").lower() != "robinhood":
        return None
    try:
        if int(payload.get("chainId") or 0) != DEFAULT_ROBINHOOD_CHAIN_ID:
            return None
    except (TypeError, ValueError):
        return None
    return payload


def _chain_id_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _file_freshness(path: Path) -> dict[str, Any]:
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        return {"status": "missing", "observedAt": None, "ageSeconds": None}
    from datetime import datetime, timezone

    observed_at = datetime.fromtimestamp(modified_at, tz=timezone.utc)
    age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - modified_at)
    return {
        "status": "present",
        "observedAt": observed_at.isoformat(),
        "ageSeconds": round(age_seconds, 3),
    }


def load_latest_robinhood_evidence(
    evidence_root: Path = ROBINHOOD_EVIDENCE_ROOT,
) -> dict[str, Any]:
    """Load only target-chain evidence from the newest evidence directory."""

    try:
        directories = sorted(
            (path for path in evidence_root.iterdir() if path.is_dir()),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    except OSError:
        directories = []
    for directory in directories:
        discovery = _read_json_object(directory / "factory_events.json")
        quote = _read_json_object(directory / "quote_smoke_report.json")
        if not discovery and not quote:
            continue
        if discovery and (
            str(discovery.get("network") or "").lower() != "robinhood"
            or _chain_id_value(discovery.get("chainId")) != DEFAULT_ROBINHOOD_CHAIN_ID
        ):
            continue
        return {
            "root": str(evidence_root),
            "directory": str(directory),
            "discoveryReport": discovery,
            "quoteReport": quote,
            "source": "evidence_package",
            "dataSource": source_metadata("evidence_package"),
        }
    return {
        "root": str(evidence_root),
        "directory": "",
        "discoveryReport": None,
        "quoteReport": None,
        "source": "none",
        "dataSource": source_metadata("builtin_fallback", fallback_reason="evidence_package_missing"),
    }


def load_latest_robinhood_pool_rows(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    directory = Path(str(evidence.get("directory") or ""))
    payload = _read_json_object(directory / "pool_static_table.json") if directory else None
    rows = payload.get("pools") if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict) and _pool_has_verified_evidence(row)]


def _robinhood_market(market_id: str, chain_id: int):
    market = liquidation_market_config_for(
        market_id=market_id,
        chain_id=chain_id,
        feature="dex_arbitrage",
    )
    robinhood_market_id = canonical_market_id_from_legacy(DEFAULT_ROBINHOOD_DEX_MARKET_ID) or DEFAULT_ROBINHOOD_DEX_MARKET_ID
    if market.market_id != robinhood_market_id or market.network != "robinhood":
        raise ValueError("Robinhood DEX observation requires the dex-arbitrage-4663-uniswap-v3 market scope")
    return market


def robinhood_dex_readiness_payload(
    *,
    market_id: str = DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    chain_id: int = DEFAULT_ROBINHOOD_CHAIN_ID,
    evidence_root: Path = ROBINHOOD_EVIDENCE_ROOT,
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
    evidence = load_latest_robinhood_evidence(evidence_root)
    readiness = robinhood_deployment_status(
        market=market.as_dict(),
        rpc_status=resolved_rpc_status,
        discovery_report=evidence["discoveryReport"],
        quote_report=evidence["quoteReport"],
    )
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
    candidate_cache_path: Path = ROBINHOOD_CANDIDATE_CACHE_PATH,
    evidence_root: Path = ROBINHOOD_EVIDENCE_ROOT,
    run_rpc_probe: bool = True,
) -> dict[str, Any]:
    market = _robinhood_market(market_id, chain_id)

    candidates: list[dict[str, Any]] = []
    source = "runtime_cache"
    legacy_source = "cache"
    database_error = None
    fallback_reason = None
    if database_url and not database_unavailable_reason(database_url):
        try:
            candidates = load_current_dex_arbitrage_candidates(
                database_url,
                market_id=market.market_id,
                chain_id=market.chain_id,
            )
            source = "database"
            legacy_source = "database"
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
            database_error = str(exc)
            fallback_reason = "database_unavailable"
    if not candidates:
        candidates = _read_candidate_cache(candidate_cache_path)
        if candidates:
            source = "runtime_cache"
            legacy_source = "cache"
            fallback_reason = fallback_reason or ("database_empty" if database_url else None)
        else:
            source = "builtin_fallback"
            legacy_source = "empty"
            fallback_reason = fallback_reason or "candidate_cache_empty"

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
        market_id=market_id,
        chain_id=chain_id,
        evidence_root=evidence_root,
        rpc_status=rpc_status,
        run_rpc_probe=False,
    )
    readiness = readiness_payload["deploymentStatus"]
    candidate_cache_state = _file_freshness(candidate_cache_path)
    pool_rows = load_latest_robinhood_pool_rows(readiness_payload["evidence"])
    pool_views = [_pool_view(row) for row in pool_rows]
    raw_market_snapshot = load_latest_robinhood_market_snapshot()
    # A quote is downstream of verified pool discovery. Do not publish P/L
    # until at least one evidence-validated pool exists.
    market_snapshot = raw_market_snapshot if pool_views else None
    market_snapshot_state = _file_freshness(ROBINHOOD_MARKET_SNAPSHOT_PATH)
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
            (market_snapshot_state.get("ageSeconds") or 0) > 1800
            or (snapshot_block_lag is not None and snapshot_block_lag > 1000)
        )
    )
    evidence_available = readiness_payload["evidence"].get("source") == "evidence_package"
    observation_status = (
        "pools_available"
        if pool_views
        else "candidates_available"
        if views
        else "awaiting_discovery_evidence"
    )
    observation_message = (
        "Robinhood DEX verified pool records are available."
        if pool_views
        else "A candidate route cache exists, but pool evidence is not available for the pool page."
        if views
        else "RPC is healthy, but no verified Robinhood DEX pool record is available."
    )
    return {
        "market": market.as_dict(),
        "chainStatus": rpc_status,
        "evidence": readiness_payload["evidence"],
        "deploymentStatus": readiness,
        # Keep the old display value for existing consumers; dataSource is the
        # canonical contract used by new callers and architecture checks.
        "candidateSource": legacy_source,
        "dataSource": source_metadata(
            source,
            freshness=_file_freshness(candidate_cache_path) if source == "runtime_cache" else None,
            fallback_reason=fallback_reason,
        ),
        "databaseError": database_error,
        "candidateCachePath": str(candidate_cache_path),
        "observationStatus": observation_status,
        "observationMessage": observation_message,
        "observationDiagnostics": {
            "rpcHealthy": bool(rpc_status.get("ok")),
            "candidateCount": len(views),
            "poolCount": len(pool_views),
            "rawCandidateCount": raw_candidate_count,
            "rejectedCandidateCount": max(0, raw_candidate_count - len(views)),
            "candidateCache": candidate_cache_state,
            "evidenceAvailable": evidence_available,
            "evidenceSource": readiness_payload["evidence"].get("source"),
            "marketSnapshot": market_snapshot_state,
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
                "Validate the factory, read pool addresses, and verify pool code, tokens, fees, and liquidity evidence."
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
            "poolSource": readiness_payload["evidence"].get("source"),
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
    candidate_cache_path: Path = ROBINHOOD_CANDIDATE_CACHE_PATH,
    evidence_root: Path = ROBINHOOD_EVIDENCE_ROOT,
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
        candidate_cache_path=candidate_cache_path,
        evidence_root=evidence_root,
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
            "Verified Robinhood DEX pools are available."
            if pools
            else "No verified Robinhood DEX pools are available yet."
        ),
        "pools": pools,
        "summary": {
            "poolCount": len(pools),
            "poolSource": evidence.get("source"),
            "executionEnabled": False,
            "broadcastEnabled": False,
            "mode": "shadow_observation_only",
        },
    }
