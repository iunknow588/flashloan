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
    "poolRegistry",
    "poolStaticTable",
)

PERSISTED_DICT_KEYS = (
    "dexSettlement",
    "flashLoanLiquidity",
    "poolRegistry",
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
