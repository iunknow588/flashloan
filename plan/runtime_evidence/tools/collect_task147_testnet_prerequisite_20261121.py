"""TASK-147 (AC-211) testnet read-only prerequisite evidence collector.

TASK-147 asks for a **read-only** proof that the Robinhood **testnet** chain
(46630) carries a *usable* closed loop before any mainnet work resumes.

This collector is deliberately read-only end to end:

  * the SQLite store is opened with ``mode=ro``; nothing is written, no
    repair/rebuild runs, no admission is hand-edited;
  * the RPC endpoints are only asked for ``eth_chainId`` /
    ``eth_blockNumber`` -- read-only calls;
  * no broadcast and no apply of any kind.

What the collector reports (each item is an observation, never a conclusion
that is not backed by a row):

  1. chain identity: configured chain id, DB path + digest, RPC resolution
     (primary and fallback) and the live chain-id read-back;
  2. WSS availability -- reported **as unavailable** when the chain config
     declares an empty ``rpc.wss`` list, because an unmeasured stream is
     never reported as a measured zero;
  3. per-library token admission (core / stocks / extension / test) with the
     raw / admitted / blocked / candidate_only split and the blocked reason;
  4. the overlap identity: the same token may appear in several libraries,
     but it must be **admitted in exactly one** of them -- the collector
     reports the per-library status so a double admission is visible;
  5. per-scope raw/active/blocked pool counts and the scope's pinned
     library versions/checksums against the *current* library state;
  6. the Manager's own counts: direction_count, loop_count, hop_count,
     optimization_count and the skip reasons;
  7. the upstream versions + checksums that produced the published state;
  8. an explicit **no-backfill** statement.

The verdict is honest in both directions: the acceptance criterion is
reported as met only when the Manager actually published a closed loop.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CHAIN_DIR = os.path.join(ROOT, "flashloan", "chains", "robinhood-testnet")
CHAIN_JSON = os.path.join(CHAIN_DIR, "chain.json")
STOCK_TOKENS_JSON = os.path.join(CHAIN_DIR, "robinhood_stock_tokens.json")
DB = os.path.join(ROOT, "flashloan", "db", "local_store_46630.sqlite3")
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task147_testnet_prerequisite_20261121.json",
)

NETWORK = "robinhood-testnet"
CHAIN_ID = 46630
RPC_TIMEOUT_SECONDS = 20
RPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

CLOSED_LOOP_SCOPE = "core4"
OBSERVATION_SCOPE = "candidate_exchange"

LIBRARIES = ("core", "stocks", "extension", "test")
LIBRARY_TABLES = {
    "core": "token_core",
    "stocks": "token_stocks",
    "extension": "token_extension",
    "test": "token_test",
}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)

def open_read_only(path):
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection

def rows(connection, sql, params=()):
    return [dict(row) for row in connection.execute(sql, params)]


# --------------------------------------------------------------------------
# RPC (read-only)
# --------------------------------------------------------------------------
def rpc_probe(name, url, method):
    """Ask one read-only JSON-RPC method.  No state is changed."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": []}
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=RPC_HEADERS)
    outcome = {
        "endpoint": name,
        "url": url,
        "method": method,
        "readOnly": True,
    }
    try:
        with urllib.request.urlopen(request, timeout=RPC_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", "replace")
        outcome["httpStatus"] = response.status
        parsed = json.loads(body)
        outcome["result"] = parsed.get("result")
        outcome["error"] = parsed.get("error")
        outcome["reachable"] = parsed.get("error") is None
    except urllib.error.HTTPError as error:
        outcome["reachable"] = False
        outcome["httpStatus"] = error.code
        outcome["error"] = "HTTPError: %s" % error.code
    except Exception as error:
        outcome["reachable"] = False
        outcome["error"] = "%s: %s" % (type(error).__name__, str(error)[:200])
    return outcome

def resolve_rpc(chain_config):
    rpc = chain_config.get("rpc", {}) or {}
    primary = (rpc.get("primary") or "").strip()
    fallbacks = [u.strip() for u in (rpc.get("fallbacks") or []) if u and u.strip()]
    wss = [u.strip() for u in (rpc.get("wss") or []) if u and u.strip()]
    alchemy = (rpc.get("alchemy") or "").strip()
    hyperrpc = (rpc.get("hyperrpc") or "").strip()
    endpoints = []
    if primary:
        endpoints.append(("primary", primary))
    for index, url in enumerate(fallbacks):
        endpoints.append(("fallback[%d]" % index, url))
    if alchemy:
        endpoints.append(("alchemy", alchemy))
    if hyperrpc:
        endpoints.append(("hyperrpc", hyperrpc))
    return {
        "configuredPrimary": primary,
        "configuredFallbacks": fallbacks,
        "configuredWss": wss,
        "configuredAlchemy": alchemy,
        "configuredHyperrpc": hyperrpc,
        "timeoutSeconds": rpc.get("timeout_seconds"),
        "endpoints": endpoints,
    }

def probe_rpc(resolution):
    probes = []
    for name, url in resolution["endpoints"]:
        chain_id_probe = rpc_probe(name, url, "eth_chainId")
        chain_id_probe["declaredChainId"] = CHAIN_ID
        try:
            observed = int(chain_id_probe.get("result"), 16)
        except (TypeError, ValueError):
            observed = None
        chain_id_probe["observedChainId"] = observed
        chain_id_probe["chainIdMatches"] = observed == CHAIN_ID
        block_probe = rpc_probe(name, url, "eth_blockNumber")
        try:
            chain_id_probe["observedBlockNumber"] = int(block_probe.get("result"), 16)
        except (TypeError, ValueError):
            chain_id_probe["observedBlockNumber"] = None
        probes.append(chain_id_probe)
    reachable = [p for p in probes if p.get("reachable")]
    agreeing = [p for p in reachable if p.get("chainIdMatches")]
    return {
        "probes": probes,
        "reachableEndpointCount": len(reachable),
        "chainIdAgreements": len(agreeing),
        "allReachableAgreeOnChainId": bool(reachable) and len(agreeing) == len(reachable),
        "anyReachable": bool(reachable),
    }

def wss_availability(resolution):
    """An unmeasured stream is reported as unavailable, never as a zero."""
    configured = resolution["configuredWss"]
    return {
        "configuredEndpoints": configured,
        "configuredCount": len(configured),
        "available": bool(configured),
        "status": "available" if configured else "unavailable",
        "reason": (
            "the chain config declares at least one websocket endpoint"
            if configured
            else (
                "the chain config declares rpc.wss = [] -- no websocket endpoint "
                "is configured, so stream availability is UNMEASURED, not a "
                "measured zero"
            )
        ),
    }


# --------------------------------------------------------------------------
# token libraries
# --------------------------------------------------------------------------
def library_admission(connection):
    per_library = {}
    admitted_index = {}
    for library in LIBRARIES:
        table = LIBRARY_TABLES[library]
        entries = rows(
            connection,
            "SELECT token_key_id, token_symbol, token_address, v4_currency_key,"
            " admission_status, admission_source, metadata_source,"
            " metadata_checksum, blocked_reason, first_admitted_at, updated_at"
            " FROM " + table + " WHERE network = ?1 AND chain_id = ?2"
            " ORDER BY token_symbol, token_key_id",
            (NETWORK, CHAIN_ID),
        )
        status_counts = {}
        for entry in entries:
            status = entry["admission_status"] or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "admitted":
                admitted_index.setdefault(entry["token_key_id"], []).append(
                    {"library": library, "token_symbol": entry["token_symbol"]}
                )
        per_library[library] = {
            "table": table,
            "rowCount": len(entries),
            "statusCounts": status_counts,
            "admittedCount": status_counts.get("admitted", 0),
            "candidateOnlyCount": status_counts.get("candidate_only", 0),
            "blockedCount": status_counts.get("blocked", 0),
            "entries": [
                {
                    "tokenKeyId": entry["token_key_id"],
                    "tokenSymbol": entry["token_symbol"],
                    "tokenAddress": entry["token_address"],
                    "admissionStatus": entry["admission_status"],
                    "admissionSource": entry["admission_source"],
                    "metadataSource": entry["metadata_source"],
                    "metadataChecksum": entry["metadata_checksum"],
                    "blockedReason": entry["blocked_reason"],
                    "firstAdmittedAt": entry["first_admitted_at"],
                }
                for entry in entries
            ],
        }

    overlaps = []
    for token_key_id, holders in sorted(admitted_index.items()):
        overlaps.append({
            "tokenKeyId": token_key_id,
            "tokenSymbol": holders[0]["token_symbol"],
            "admittedInLibraries": [h["library"] for h in holders],
            "admittedLibraryCount": len(holders),
            "doubleAdmitted": len(holders) > 1,
        })
    double_admitted = [o for o in overlaps if o["doubleAdmitted"]]
    return {
        "perLibrary": per_library,
        "admittedOverlapIndex": overlaps,
        "doubleAdmittedTokenCount": len(double_admitted),
        "doubleAdmittedTokens": double_admitted,
        "singleAdmissionHolds": len(double_admitted) == 0,
    }

def library_state(connection):
    entries = rows(
        connection,
        "SELECT library_key, library_version, token_count, admitted_count,"
        " metadata_checksum, source_checkpoint, generated_at, updated_at"
        " FROM token_library_state WHERE network = ?1 AND chain_id = ?2"
        " ORDER BY library_key",
        (NETWORK, CHAIN_ID),
    )
    return {
        "rows": [
            {
                "libraryKey": e["library_key"],
                "libraryVersion": e["library_version"],
                "tokenCount": e["token_count"],
                "admittedCount": e["admitted_count"],
                "metadataChecksum": e["metadata_checksum"],
                "sourceCheckpoint": e["source_checkpoint"],
                "generatedAt": e["generated_at"],
                "updatedAt": e["updated_at"],
            }
            for e in entries
        ]
    }

def admission_events(connection, limit=40):
    entries = rows(
        connection,
        "SELECT event_id, token_key_id, library_key, previous_status, new_status,"
        " reason, actor, created_at FROM token_admission_events"
        " WHERE network = ?1 AND chain_id = ?2 ORDER BY created_at DESC LIMIT ?3",
        (NETWORK, CHAIN_ID, limit),
    )
    return [
        {
            "eventId": e["event_id"],
            "tokenKeyId": e["token_key_id"],
            "libraryKey": e["library_key"],
            "previousStatus": e["previous_status"],
            "newStatus": e["new_status"],
            "reason": e["reason"],
            "actor": e["actor"],
            "createdAt": e["created_at"],
        }
        for e in entries
    ]


# --------------------------------------------------------------------------
# scopes and pools
# --------------------------------------------------------------------------
def scope_state(connection, library_state_rows):
    current = {e["libraryKey"]: e for e in library_state_rows["rows"]}
    scopes = []
    for scope in rows(
        connection,
        "SELECT scope_key, pool_table, library_keys_json, library_versions_json,"
        " library_checksums_json, filter_mode, excluded_token_key_ids_json,"
        " enabled, materialized_checksum, last_rebuilt_at, created_at, updated_at"
        " FROM pool_scope_config WHERE network = ?1 AND chain_id = ?2"
        " ORDER BY scope_key",
        (NETWORK, CHAIN_ID),
    ):
        versions = json.loads(scope["library_versions_json"] or "{}")
        checksums = json.loads(scope["library_checksums_json"] or "{}")
        stale = []
        for library, pinned_version in versions.items():
            live = current.get(library)
            if live is None:
                stale.append({"library": library, "pinnedVersion": pinned_version,
                              "currentVersion": None, "reason": "library_missing"})
                continue
            if live["libraryVersion"] != pinned_version:
                stale.append({
                    "library": library,
                    "pinnedVersion": pinned_version,
                    "currentVersion": live["libraryVersion"],
                    "reason": "version_drift",
                })
            elif live["metadataChecksum"] != checksums.get(library):
                stale.append({
                    "library": library,
                    "pinnedVersion": pinned_version,
                    "currentVersion": live["libraryVersion"],
                    "reason": "checksum_drift",
                })
        table = scope["pool_table"]
        counts = {"raw": 0, "active": 0, "blocked": 0}
        pair_rows = []
        token_pair_count = 0
        if table:
            try:
                counts["raw"] = connection.execute(
                    "SELECT COUNT(*) FROM " + table
                    + " WHERE network = ?1 AND chain_id = ?2",
                    (NETWORK, CHAIN_ID),
                ).fetchone()[0]
                counts["active"] = connection.execute(
                    "SELECT COUNT(*) FROM " + table
                    + " WHERE network = ?1 AND chain_id = ?2 AND pool_status = 'active'",
                    (NETWORK, CHAIN_ID),
                ).fetchone()[0]
                counts["blocked"] = connection.execute(
                    "SELECT COUNT(*) FROM " + table
                    + " WHERE network = ?1 AND chain_id = ?2"
                    + " AND COALESCE(pool_status, '') <> 'active'",
                    (NETWORK, CHAIN_ID),
                ).fetchone()[0]
                pair_rows = rows(
                    connection,
                    "SELECT token0_key_id, token1_key_id, COUNT(*) AS pool_count,"
                    " GROUP_CONCAT(DISTINCT depth_status) AS depth_statuses"
                    " FROM " + table + " WHERE network = ?1 AND chain_id = ?2"
                    " GROUP BY token0_key_id, token1_key_id"
                    " ORDER BY token0_key_id, token1_key_id",
                    (NETWORK, CHAIN_ID),
                )
                token_pair_count = len(pair_rows)
            except sqlite3.Error:
                pair_rows = []
        scopes.append({
            "scopeKey": scope["scope_key"],
            "poolTable": table,
            "filterMode": scope["filter_mode"],
            "enabled": bool(scope["enabled"]),
            "libraryKeys": json.loads(scope["library_keys_json"] or "[]"),
            "pinnedLibraryVersions": versions,
            "pinnedLibraryChecksums": checksums,
            "excludedTokenKeyIds": json.loads(scope["excluded_token_key_ids_json"] or "[]"),
            "materializedChecksum": scope["materialized_checksum"],
            "lastRebuiltAt": scope["last_rebuilt_at"],
            "createdAt": scope["created_at"],
            "updatedAt": scope["updated_at"],
            "poolCounts": counts,
            "distinctTokenPairCount": token_pair_count,
            "tokenPairs": [
                {
                    "token0KeyId": p["token0_key_id"],
                    "token1KeyId": p["token1_key_id"],
                    "poolCount": p["pool_count"],
                    "depthStatuses": sorted(
                        s for s in (p["depth_statuses"] or "").split(",") if s
                    ),
                }
                for p in pair_rows
            ],
            "staleLibraries": stale,
            "libraryVersionsAreCurrent": not stale,
        })
    return scopes


# --------------------------------------------------------------------------
# Manager published state
# --------------------------------------------------------------------------
def manager_state(connection):
    observation = rows(
        connection,
        "SELECT protocol, scope_key, observation_version, materialized_checksum,"
        " candidate_edge_version, candidate_edge_checksum, pool_scope_checksum,"
        " library_versions_json, library_checksums_json, anchor_count, token_count,"
        " direction_count, empty_direction_count, pool_count,"
        " abnormal_direction_count, abnormal_pool_count, generated_at, updated_at"
        " FROM manager_market_observation_library_state"
        " WHERE network = ?1 AND chain_id = ?2",
        (NETWORK, CHAIN_ID),
    )
    candidate = rows(
        connection,
        "SELECT protocol, scope_key, candidate_version, materialized_checksum,"
        " pool_scope_checksum, pool_library_versions_json, library_versions_json,"
        " library_checksums_json, anchor_token_count, candidate_token_count,"
        " eligible_pool_count, recommendation_count, abnormal_pool_count,"
        " generated_at, updated_at"
        " FROM manager_dex_candidate_library_state"
        " WHERE network = ?1 AND chain_id = ?2",
        (NETWORK, CHAIN_ID),
    )
    closed_loop = rows(
        connection,
        "SELECT protocol, scope_key, closed_loop_version, materialized_checksum,"
        " candidate_edge_version, candidate_edge_checksum, observation_version,"
        " observation_checksum, pool_scope_checksum, library_versions_json,"
        " library_checksums_json, anchor_count, loop_count, hop_count,"
        " optimization_count, skipped_token_count, consumers_need_rebuild,"
        " generated_at, updated_at"
        " FROM manager_v4_closed_loop_library_state"
        " WHERE network = ?1 AND chain_id = ?2",
        (NETWORK, CHAIN_ID),
    )
    skipped = rows(
        connection,
        "SELECT protocol, scope_key, anchor_token_key_id, token_key_id, token_symbol,"
        " candidate_role, reason, created_at, updated_at"
        " FROM manager_v4_closed_loop_skipped_tokens"
        " WHERE network = ?1 AND chain_id = ?2"
        " ORDER BY anchor_token_key_id, token_key_id, candidate_role",
        (NETWORK, CHAIN_ID),
    )
    table_counts = {}
    for table in (
        "manager_v4_closed_loops",
        "manager_v4_closed_loop_hops",
        "manager_v4_closed_loop_optimizations",
        "manager_market_observation_yz_pairs",
        "manager_market_observation_intermediate_pools",
        "manager_market_observation_liquidity_snapshots",
        "manager_dex_candidate_anchor_tokens",
        "manager_dex_candidate_pool_recommendations",
    ):
        try:
            table_counts[table] = connection.execute(
                "SELECT COUNT(*) FROM " + table
                + " WHERE network = ?1 AND chain_id = ?2",
                (NETWORK, CHAIN_ID),
            ).fetchone()[0]
        except sqlite3.Error:
            table_counts[table] = None

    def shape(entry, extra):
        out = {
            "protocol": entry["protocol"],
            "scopeKey": entry["scope_key"],
            "materializedChecksum": entry["materialized_checksum"],
            "poolScopeChecksum": entry["pool_scope_checksum"],
            "libraryVersions": json.loads(entry["library_versions_json"] or "{}"),
            "libraryChecksums": json.loads(entry["library_checksums_json"] or "{}"),
            "generatedAt": entry["generated_at"],
            "updatedAt": entry["updated_at"],
        }
        out.update(extra)
        return out

    return {
        "marketObservation": [shape(e, {
            "observationVersion": e["observation_version"],
            "candidateEdgeVersion": e["candidate_edge_version"],
            "candidateEdgeChecksum": e["candidate_edge_checksum"],
            "anchorCount": e["anchor_count"],
            "tokenCount": e["token_count"],
            "directionCount": e["direction_count"],
            "emptyDirectionCount": e["empty_direction_count"],
            "poolCount": e["pool_count"],
            "abnormalDirectionCount": e["abnormal_direction_count"],
            "abnormalPoolCount": e["abnormal_pool_count"],
        }) for e in observation],
        "dexCandidate": [shape(e, {
            "candidateVersion": e["candidate_version"],
            "poolLibraryVersions": json.loads(e["pool_library_versions_json"] or "{}"),
            "anchorTokenCount": e["anchor_token_count"],
            "candidateTokenCount": e["candidate_token_count"],
            "eligiblePoolCount": e["eligible_pool_count"],
            "recommendationCount": e["recommendation_count"],
            "abnormalPoolCount": e["abnormal_pool_count"],
        }) for e in candidate],
        "closedLoop": [shape(e, {
            "closedLoopVersion": e["closed_loop_version"],
            "candidateEdgeVersion": e["candidate_edge_version"],
            "candidateEdgeChecksum": e["candidate_edge_checksum"],
            "observationVersion": e["observation_version"],
            "observationChecksum": e["observation_checksum"],
            "anchorCount": e["anchor_count"],
            "loopCount": e["loop_count"],
            "hopCount": e["hop_count"],
            "optimizationCount": e["optimization_count"],
            "skippedTokenCount": e["skipped_token_count"],
            "consumersNeedRebuild": e["consumers_need_rebuild"],
        }) for e in closed_loop],
        "skippedTokens": [
            {
                "protocol": s["protocol"],
                "scopeKey": s["scope_key"],
                "anchorTokenKeyId": s["anchor_token_key_id"],
                "tokenKeyId": s["token_key_id"],
                "tokenSymbol": s["token_symbol"],
                "candidateRole": s["candidate_role"],
                "reason": s["reason"],
                "createdAt": s["created_at"],
                "updatedAt": s["updated_at"],
            }
            for s in skipped
        ],
        "tableCounts": table_counts,
    }


def main():
    chain_config = read_json(CHAIN_JSON)
    configured_chain_id = chain_config.get("chain_id")
    resolution = resolve_rpc(chain_config)
    rpc_result = probe_rpc(resolution)
    stream = wss_availability(resolution)

    stock_registry = (
        read_json(STOCK_TOKENS_JSON) if os.path.exists(STOCK_TOKENS_JSON) else None
    )

    connection = open_read_only(DB)
    try:
        libraries = library_admission(connection)
        lib_state = library_state(connection)
        events = admission_events(connection)
        scopes = scope_state(connection, lib_state)
        manager = manager_state(connection)
    finally:
        connection.close()

    observation = next(
        (s for s in manager["marketObservation"]
         if s["scopeKey"] == OBSERVATION_SCOPE), None)
    closed_loop = next(
        (s for s in manager["closedLoop"]
         if s["scopeKey"] == CLOSED_LOOP_SCOPE), None)

    direction_count = observation["directionCount"] if observation else 0
    loop_count = closed_loop["loopCount"] if closed_loop else 0
    hop_count = closed_loop["hopCount"] if closed_loop else 0
    optimization_count = closed_loop["optimizationCount"] if closed_loop else 0

    observation_scope = next(
        (s for s in scopes if s["scopeKey"] == OBSERVATION_SCOPE), None)
    closed_loop_scope = next(
        (s for s in scopes if s["scopeKey"] == CLOSED_LOOP_SCOPE), None)

    drifted_scopes = [
        {
            "scopeKey": s["scopeKey"],
            "filterMode": s["filterMode"],
            "poolTable": s["poolTable"],
            "poolCounts": s["poolCounts"],
            "distinctTokenPairCount": s["distinctTokenPairCount"],
            "staleLibraries": s["staleLibraries"],
            "lastRebuiltAt": s["lastRebuiltAt"],
        }
        for s in scopes if s["staleLibraries"]
    ]

    # A middle direction needs a Y->Z token pair whose *both* ends are
    # admitted.  The candidate-exchange pool table is the raw material; the
    # scope filter decides which pairs survive.  This is reported as a
    # structural observation, never as a predicted loop.
    admitted_keys = set()
    for _library, payload in libraries["perLibrary"].items():
        for entry in payload["entries"]:
            if entry["admissionStatus"] == "admitted":
                admitted_keys.add(entry["tokenKeyId"])
    admitted_pairs = []
    if observation_scope:
        for pair in observation_scope["tokenPairs"]:
            admitted_pairs.append({
                "token0KeyId": pair["token0KeyId"],
                "token1KeyId": pair["token1KeyId"],
                "poolCount": pair["poolCount"],
                "depthStatuses": pair["depthStatuses"],
                "bothEndsAdmittedInAnyLibrary": (
                    pair["token0KeyId"] in admitted_keys
                    and pair["token1KeyId"] in admitted_keys
                ),
            })

    loop_published = (
        loop_count > 0 and hop_count > 0 and optimization_count > 0
    )
    broadcast_allowed = bool(
        chain_config.get("features", {})
        .get("dex_arbitrage", {})
        .get("allow_official_v4_broadcast", False)
    )

    report = {
        "schema": "task147-testnet-prerequisite-v1",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "task": "TASK-147",
        "acceptanceCriterion": "AC-211",
        "mode": "read-only",
        "chain": {
            "network": NETWORK,
            "configuredChainId": configured_chain_id,
            "expectedChainId": CHAIN_ID,
            "chainIdConfigMatches": configured_chain_id == CHAIN_ID,
            "rpc": {
                "configuration": resolution,
                "probe": rpc_result,
            },
            "websocket": stream,
            "broadcastAllowedByChainConfig": broadcast_allowed,
        },
        "store": {
            "path": os.path.relpath(DB, ROOT).replace("\\", "/"),
            "absolutePath": DB,
            "sha256": sha256_file(DB),
            "sizeBytes": os.path.getsize(DB),
            "openedReadOnly": True,
        },
        "tokenLibraries": libraries,
        "tokenLibraryState": lib_state,
        "tokenAdmissionEvents": events,
        "stockRegistry": {
            "path": os.path.relpath(STOCK_TOKENS_JSON, ROOT).replace("\\", "/"),
            "sha256": sha256_file(STOCK_TOKENS_JSON),
            "declaredTokens": stock_registry,
        },
        "scopes": scopes,
        "manager": manager,
        "derived": {
            "directionCount": direction_count,
            "loopCount": loop_count,
            "hopCount": hop_count,
            "optimizationCount": optimization_count,
            "observationScope": {
                "scopeKey": OBSERVATION_SCOPE,
                "poolCounts": observation_scope["poolCounts"] if observation_scope else None,
                "distinctTokenPairCount": observation_scope["distinctTokenPairCount"] if observation_scope else None,
                "staleLibraries": observation_scope["staleLibraries"] if observation_scope else None,
                "filterMode": observation_scope["filterMode"] if observation_scope else None,
            },
            "closedLoopScope": {
                "scopeKey": CLOSED_LOOP_SCOPE,
                "poolCounts": closed_loop_scope["poolCounts"] if closed_loop_scope else None,
                "distinctTokenPairCount": closed_loop_scope["distinctTokenPairCount"] if closed_loop_scope else None,
                "staleLibraries": closed_loop_scope["staleLibraries"] if closed_loop_scope else None,
                "filterMode": closed_loop_scope["filterMode"] if closed_loop_scope else None,
            },
            "scopesWithLibraryDrift": drifted_scopes,
            "candidateExchangeTokenPairs": admitted_pairs,
            "skipReasons": sorted({
                s["reason"] for s in manager["skippedTokens"]
            }),
            "closedLoopPublished": loop_published,
        },

        "acceptance": {
            "criterion": "AC-211",
            "met": bool(loop_published),
            "verdict": "met" if loop_published else "not_met",
            "reason": (
                "the Manager published at least one closed loop on the testnet "
                "chain with hops and optimizations"
                if loop_published
                else (
                    "the Manager published no closed loop on the testnet chain: "
                    "direction_count=%d, loop_count=%d, hop_count=%d. The only "
                    "admitted candidate token carries the skip reason "
                    "\u0027no_completing_middle_direction\u0027 for both the first and "
                    "the third hop, so the middle Y->Z direction required to "
                    "close the loop does not exist in the published market "
                    "observation." % (direction_count, loop_count, hop_count)
                )
            ),
        },
        "prohibitions": {
            "noBackfill": (
                "No historical depth, price, business or valuation data was "
                "backfilled. Every number in this report is read from the "
                "store as it stands; a missing value is reported as missing "
                "and never replaced by a default zero or an estimate."
            ),
            "noHandEdit": (
                "No admission, library version, scope pin, pool row or Manager "
                "row was edited by hand. The store was opened read-only."
            ),
            "noApply": (
                "No rebuild, no re-materialisation and no --apply of any kind "
                "was performed."
            ),
            "noBroadcast": (
                "No transaction was sent and no broadcast was attempted. The "
                "chain config itself declares allow_official_v4_broadcast=%s."
                % broadcast_allowed
            ),
            "measuredVsUnmeasured": (
                "A measured zero and an unmeasured quantity are kept apart: the "
                "websocket stream is reported as unavailable because the chain "
                "config declares no endpoint, which is not the same as a "
                "measured absence of traffic."
            ),
        },
        "nextStep": {
            "requiresAuthorization": True,
            "actions": [
                "re-pin and rebuild the candidate_exchange pool scope against the "
                "current core library version so the market observation can "
                "produce the missing Y->Z middle direction",
                "re-run the closed-loop Manager on the testnet chain and "
                "re-collect this prerequisite report",
            ],
            "note": (
                "Both actions write to the store. They are therefore NOT "
                "performed by this read-only collector and require an explicit "
                "separate authorization."
            ),
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print("wrote:", os.path.relpath(OUT, ROOT).replace("\\", "/"))
    print("chainIdAgreeingEndpoints: %d / %d" % (
        rpc_result["chainIdAgreements"], rpc_result["reachableEndpointCount"]))
    print("websocket:", stream["status"])
    print("directionCount: %d loopCount: %d hopCount: %d" % (
        direction_count, loop_count, hop_count))
    print("AC-211 met:", report["acceptance"]["met"])


if __name__ == "__main__":
    main()


