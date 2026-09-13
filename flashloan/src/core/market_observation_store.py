from pathlib import Path
from typing import Any

from core.contracts import validate_scoped_payload

from core.market_observation_compact import (
    _candidate_groups_from_sequences,
    _closed_routes_from_sequences,
    _compact_candidate_group,
    _compact_observation_pair,
    _compact_observation_sequence,
    _compact_route_pool,
    _observation_sequences_from_observation,
    _overview_route_summary,
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
    STATIC_META_KEYS,
    _as_int,
    _network_for_chain,
    _protocol_key,
    _read_json,
    _stamp,
    _text,
    _write_json,
    market_observation_overview_static_path,
    market_observation_static_path,
    market_observation_store_root,
)
from core.market_observation_merge import _list_counts, merge_market_observation_static

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
    sequences = _observation_sequences_from_observation(observation)
    anchors = [
        _text(symbol).upper()
        for symbol in (
            observation.get("observationAnchorSymbols")
            or observation.get("borrowableSymbols")
            or []
        )
        if _text(symbol)
    ]
    raw_groups = observation.get("candidateObservationGroups")
    if not isinstance(raw_groups, list):
        raw_groups = observation.get("anchorGroups")
    snapshot["observationSequences"] = sequences
    snapshot["candidateObservationGroups"] = _candidate_groups_from_sequences(
        raw_groups if isinstance(raw_groups, list) else [],
        sequences,
        anchors,
    )
    snapshot["borrowableSymbols"] = list(observation.get("borrowableSymbols") or anchors)
    snapshot["observationAnchorSymbols"] = list(observation.get("observationAnchorSymbols") or anchors)
    snapshot["chainSpecificObservationPairs"] = [
        _compact_observation_pair(pair)
        for pair in observation.get("chainSpecificObservationPairs") or []
        if isinstance(pair, dict)
    ]
    snapshot["list_counts"] = _list_counts(snapshot)
    return snapshot


def overview_snapshot_from_observation(snapshot: dict[str, Any]) -> dict[str, Any]:
    overview = {
        key: value
        for key, value in snapshot.items()
        if key in STATIC_META_KEYS or key in {"mode", "chain_id", "selected_protocols", "summary"}
    }
    overview["snapshot_kind"] = "dex_overview_compact"
    overview["observationSnapshotPath"] = snapshot.get("path")
    overview["closedLoopRouteSummaries"] = [
        _overview_route_summary(sequence)
        for sequence in snapshot.get("observationSequences") or []
        if isinstance(sequence, dict)
    ]
    for key in ("borrowableSymbols", "observationAnchorSymbols", "chainSpecificObservationPairs"):
        value = snapshot.get(key)
        if isinstance(value, list):
            overview[key] = value
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
    _write_json(path, stored)
    overview_path = market_observation_overview_static_path(
        network=stored["network"],
        chain_id=stored["chain_id"],
        protocols=stored["protocols"],
        root=root,
    )
    overview = {
        **overview_snapshot_from_observation(stored),
        "path": str(overview_path),
        "exists": True,
    }
    _write_json(overview_path, overview)
    return stored


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
    overview = {
        **overview_snapshot_from_observation(snapshot),
        "path": str(path),
        "exists": True,
    }
    _write_json(path, overview)
    return overview


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
    _write_json(path, compact)

    overview_path = market_observation_overview_static_path(
        network=compact.get("network"),
        chain_id=compact.get("chain_id"),
        protocols=compact.get("protocols"),
        root=root,
    )
    overview = {
        **overview_snapshot_from_observation(compact),
        "path": str(overview_path),
        "exists": True,
    }
    _write_json(overview_path, overview)
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
    payload["observationSequences"] = _observation_sequences_from_observation(payload)
    payload["closedLoopRoutes"] = _closed_routes_from_sequences(payload["observationSequences"])
    payload["indirectRoutes"] = [
        route for route in payload["closedLoopRoutes"]
        if route.get("kind") == "bridge_closed_loop"
    ]
    payload["directClosedLoopRoutes"] = [
        route for route in payload["closedLoopRoutes"]
        if route.get("kind") == "direct_closed_pair"
    ]
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
        anchors = sorted(
            {
                _text(group.get("anchorSymbol")).upper()
                for group in payload.get("anchorGroups") or []
                if isinstance(group, dict) and _text(group.get("anchorSymbol"))
            }
        )
        payload["observationAnchorSymbols"] = anchors
    if not payload.get("borrowableSymbols"):
        payload["borrowableSymbols"] = list(payload.get("observationAnchorSymbols") or [])
    if not isinstance(payload.get("summary"), dict):
        payload["summary"] = {}
    payload["summary"]["observationAnchorCount"] = len(payload.get("observationAnchorSymbols") or [])
    payload["summary"]["borrowableTokenCount"] = len(payload.get("borrowableSymbols") or [])
    payload["summary"]["candidateObservationGroupCount"] = len(payload.get("candidateObservationGroups") or [])
    payload["summary"]["observationSequenceCount"] = len(payload.get("observationSequences") or [])
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
