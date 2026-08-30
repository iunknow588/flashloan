from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_ROOT = APP_ROOT / "runtime" / "market_observation"
SCHEMA_VERSION = "market-observation-static-store-v1"

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
)

DICT_KEYS = (
    "bridgePoolIndexLookup",
    "chainSpecificObservation",
    "dexSettlement",
    "poolStaticTable",
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


def _list_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(payload.get(key) or [])
        for key in LIST_KEYS
        if isinstance(payload.get(key), list)
    }


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
    for key in DICT_KEYS:
        value = observation.get(key)
        if isinstance(value, dict):
            snapshot[key] = value
    for key in LIST_KEYS:
        value = observation.get(key)
        if isinstance(value, list):
            snapshot[key] = value
    snapshot["list_counts"] = _list_counts(snapshot)
    return snapshot


def merge_market_observation_static(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {
        **existing,
        **incoming,
        "first_generated_at": existing.get("first_generated_at") or existing.get("generated_at") or incoming.get("generated_at"),
        "generated_at": _stamp(),
        "refresh_mode": "incremental",
        "previous_path": existing.get("path"),
    }
    for key in LIST_KEYS:
        existing_rows = [row for row in existing.get(key) or [] if isinstance(row, dict)]
        incoming_rows = [row for row in incoming.get(key) or [] if isinstance(row, dict)]
        if existing_rows or incoming_rows:
            merged[key] = _merge_rows(existing_rows, incoming_rows)
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
    payload["marketObservationSourceStage"] = payload.get("marketObservationSourceStage") or source
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
    "SCHEMA_VERSION",
    "load_market_observation_static",
    "market_observation_static_path",
    "market_observation_static_table",
    "market_observation_store_root",
    "merge_market_observation_static",
    "observation_payload_from_static",
    "static_snapshot_from_observation",
    "static_store_metadata",
    "write_market_observation_static",
]
