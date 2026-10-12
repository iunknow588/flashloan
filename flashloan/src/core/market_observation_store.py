from pathlib import Path
from typing import Any

from core.contracts import validate_scoped_payload

from core.market_observation_compact import (
    _closed_loop_dedup_key,
    _candidate_groups_from_sequences,
    _closed_routes_from_sequences,
    _compact_candidate_group,
    _compact_observation_pair,
    _compact_observation_sequence,
    _compact_route_pool,
    _dedup_closed_loop_sequences,
    _hydrate_route_pools_from_registry,
    _merge_dynamic_hop_candidates_into_sequences,
    _observation_sequences_from_observation,
    _overview_route_summary,
    _pool_registry_from_sequences,
    _route_pairs_from_sequence,
    _sequence_from_route,
)
from core.market_observation_io import (
    DEFAULT_STORE_ROOT,
    DICT_KEYS,
    LIST_KEYS,
    OVERVIEW_LIST_KEYS,
    PERSISTED_DICT_KEYS,
    PERSISTED_LIST_KEYS,
    SCHEMA_VERSION,
    SHARDABLE_LIST_KEYS,
    SHARD_MANIFEST_FILE,
    SHARD_SCHEMA_VERSION,
    STATIC_META_KEYS,
    _as_int,
    _hydrate_payload_from_shards,
    _network_for_chain,
    _protocol_key,
    _read_json,
    _shard_manifest_path,
    _shard_path_for_symbol,
    _split_payload_shards,
    _stamp,
    _text,
    _write_json,
    market_observation_overview_static_path,
    market_observation_static_path,
    market_observation_store_root,
)
from core.market_observation_merge import _list_counts, merge_market_observation_static


def _merge_bridge_pool_rows(rows: list[dict[str, Any]], registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge static bridge-pool rows with the full route-step registry."""
    by_pool_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pool_id = str(row.get("poolId") or row.get("pool") or "").strip().lower()
        if pool_id:
            by_pool_id[pool_id] = {**by_pool_id.get(pool_id, {}), **row}
    for pool_id, row in (registry or {}).items():
        if not isinstance(row, dict):
            continue
        normalized = str(pool_id or "").strip().lower()
        if normalized:
            by_pool_id[normalized] = {**by_pool_id.get(normalized, {}), **row}
    return [row for _, row in sorted(by_pool_id.items())]


def market_observation_static_table(snapshot: dict[str, Any]) -> dict[str, Any]:
    processing = snapshot.get("processing") if isinstance(snapshot.get("processing"), dict) else {}
    protocols = list(snapshot.get("protocols") or snapshot.get("selected_protocols") or [])
    chain_id = _as_int(snapshot.get("chain_id"))
    network = _text(snapshot.get("network")) or _network_for_chain(chain_id, None)
    scope_key = snapshot.get("scope_key") or snapshot.get("chain_key") or processing.get("global_chain_id")
    if not scope_key:
        scope_key = f"{network}:{chain_id or 'no-chain-id'}:{_protocol_key(protocols)}"
    return {
        "path": snapshot.get("path"),
        "exists": bool(snapshot.get("exists")),
        "network": network,
        "chain_id": chain_id,
        "protocols": protocols,
        "protocol_key": snapshot.get("protocol_key"),
        "surface": snapshot.get("surface") or "dex_market_observation",
        "source": snapshot.get("source") or "dex_market_observation_payload",
        "flow_name": snapshot.get("flow_name") or processing.get("flow_name"),
        "handler": snapshot.get("handler") or processing.get("handler"),
        "chain_key": scope_key,
        "scope_key": scope_key,
        "storage_root": snapshot.get("storage_root"),
        "generated_at": snapshot.get("generated_at"),
        "refresh_mode": snapshot.get("refresh_mode"),
        "summary": snapshot.get("summary") or {},
        "list_counts": snapshot.get("list_counts") or {},
    }


def static_snapshot_from_observation(
    observation: dict[str, Any],
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    refresh_mode: str = "incremental",
) -> dict[str, Any]:
    selected_protocols = list(protocols or observation.get("selected_protocols") or [])
    normalized_chain_id = _as_int(chain_id if chain_id is not None else observation.get("chain_id"))
    normalized_network = _network_for_chain(normalized_chain_id, network)
    processing = observation.get("processing") if isinstance(observation.get("processing"), dict) else {}
    scope_key = f"{normalized_network}:{normalized_chain_id or 'no-chain-id'}:{_protocol_key(selected_protocols)}"
    snapshot = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "mode": observation.get("mode") or "dex_market_observation",
        "network": normalized_network,
        "chain_id": normalized_chain_id,
        "selected_protocols": selected_protocols,
        "protocols": selected_protocols,
        "protocol_key": _protocol_key(selected_protocols),
        "surface": "dex_market_observation",
        "source": observation.get("marketObservationSource") or "dex_market_observation_payload",
        "marketObservationSourceStage": observation.get("marketObservationSourceStage")
        or observation.get("marketObservationSource")
        or observation.get("source")
        or "dex_market_observation_payload",
        "flow_name": observation.get("flow_name") or processing.get("flow_name") or observation.get("mode") or "dex_market_observation",
        "handler": observation.get("handler") or processing.get("handler"),
        "chain_key": scope_key,
        "scope_key": scope_key,
        "storage_root": str(market_observation_store_root()),
        "generated_at": _stamp(),
        "refresh_mode": refresh_mode,
        "summary": dict(observation.get("summary") if isinstance(observation.get("summary"), dict) else {}),
    }
    if processing:
        snapshot["processing"] = processing
    # The page snapshot is an inter-page handoff, not a copy of the V4 pool
    # index. Keep only route-level data needed by observation, overview, and
    # quote routing. Full pool evidence stays in the chain evidence files.
    for key in PERSISTED_DICT_KEYS:
        value = observation.get(key)
        if isinstance(value, dict):
            snapshot[key] = value
    # The YC/ZC candidate set is the authoritative page handoff. It keeps
    # every concrete middle-hop pool and every Y-Z direction combination,
    # including rows that have no pool.  The overview stage counts one
    # pool-backed closed loop per bridge-pool address, while the observation
    # page keeps the full Y-Z surface for display.
    sequences = _observation_sequences_from_observation(observation)
    dynamic_paths = _dynamic_overview_path_candidates(observation, normalized_chain_id, selected_protocols)
    sequences = _merge_dynamic_hop_candidates_into_sequences(sequences, dynamic_paths)
    # ``observationSequences`` keeps every directional Y-Z row for the
    # observation page. ``closedLoopSequences`` is the executable subset:
    # only three-hop routes with an actual middle pool, deduplicated by bridge
    # pool address so reverse directions and legacy route duplicates count once.
    closed_loop_sequences = _dedup_closed_loop_sequences(
        [
            sequence
            for sequence in sequences
            if isinstance(sequence, dict)
            and (
                sequence.get("observationReachable")
                or sequence.get("observationState") == "reachable"
            )
        ]
    )
    # The external observation file stores every middle-hop Y-Z pool address
    # plus the liquidity snapshot fields already present on each route step.
    # Pool addresses are fixed; later refreshes update liquidity evidence
    # without recreating the address registry.
    snapshot["chainSpecificObservationPools"] = [
        {
            "pool": _text(step.get("pool") or step.get("poolId")),
            "poolId": _text(step.get("pool") or step.get("poolId")),
            "poolIdentityMode": "pool_id",
            "protocol": "uniswap_v4",
            "dex": "uniswap_v4",
            "direction": "bridge",
            "routeText": _text(step.get("routeText"))
            or " -> ".join(_text(value) for value in step.get("routeSymbols") or []),
            "bridgeSymbol": _text(sequence.get("bridgeSymbol")),
            "counterSymbol": _text(sequence.get("counterSymbol")),
            "hasLiquidity": step.get("hasLiquidity"),
            "usableForObservation": step.get("usableForObservation"),
            "balances": step.get("balances") or {},
            "depthStatus": step.get("depthStatus") or "",
            "liquidity": step.get("liquidity") or "",
            "liquidityIndex": step.get("liquidityIndex"),
            "estimatedTvlUsd": step.get("estimatedTvlUsd"),
            "depthBand": step.get("depthBand") or {},
            "liquiditySnapshotAt": step.get("liquiditySnapshotAt"),
            "liquidityReadAt": step.get("liquidityReadAt"),
            "liquidityBlockNumber": step.get("liquidityBlockNumber"),
            "recentSwapActive": step.get("recentSwapActive"),
            "token0": step.get("token0"),
            "token1": step.get("token1"),
            "token0Symbol": step.get("token0Symbol"),
            "token1Symbol": step.get("token1Symbol"),
            "addressPair": step.get("pairAddress") or "",
        }
        for sequence in sequences
        if isinstance(sequence, dict)
        for step in sequence.get("routePools") or []
        if isinstance(step, dict)
        and _text(step.get("direction")).lower() == "bridge"
        and _text(step.get("pool") or step.get("poolId"))
    ]
    snapshot["summary"] = {
        **(snapshot.get("summary") or {}),
        "closedLoopSequenceCount": len(closed_loop_sequences),
        "fundAnchorPairObservationRouteCount": len(sequences),
        "nonEthPairFirstRouteCount": len(sequences),
        "chainSpecificObservationSequenceCount": len(sequences),
        "observationSequenceCount": len(sequences),
    }
    sequence_anchors = sorted(
        {
            _text(sequence.get("anchorSymbol")).upper()
            for sequence in sequences
            if isinstance(sequence, dict) and _text(sequence.get("anchorSymbol"))
        }
    )
    anchors = [
        _text(symbol).upper()
        for symbol in (
            observation.get("observationAnchorSymbols")
            or observation.get("borrowableSymbols")
            or []
        )
        if _text(symbol)
    ]
    if not anchors:
        anchors = sequence_anchors
    raw_groups = observation.get("candidateObservationGroups")
    if not isinstance(raw_groups, list):
        raw_groups = observation.get("anchorGroups")
    snapshot["observationSequences"] = sequences
    snapshot["closedLoopSequences"] = list(closed_loop_sequences)
    # Rebuild a full pool registry from observation pools so later page reads
    # can hydrate every route, including routes restored from legacy
    # closedLoopRoutes.  Static pool addresses remain stable; liquidity fields
    # are refreshed separately.
    registry = _pool_registry_from_sequences(sequences)
    snapshot["poolRegistry"] = registry
    snapshot["chainSpecificObservationPools"] = _merge_bridge_pool_rows(
        snapshot.get("chainSpecificObservationPools") or [],
        registry,
    )
    snapshot["candidateObservationGroups"] = _candidate_groups_from_sequences(
        raw_groups if isinstance(raw_groups, list) else [],
        sequences,
        anchors,
    )
    snapshot["borrowableSymbols"] = list(observation.get("borrowableSymbols") or anchors)
    snapshot["observationAnchorSymbols"] = list(observation.get("observationAnchorSymbols") or anchors)
    snapshot["anchorGroups"] = list(observation.get("anchorGroups") or [])
    snapshot["pairs"] = list(observation.get("pairs") or [])
    snapshot["bridgePairs"] = list(observation.get("bridgePairs") or [])
    snapshot["chainSpecificObservationPairs"] = [
        _compact_observation_pair(pair)
        for pair in observation.get("chainSpecificObservationPairs") or []
        if isinstance(pair, dict)
    ]
    snapshot["tableSource"] = observation.get("tableSource") or "ob_table"
    snapshot["referencedTable"] = observation.get("referencedTable") or "ch_table"
    snapshot["poolIndexVersion"] = observation.get("poolIndexVersion") or {}
    snapshot["list_counts"] = _list_counts(snapshot)
    return snapshot


def _dynamic_overview_path_candidates(
    observation: dict[str, Any],
    chain_id: int | None,
    protocols: list[str] | None,
) -> list[dict[str, Any]]:
    """Return dynamic closed-loop candidates for sequence enrichment.

    The market-observation facade owns the graph algorithm.  This helper is
    intentionally isolated here so core snapshots can preserve per-hop
    candidates without creating an import cycle.
    """
    if not (observation.get("bridgePairs") or observation.get("anchorGroups")):
        return []
    try:
        from market.binance_market.dex_surface_observation_impl import (
            _overview_path_candidates_from_observation_sets,
        )

        paths, _watch, _anchors = _overview_path_candidates_from_observation_sets(
            observation,
            None,
            chain_id=chain_id,
            protocols=protocols,
        )
        return [path for path in paths if isinstance(path, dict)]
    except Exception:
        return []


def overview_snapshot_from_observation(snapshot: dict[str, Any]) -> dict[str, Any]:
    overview = {
        key: value
        for key, value in snapshot.items()
        if key in STATIC_META_KEYS or key in {"mode", "chain_id", "selected_protocols", "summary"}
    }
    overview["snapshot_kind"] = "dex_overview_compact"
    overview["observationSnapshotPath"] = snapshot.get("path")
    summaries: list[dict[str, Any]] = []
    seen_loop_keys: set[tuple[str, tuple[str, ...]]] = set()
    for sequence in snapshot.get("observationSequences") or []:
        if not isinstance(sequence, dict):
            continue
        # Market observation keeps every Y-Z direction, including rows without
        # a middle-hop pool. DEX overview must only receive pool-backed closed
        # loops; no-pool discovery rows stay on the observation page and must
        # not inflate the static overview.
        if not (
            sequence.get("observationReachable")
            or sequence.get("observationState") == "reachable"
        ):
            continue
        loop_key = _closed_loop_dedup_key(sequence)
        if loop_key in seen_loop_keys:
            continue
        seen_loop_keys.add(loop_key)
        summaries.append(_overview_route_summary(sequence))
    overview["closedLoopRouteSummaries"] = summaries
    for key in OVERVIEW_LIST_KEYS:
        value = snapshot.get(key)
        if isinstance(value, list):
            overview[key] = value
    overview["tableSource"] = snapshot.get("tableSource") or "ob_table"
    overview["referencedTable"] = snapshot.get("referencedTable") or "ch_table"
    overview["poolIndexVersion"] = snapshot.get("poolIndexVersion") or {}
    for key in PERSISTED_DICT_KEYS:
        value = snapshot.get(key)
        if isinstance(value, dict):
            overview[key] = value
    overview["list_counts"] = _list_counts(overview)
    return overview


def write_market_observation_static(
    observation: dict[str, Any],
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    refresh_mode: str = "incremental",
    root: Path | str | None = None,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incoming = static_snapshot_from_observation(
        observation,
        network=network,
        chain_id=chain_id,
        protocols=protocols,
        refresh_mode=refresh_mode,
    )
    path = market_observation_static_path(
        network=incoming["network"],
        chain_id=incoming["chain_id"],
        protocols=incoming["protocols"],
        root=root,
    )
    if refresh_mode == "full":
        stored = incoming
    else:
        existing = _read_json(path)
        stored = merge_market_observation_static(existing, incoming) if existing else incoming
    stored = {**stored, "path": str(path), "exists": True}
    if expected_scope is not None:
        validate_scoped_payload({"scope": expected_scope, **stored}, expected_scope)
        stored["scope"] = dict(expected_scope)
    _write_sharded_market_observation_static(
        stored,
        path=path,
        refresh_mode=refresh_mode,
        expected_scope=expected_scope,
    )
    overview_path = market_observation_overview_static_path(
        network=stored["network"],
        chain_id=stored["chain_id"],
        protocols=stored["protocols"],
        root=root,
    )
    _write_sharded_market_observation_overview(stored, path=overview_path)
    return stored


def _write_sharded_market_observation_static(
    stored: dict[str, Any],
    *,
    path: Path,
    refresh_mode: str,
    expected_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Write observation.json as a compact index plus per-anchor shards.

    Large row lists are removed from the main file and persisted in
    ``shards/<anchor>.json``.  The main file carries ``shardRef`` so readers
    can reconstruct the legacy schema in memory; it does not keep a duplicate
    full-size copy.
    """
    compact = dict(stored)
    shards = _split_payload_shards(
        stored,
        path,
        network=str(stored.get("network") or ""),
        chain_id=_as_int(stored.get("chain_id")),
        protocols=list(stored.get("protocols") or stored.get("selected_protocols") or []),
    )
    for key in SHARDABLE_LIST_KEYS:
        compact.pop(key, None)
    compact["shardRef"] = {
        "schemaVersion": SHARD_SCHEMA_VERSION,
        "manifest": str(_shard_manifest_path(path)),
        "shardCount": len(shards),
        "shards": [
            {
                "anchorSymbol": shard["anchorSymbol"],
                "path": str(_shard_path_for_symbol(path, shard["anchorSymbol"])),
                "rowKeys": sorted(shard["rows"].keys()),
                "rowCount": sum(len(rows) for rows in shard["rows"].values()),
            }
            for shard in shards
        ],
    }
    compact["list_counts"] = _list_counts(compact)
    if expected_scope is not None:
        validate_scoped_payload({"scope": expected_scope, **compact}, expected_scope)
        compact["scope"] = dict(expected_scope)
    _write_json(path, compact)
    _write_shard_manifest(path, shards)
    for shard in shards:
        shard_path = _shard_path_for_symbol(path, shard["anchorSymbol"])
        _write_json(shard_path, shard)
    return compact


def _write_shard_manifest(payload_path: Path, shards: list[dict[str, Any]]) -> None:
    manifest = {
        "schemaVersion": SHARD_SCHEMA_VERSION,
        "payloadPath": str(payload_path),
        "shardCount": len(shards),
        "shards": [
            {
                "anchorSymbol": shard["anchorSymbol"],
                "path": str(_shard_path_for_symbol(payload_path, shard["anchorSymbol"])),
                "rowKeys": sorted(shard["rows"].keys()),
                "rowCount": sum(len(rows) for rows in shard["rows"].values()),
            }
            for shard in shards
        ],
    }
    _write_json(_shard_manifest_path(payload_path), manifest)


def _write_sharded_market_observation_overview(stored: dict[str, Any], *, path: Path) -> dict[str, Any]:
    overview = overview_snapshot_from_observation(stored)
    overview = {**overview, "path": str(path), "exists": True}
    return _write_sharded_market_observation_static(
        overview,
        path=path,
        refresh_mode="full",
        expected_scope=None,
    )


def load_market_observation_static(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    root: Path | str | None = None,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = market_observation_static_path(network=network, chain_id=chain_id, protocols=protocols, root=root)
    payload = _read_json(path)
    if payload:
        payload = _hydrate_payload_from_shards(
            payload,
            path,
            network=str(payload.get("network") or _network_for_chain(_as_int(chain_id), network) or "unknown"),
            chain_id=_as_int(payload.get("chain_id") or chain_id),
            protocols=list(payload.get("protocols") or payload.get("selected_protocols") or protocols or []),
        )
        if expected_scope is not None:
            validate_scoped_payload(payload, expected_scope)
        return {**payload, "path": str(path), "exists": True}
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "path": str(path),
        "exists": False,
        "network": _network_for_chain(_as_int(chain_id), network),
        "chain_id": _as_int(chain_id),
        "protocols": list(protocols or []),
        "selected_protocols": list(protocols or []),
        "protocol_key": _protocol_key(protocols),
        "summary": {},
        "list_counts": {},
    }


def load_market_observation_overview_static(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    path = market_observation_overview_static_path(
        network=network,
        chain_id=chain_id,
        protocols=protocols,
        root=root,
    )
    payload = _read_json(path)
    if payload:
        payload = _hydrate_payload_from_shards(
            payload,
            path,
            network=str(payload.get("network") or _network_for_chain(_as_int(chain_id), network) or "unknown"),
            chain_id=_as_int(payload.get("chain_id") or chain_id),
            protocols=list(payload.get("protocols") or payload.get("selected_protocols") or protocols or []),
        )
        return {**payload, "path": str(path), "exists": True}
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "path": str(path),
        "exists": False,
        "network": _network_for_chain(_as_int(chain_id), network),
        "chain_id": _as_int(chain_id),
        "protocols": list(protocols or []),
        "selected_protocols": list(protocols or []),
        "protocol_key": _protocol_key(protocols),
        "snapshot_kind": "dex_overview_compact",
        "summary": {},
        "list_counts": {},
    }


def write_market_observation_overview_from_static(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    snapshot = load_market_observation_static(
        network=network,
        chain_id=chain_id,
        protocols=protocols,
        root=root,
    )
    if not snapshot.get("exists"):
        return snapshot
    path = market_observation_overview_static_path(
        network=snapshot.get("network"),
        chain_id=snapshot.get("chain_id"),
        protocols=snapshot.get("protocols") or snapshot.get("selected_protocols"),
        root=root,
    )
    return _write_sharded_market_observation_overview(snapshot, path=path)


def compact_market_observation_static(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Rewrite a legacy full snapshot as a route-level page handoff."""
    existing = load_market_observation_static(
        network=network,
        chain_id=chain_id,
        protocols=protocols,
        root=root,
    )
    if not existing.get("exists"):
        return existing

    compact = static_snapshot_from_observation(
        existing,
        network=existing.get("network") or network,
        chain_id=existing.get("chain_id") or chain_id,
        protocols=existing.get("protocols") or existing.get("selected_protocols") or protocols,
        refresh_mode=existing.get("refresh_mode") or "full",
    )
    compact["first_generated_at"] = existing.get("first_generated_at") or existing.get("generated_at")
    compact["generated_at"] = existing.get("generated_at") or compact.get("generated_at")
    path = market_observation_static_path(
        network=compact.get("network"),
        chain_id=compact.get("chain_id"),
        protocols=compact.get("protocols"),
        root=root,
    )
    compact = {**compact, "path": str(path), "exists": True}
    _write_sharded_market_observation_static(
        compact,
        path=path,
        refresh_mode="full",
        expected_scope=None,
    )

    overview_path = market_observation_overview_static_path(
        network=compact.get("network"),
        chain_id=compact.get("chain_id"),
        protocols=compact.get("protocols"),
        root=root,
    )
    _write_sharded_market_observation_overview(compact, path=overview_path)
    return compact


def static_store_metadata(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    payload = load_market_observation_static(network=network, chain_id=chain_id, protocols=protocols, root=root)
    return market_observation_static_table(payload)


def observation_payload_from_static(snapshot: dict[str, Any], *, source: str = "cached_snapshot") -> dict[str, Any]:
    payload = {
        key: value
        for key, value in snapshot.items()
        if key not in STATIC_META_KEYS
    }
    for key in LIST_KEYS:
        if not isinstance(payload.get(key), list):
            payload[key] = []
    # The persisted observation handoff carries the YC-ZC bridge pools and the
    # concrete closed-loop sequences.  Restore those sequences from the compact
    # pool registry so a later page read does not discard the middle-hop pools
    # that the DEX overview still needs.
    persisted_sequences = payload.get("observationSequences") or payload.get("closedLoopSequences") or []
    payload["observationSequences"] = _hydrate_route_pools_from_registry(
        persisted_sequences,
        payload.get("poolRegistry"),
    )
    payload["closedLoopRoutes"] = []
    payload["indirectRoutes"] = []
    # The registry stays available to the observation page so middle-hop pool
    # addresses and their latest liquidity snapshot can be rendered without
    # re-reading the full V4 index.
    payload["candidateObservationGroups"] = _candidate_groups_from_sequences(
        payload.get("candidateObservationGroups") or payload.get("anchorGroups") or [],
        payload["observationSequences"],
        [
            _text(symbol).upper()
            for symbol in (
                payload.get("observationAnchorSymbols")
                or payload.get("borrowableSymbols")
                or []
            )
            if _text(symbol)
        ],
    )
    if not payload.get("observationAnchorSymbols"):
        anchor_set: set[str] = set()
        for group in payload.get("anchorGroups") or []:
            if isinstance(group, dict) and _text(group.get("anchorSymbol")):
                anchor_set.add(_text(group.get("anchorSymbol")).upper())
        for group in payload.get("candidateObservationGroups") or []:
            if isinstance(group, dict) and _text(group.get("anchorSymbol")):
                anchor_set.add(_text(group.get("anchorSymbol")).upper())
        anchors = sorted(anchor_set)
        payload["observationAnchorSymbols"] = anchors
    if not payload.get("borrowableSymbols"):
        payload["borrowableSymbols"] = list(payload.get("observationAnchorSymbols") or [])
    if not isinstance(payload.get("summary"), dict):
        payload["summary"] = {}
    payload["summary"]["observationAnchorCount"] = len(payload.get("observationAnchorSymbols") or [])
    payload["summary"]["borrowableTokenCount"] = len(payload.get("borrowableSymbols") or [])
    payload["summary"]["candidateObservationGroupCount"] = len(payload.get("candidateObservationGroups") or [])
    payload["summary"]["observationSequenceCount"] = len(payload.get("observationSequences") or [])
    payload["summary"]["closedLoopSequenceCount"] = len(payload.get("closedLoopSequences") or [])
    payload["summary"]["closedLoopRouteCount"] = len(payload.get("closedLoopRoutes") or [])
    payload["mode"] = payload.get("mode") or "dex_market_observation"
    payload["selected_protocols"] = payload.get("selected_protocols") or snapshot.get("protocols") or []
    # This field describes the current page handoff. A snapshot may have been
    # generated by a live refresh, but a later page is still reading it from
    # the local static cache rather than recomputing the market observation.
    payload["marketObservationSourceStage"] = (
        source
        if source == "cached_snapshot"
        else payload.get("marketObservationSourceStage") or source
    )
    payload["marketObservationStaticTable"] = {
        **market_observation_static_table(snapshot),
    }
    if not isinstance(payload.get("poolStaticTable"), dict):
        payload["poolStaticTable"] = {
            "path": None,
            "exists": False,
            "network": snapshot.get("network"),
            "chainId": snapshot.get("chain_id"),
        }
    payload["marketObservationSource"] = source
    return payload


__all__ = [
    "DEFAULT_STORE_ROOT",
    "PERSISTED_DICT_KEYS",
    "PERSISTED_LIST_KEYS",
    "SCHEMA_VERSION",
    "load_market_observation_static",
    "load_market_observation_overview_static",
    "market_observation_static_path",
    "market_observation_overview_static_path",
    "market_observation_static_table",
    "market_observation_store_root",
    "merge_market_observation_static",
    "compact_market_observation_static",
    "observation_payload_from_static",
    "overview_snapshot_from_observation",
    "static_snapshot_from_observation",
    "static_store_metadata",
    "write_market_observation_overview_from_static",
    "write_market_observation_static",
]
