from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.market_config import (
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    liquidation_market_config_for,
)
from core.contracts import source_metadata
from core.robinhood_chain_health import probe_robinhood_rpc
from core.robinhood_readiness import robinhood_deployment_status
from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from db.storage_chain_markets import load_current_dex_arbitrage_candidates


SRC_ROOT = Path(__file__).resolve().parents[1]
ROBINHOOD_CANDIDATE_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "robinhood_dex_candidates.json"
ROBINHOOD_EVIDENCE_ROOT = SRC_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"


def _read_candidate_cache(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _candidate_view(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    return {
        "routeKey": str(row.get("route_key") or row.get("routeKey") or ""),
        "routeSymbols": list(row.get("route_symbols") or row.get("routeSymbols") or []),
        "poolAddresses": list(row.get("pool_addresses") or row.get("poolAddresses") or []),
        "status": str(row.get("status") or "observed"),
        "observedBlock": row.get("observed_block") or row.get("observedBlock"),
        "updatedAt": row.get("updated_at") or row.get("updatedAt"),
        "expiresAt": row.get("expires_at") or row.get("expiresAt"),
        "evidence": {
            "factory": evidence.get("factory"),
            "poolCodeVerified": bool(evidence.get("poolCodeVerified")),
            "eventVerified": bool(evidence.get("eventVerified")),
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


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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


def _robinhood_market(market_id: str, chain_id: int):
    market = liquidation_market_config_for(
        market_id=market_id,
        chain_id=chain_id,
        feature="dex_arbitrage",
    )
    if market.market_id != DEFAULT_ROBINHOOD_DEX_MARKET_ID or market.network != "robinhood":
        raise ValueError("Robinhood DEX observation requires the robinhood-dex-shadow market scope")
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
    evidence_available = readiness_payload["evidence"].get("source") == "evidence_package"
    observation_status = "candidates_available" if views else "awaiting_discovery_evidence"
    observation_message = (
        "Robinhood DEX observation candidates are available."
        if views
        else "RPC is healthy, but no verified Robinhood DEX discovery evidence or candidate cache is available."
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
            "candidateCache": candidate_cache_state,
            "evidenceAvailable": evidence_available,
            "evidenceSource": readiness_payload["evidence"].get("source"),
            "nextAction": (
                "Run the Robinhood DEX discovery evidence workflow with a verified factory address, then write the candidate cache."
                if not views
                else "Review candidate evidence and freshness before using it for observation."
            ),
        },
        "candidates": views,
        "summary": {
            "candidateCount": len(views),
            "executionEnabled": False,
            "broadcastEnabled": False,
            "mode": "shadow_observation_only",
            "deploymentLevel": readiness["deploymentLevel"],
        },
    }
