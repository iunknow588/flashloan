from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_ROOT = APP_ROOT / "runtime" / "market_observation"
SCHEMA_VERSION = "market-observation-static-store-v3"

LIST_KEYS = (
    "borrowableSymbols",
    "observationAnchorSymbols",
    "anchorGroups",
    "candidateObservationGroups",
    "observationSequences",
    "directPools",
    "directClosedLoopRoutes",
    "closedLoopRoutes",
    "indirectRoutes",
    "unmatchedPools",
    "chainSpecificObservationPairs",
    "chainSpecificObservationPools",
    "chainSpecificObservationTokens",
    "closedLoopRouteSummaries",
)

PERSISTED_LIST_KEYS = (
    "borrowableSymbols",
    "observationAnchorSymbols",
    "candidateObservationGroups",
    "observationSequences",
    "chainSpecificObservationPairs",
)

DICT_KEYS = (
    "bridgePoolIndexLookup",
    "chainSpecificObservation",
    "dexSettlement",
    "flashLoanLiquidity",
    "poolStaticTable",
)

PERSISTED_DICT_KEYS = (
    "dexSettlement",
    "flashLoanLiquidity",
    "poolStaticTable",
)

OVERVIEW_LIST_KEYS = (
    "borrowableSymbols",
    "observationAnchorSymbols",
    "closedLoopRouteSummaries",
    "chainSpecificObservationPairs",
)

STATIC_META_KEYS = {
    "ok",
    "schema_version",
    "network",
    "protocols",
    "protocol_key",
    "generated_at",
    "refresh_mode",
    "first_generated_at",
    "previous_path",
    "path",
    "exists",
    "list_counts",
    "surface",
    "source",
    "flow_name",
    "handler",
    "chain_key",
    "scope_key",
    "storage_root",
    "processing",
}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: object) -> str:
    return str(value or "").strip()


def _as_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _network_for_chain(chain_id: int | None, fallback: object = "") -> str:
    if chain_id is not None:
        row = chain_data_by_chain_id(int(chain_id))
        if row and row.get("network"):
            return _text(row["network"]).lower()
    return _text(fallback).lower() or "unknown"


def _protocol_key(protocols: list[str] | None) -> str:
    values = sorted({_text(protocol).lower() for protocol in (protocols or []) if _text(protocol)})
    return "+".join(values) if values else "all"


def market_observation_store_root(root: Path | str | None = None) -> Path:
    return Path(root) if root is not None else DEFAULT_STORE_ROOT


def market_observation_static_path(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    root: Path | str | None = None,
) -> Path:
    normalized_chain_id = _as_int(chain_id)
    normalized_network = _network_for_chain(normalized_chain_id, network)
    chain_part = str(normalized_chain_id) if normalized_chain_id is not None else "no-chain-id"
    return market_observation_store_root(root) / normalized_network / chain_part / _protocol_key(protocols) / "observation.json"


def market_observation_overview_static_path(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    root: Path | str | None = None,
) -> Path:
    return market_observation_static_path(
        network=network,
        chain_id=chain_id,
        protocols=protocols,
        root=root,
    ).with_name("overview.json")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _row_identity(row: dict[str, Any]) -> str:
    for key in (
        "routeKey",
        "pathText",
        "addressPair",
        "symbolPair",
        "anchorSymbol",
        "pool",
        "poolId",
        "address",
    ):
        value = _text(row.get(key))
        if value:
            return f"{key}:{value.lower()}"
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _merge_rows(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in [*existing, *incoming]:
        if not isinstance(row, dict):
            continue
        key = _row_identity(row)
        if key not in merged:
            order.append(key)
        merged[key] = {**merged.get(key, {}), **row}
    return [merged[key] for key in order]


def _merge_list_values(existing: list[Any], incoming: list[Any], *, key: str) -> list[Any]:
    if key == "candidateObservationGroups":
        existing = [
            _compact_candidate_group(row)
            for row in existing
            if isinstance(row, dict)
        ]
        incoming = [
            _compact_candidate_group(row)
            for row in incoming
            if isinstance(row, dict)
        ]
        return _merge_rows(existing, incoming)
    if key == "observationSequences":
        existing = [
            _compact_observation_sequence(row)
            for row in existing
            if isinstance(row, dict)
        ]
        incoming = [
            _compact_observation_sequence(row)
            for row in incoming
            if isinstance(row, dict)
        ]
        return _merge_rows(existing, incoming)
    if key == "chainSpecificObservationPairs":
        existing = [
            _compact_observation_pair(row)
            for row in existing
            if isinstance(row, dict)
        ]
        incoming = [
            _compact_observation_pair(row)
            for row in incoming
            if isinstance(row, dict)
        ]
        return _merge_rows(existing, incoming)
    if any(isinstance(row, dict) for row in [*existing, *incoming]):
        return _merge_rows(
            [row for row in existing if isinstance(row, dict)],
            [row for row in incoming if isinstance(row, dict)],
        )
    values: list[Any] = []
    seen: set[str] = set()
    for value in [*existing, *incoming]:
        identity = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        values.append(value)
    return values


def _list_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(payload.get(key) or [])
        for key in LIST_KEYS
        if isinstance(payload.get(key), list)
    }


def _compact_route_pool(step: dict[str, Any]) -> dict[str, Any]:
    """Keep only the hop evidence needed to rebuild quote-route inputs."""
    keys = (
        "pool",
        "direction",
        "anchorSymbol",
        "anchorAddress",
        "counterSymbol",
        "counterAddress",
        "routeSymbols",
        "routeAddresses",
        "routeText",
        "poolIdentityMode",
        "protocol",
        "dex",
        "fee",
        "quality",
        "classification",
        "balances",
        "depthBand",
        "quoteSummary",
        "tick",
        "sqrtPriceX96",
        "liquidity",
        "token0",
        "token1",
        "token0Symbol",
        "token1Symbol",
        "pairSymbol",
        "pairAddress",
        "source",
        "evidence",
    )
    return {key: step[key] for key in keys if key in step}


def _compact_observation_sequence(sequence: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: value
        for key, value in sequence.items()
        if key not in {"ycPool", "bridgePool", "zcPool", "routePairs"}
    }
    route_pools = [
        _compact_route_pool(step)
        for step in sequence.get("routePools") or []
        if isinstance(step, dict)
    ]
    compact["routePools"] = route_pools
    return compact


def _compact_candidate_group(group: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "anchorSymbol",
        "anchorAddresses",
        "candidatePoolCount",
        "candidateQueueCount",
        "candidateSetMode",
        "sharedPoolCount",
        "sharedTokenCount",
        "ycPoolCount",
        "ycTokenCount",
        "ycZcSameSet",
        "ycZcSharedPoolSet",
        "zcPoolCount",
        "zcTokenCount",
        "observationSequenceCount",
        "observationSequencePreview",
        "observationReachable",
        "observationEmptyReason",
        "observationState",
        "observationCombinationCount",
        "observationCombinationPreview",
        "directClosedLoopCount",
        "bridgeClosedLoopCount",
        "closedLoopRouteCount",
    )
    compact = {key: group[key] for key in keep if key in group}

    def compact_tokens(rows: object) -> list[dict[str, Any]]:
        return [
            {
                key: token[key]
                for key in ("symbol", "address", "poolCount")
                if key in token
            }
            for token in rows or []
            if isinstance(token, dict)
        ]

    shared_tokens = compact_tokens(group.get("sharedTokens"))
    yc_tokens = compact_tokens(group.get("ycTokens"))
    zc_tokens = compact_tokens(group.get("zcTokens"))
    if shared_tokens:
        compact["sharedTokens"] = shared_tokens
        compact["ycTokensRef"] = "sharedTokens"
        compact["zcTokensRef"] = "sharedTokens"
    else:
        if yc_tokens:
            compact["ycTokens"] = yc_tokens
        if zc_tokens:
            compact["zcTokens"] = zc_tokens
    return compact


def _compact_observation_pair(pair: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "symbolPair",
        "addressPair",
        "token0",
        "token1",
        "poolCount",
        "kind",
        "source",
        "observationReason",
        "closedLoopReady",
        "associatedFundSymbols",
        "routeSemantics",
        "directSwapRequired",
        "directSwapPoolCount",
        "closedLoopRoutePreviews",
    )
    return {key: pair[key] for key in keep if key in pair}


def _overview_route_summary(sequence: dict[str, Any]) -> dict[str, Any]:
    route_pools = [
        step
        for step in sequence.get("routePools") or []
        if isinstance(step, dict)
    ]
    pool_ids = [
        _text(step.get("pool")).lower()
        for step in route_pools
        if _text(step.get("pool"))
    ]
    pair_symbols = [
        _text(step.get("pairSymbol") or " -> ".join(step.get("routeSymbols") or ""))
        for step in route_pools
        if _text(step.get("pairSymbol") or " -> ".join(step.get("routeSymbols") or ""))
    ]
    return {
        "routeKey": "|".join(
            [
                _text(sequence.get("pathText")),
                *pool_ids,
            ]
        ),
        "kind": sequence.get("kind") or "bridge_closed_loop",
        "source": sequence.get("source") or "market_observation",
        "anchorSymbol": sequence.get("anchorSymbol"),
        "counterSymbol": sequence.get("counterSymbol"),
        "bridgeSymbol": sequence.get("bridgeSymbol"),
        "pathSymbols": list(sequence.get("pathSymbols") or []),
        "pathText": sequence.get("pathText") or "",
        "poolIds": pool_ids,
        "pairSymbols": pair_symbols,
        "anchorAddresses": list(sequence.get("anchorAddresses") or []),
        "observationRef": "observation.json",
    }


def _sequence_from_route(route: dict[str, Any]) -> dict[str, Any]:
    route_pools = [
        _compact_route_pool(step)
        for step in route.get("routePools") or []
        if isinstance(step, dict)
    ]
    if not route_pools:
        for pair in route.get("routePairs") or []:
            if not isinstance(pair, dict):
                continue
            pools = pair.get("pools") or []
            if pools and isinstance(pools[0], dict):
                route_pools.append(_compact_route_pool(pools[0]))
    return {
        "kind": route.get("kind") or "bridge_closed_loop",
        "source": route.get("source") or "cached_snapshot",
        "anchorSymbol": route.get("anchorSymbol") or route.get("borrowSymbol"),
        "counterSymbol": route.get("targetSymbol") or route.get("counterSymbol"),
        "bridgeSymbol": route.get("bridgeSymbol"),
        "pathSymbols": list(route.get("pathSymbols") or []),
        "pathText": route.get("pathText") or "",
        "pool": route.get("pool"),
        "routePools": route_pools,
        "candidatePoolCount": route.get("candidatePoolCount") or str(len(route_pools)),
        "matchedPoolCount": route.get("matchedPoolCount") or str(len(route_pools)),
        "anchorAddresses": list(route.get("anchorAddresses") or []),
        "counterTokenCount": route.get("counterTokenCount"),
    }


def _observation_sequences_from_observation(observation: dict[str, Any]) -> list[dict[str, Any]]:
    sequences = [
        _compact_observation_sequence(sequence)
        for sequence in observation.get("observationSequences") or []
        if isinstance(sequence, dict)
    ]
    if sequences:
        return sequences
    routes = [
        route
        for key in ("closedLoopRoutes", "indirectRoutes", "directClosedLoopRoutes")
        for route in observation.get(key) or []
        if isinstance(route, dict)
    ]
    seen: set[tuple[str, str]] = set()
    recovered: list[dict[str, Any]] = []
    for route in routes:
        sequence = _sequence_from_route(route)
        identity = (str(sequence.get("pathText") or ""), str(sequence.get("pool") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        recovered.append(sequence)
    return recovered


def _route_pairs_from_sequence(sequence: dict[str, Any]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for step in sequence.get("routePools") or []:
        if not isinstance(step, dict):
            continue
        route_symbols = list(step.get("routeSymbols") or [])
        pairs.append(
            {
                "symbolPair": step.get("pairSymbol") or " -> ".join(route_symbols),
                "tradeSymbols": route_symbols,
                "poolCount": 1 if step.get("pool") else 0,
                "classification": step.get("classification") or {},
                "pool": step.get("pool"),
                "protocol": step.get("protocol") or step.get("dex") or "",
                "fee": step.get("fee"),
                "pools": [step] if step.get("pool") else [],
            }
        )
    return pairs


def _closed_routes_from_sequences(sequences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for sequence in sequences:
        route_pairs = _route_pairs_from_sequence(sequence)
        routes.append(
            {
                "kind": "direct_closed_pair" if len(route_pairs) <= 2 else "bridge_closed_loop",
                "borrowSymbol": sequence.get("anchorSymbol"),
                "anchorSymbol": sequence.get("anchorSymbol"),
                "bridgeSymbol": sequence.get("bridgeSymbol"),
                "targetSymbol": sequence.get("counterSymbol"),
                "pathSymbols": list(sequence.get("pathSymbols") or []),
                "pathText": sequence.get("pathText") or "",
                "closedLoop": True,
                "firstLegCount": 1 if route_pairs else 0,
                "secondLegCount": max(0, len(route_pairs) - 1),
                "firstLegPair": route_pairs[0] if route_pairs else {},
                "secondLegPairs": route_pairs[1:],
                "routePairs": route_pairs,
                "routePools": list(sequence.get("routePools") or []),
                "source": sequence.get("source") or "yc_zc_bridge_observation",
            }
        )
    return routes


def _candidate_groups_from_sequences(
    groups: list[dict[str, Any]],
    sequences: list[dict[str, Any]],
    anchors: list[str],
) -> list[dict[str, Any]]:
    by_anchor: dict[str, list[dict[str, Any]]] = {}
    for sequence in sequences:
        anchor = _text(sequence.get("anchorSymbol")).upper()
        if anchor:
            by_anchor.setdefault(anchor, []).append(sequence)
    existing = {
        _text(group.get("anchorSymbol")).upper(): _compact_candidate_group(group)
        for group in groups
        if isinstance(group, dict) and _text(group.get("anchorSymbol"))
    }
    result: list[dict[str, Any]] = []
    for anchor in sorted(set(anchors) | set(existing) | set(by_anchor)):
        group = dict(existing.get(anchor) or {"anchorSymbol": anchor})
        rows = by_anchor.get(anchor) or []
        group["observationSequenceCount"] = len(rows)
        group["observationSequencePreview"] = [
            row.get("pathText") or row.get("pool") or "" for row in rows[:6]
        ]
        group["observationReachable"] = bool(rows)
        if rows:
            group.pop("observationEmptyReason", None)
        elif group.get("observationEmptyReason") is not None:
            group["observationEmptyReason"] = group.get("observationEmptyReason")
        group["observationState"] = {
            **(group.get("observationState") or {}),
            "status": "reachable" if rows else "no_observation_sequence",
            "reachable": bool(rows),
            "groupCount": len(rows),
        }
        group["observationCombinationCount"] = len(rows)
        group["observationCombinationPreview"] = group["observationSequencePreview"]
        result.append(group)
    return result


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


def merge_market_observation_static(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {
        **existing,
        **incoming,
        "first_generated_at": existing.get("first_generated_at") or existing.get("generated_at") or incoming.get("generated_at"),
        "generated_at": _stamp(),
        "refresh_mode": "incremental",
        "previous_path": existing.get("path"),
    }
    # Drop historical diagnostic exports from legacy snapshots. Otherwise an
    # incremental refresh would preserve the old full V4 pool universe.
    for key in LIST_KEYS:
        if key not in PERSISTED_LIST_KEYS:
            merged.pop(key, None)
    for key in DICT_KEYS:
        if key not in PERSISTED_DICT_KEYS:
            merged.pop(key, None)
    for key in PERSISTED_LIST_KEYS:
        existing_rows = existing.get(key) if isinstance(existing.get(key), list) else []
        incoming_rows = incoming.get(key) if isinstance(incoming.get(key), list) else []
        if existing_rows or incoming_rows:
            merged[key] = _merge_list_values(existing_rows, incoming_rows, key=key)
    merged["list_counts"] = _list_counts(merged)
    return merged


def write_market_observation_static(
    observation: dict[str, Any],
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    refresh_mode: str = "incremental",
    root: Path | str | None = None,
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
) -> dict[str, Any]:
    path = market_observation_static_path(network=network, chain_id=chain_id, protocols=protocols, root=root)
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
    payload["summary"].setdefault("observationAnchorCount", len(payload.get("observationAnchorSymbols") or []))
    payload["summary"].setdefault("borrowableTokenCount", len(payload.get("borrowableSymbols") or []))
    payload["summary"].setdefault("candidateObservationGroupCount", len(payload.get("candidateObservationGroups") or []))
    payload["summary"].setdefault("observationSequenceCount", len(payload.get("observationSequences") or []))
    payload["summary"].setdefault("closedLoopRouteCount", len(payload.get("closedLoopRoutes") or []))
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
