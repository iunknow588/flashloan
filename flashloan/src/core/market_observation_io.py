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
    "closedLoopSequences",
    "pairs",
    "bridgePairs",
    "directPools",
    "closedLoopRoutes",
    "indirectRoutes",
    "unmatchedPools",
    "chainSpecificObservationPairs",
    "chainSpecificObservationPools",
    "chainSpecificObservationTokens",
    "activePoolIds",
    "liquidPoolIds",
    "liquidPoolOutsideClosedLoopIds",
    "closedLoopRouteSummaries",
)

SHARDABLE_LIST_KEYS = (
    "anchorGroups",
    "candidateObservationGroups",
    "observationSequences",
    "closedLoopSequences",
    "pairs",
    "bridgePairs",
    "directPools",
    "closedLoopRoutes",
    "indirectRoutes",
    "unmatchedPools",
    "chainSpecificObservationPairs",
    "chainSpecificObservationPools",
    "chainSpecificObservationTokens",
    "closedLoopRouteSummaries",
)

SHARD_DIR = "shards"
SHARD_MANIFEST_FILE = "manifest.json"
SHARD_SCHEMA_VERSION = "market-observation-shard-v1"

PERSISTED_LIST_KEYS = (
    "borrowableSymbols",
    "observationAnchorSymbols",
    "candidateObservationGroups",
    "observationSequences",
    "closedLoopSequences",
    "pairs",
    "bridgePairs",
    "chainSpecificObservationPairs",
    "chainSpecificObservationPools",
    "activePoolIds",
    "liquidPoolIds",
    "liquidPoolOutsideClosedLoopIds",
)

DICT_KEYS = (
    "bridgePoolIndexLookup",
    "chainSpecificObservation",
    "dexSettlement",
    "flashLoanLiquidity",
    "liquidPoolRegistry",
    "poolRegistry",
    "poolStaticTable",
)

PERSISTED_DICT_KEYS = (
    "dexSettlement",
    "flashLoanLiquidity",
    "liquidPoolRegistry",
    "poolRegistry",
    "poolStaticTable",
)

OVERVIEW_LIST_KEYS = (
    "borrowableSymbols",
    "observationAnchorSymbols",
    "closedLoopRouteSummaries",
    "chainSpecificObservationPairs",
    "activePoolIds",
    "liquidPoolIds",
    "liquidPoolOutsideClosedLoopIds",
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


def _row_anchor_symbol(row: dict[str, Any], fallback: str = "") -> str:
    for key in ("anchorSymbol", "anchor", "fromSymbol", "tokenXSymbol", "token0Symbol"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.upper()
    return str(fallback or "").strip().upper()


def _row_token_addresses(row: dict[str, Any]) -> list[str]:
    addresses: list[str] = []
    for key in (
        "anchorAddress",
        "fromAddress",
        "tokenX",
        "token0",
        "counterAddress",
        "toAddress",
        "tokenY",
        "token1",
    ):
        text = str(row.get(key) or "").strip()
        if text.lower().startswith("0x") and len(text) == 42:
            addresses.append(text.lower())
    return sorted(set(addresses))


def _row_symbols(row: dict[str, Any]) -> list[str]:
    symbols: list[str] = []
    for key in ("anchorSymbol", "fromSymbol", "tokenXSymbol", "token0Symbol", "counterSymbol", "toSymbol", "tokenYSymbol", "token1Symbol"):
        text = str(row.get(key) or "").strip().upper()
        if text:
            symbols.append(text)
    return sorted(set(symbols))


def _safe_shard_name(value: str) -> str:
    text = "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in "._-")
    return text.strip("._-") or "unknown"


def _shard_manifest_path(payload_path: Path) -> Path:
    return _shard_root(payload_path) / SHARD_MANIFEST_FILE


def _shard_path_for_symbol(payload_path: Path, symbol: str) -> Path:
    return _shard_root(payload_path) / f"{_safe_shard_name(symbol)}.json"


def _shard_root(payload_path: Path) -> Path:
    """Return the shard directory for a payload file.

    ``observation.json`` and ``overview.json`` live in the same directory but
    must never share shard files: writing one would overwrite the other's
    per-anchor rows.  The directory name is derived from the payload filename
    so both files keep independent compact indexes and manifests.
    """
    stem = payload_path.stem or "payload"
    return payload_path.parent / f"{SHARD_DIR}-{stem}"


def _resolve_shard_path(raw_path: str, payload_path: Path) -> Path:
    """Resolve a shard path recorded in a compact payload's shardRef.

    Writers may store an absolute path or a root-relative path.  Both are
    accepted here; a path that does not exist yet is also checked relative to
    the payload directory so mixed layouts keep loading.
    """
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    root_relative = Path.cwd() / candidate
    if root_relative.exists():
        return root_relative
    payload_relative = payload_path.parent / candidate
    return payload_relative


def _split_rows_by_shard_key(rows: list[dict[str, Any]], *, key: str) -> dict[str, list[dict[str, Any]]]:
    """Group persisted list rows by the shard key used by the static store.

    The first/third-hop rows are anchored by ``anchorSymbol``; y-z bridge
    rows may not touch a configured anchor, so their anchor fallback uses the
    lower-symbol side of the pair to keep every pool row in exactly one shard.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _row_anchor_symbol(row)
        if not symbol and key == "closedLoopRouteSummaries":
            # Overview summaries carry no anchorSymbol after compaction in some
            # existing payloads; anchor them by path/address/symbol rather than
            # dropping them into the unknown bucket.
            symbol = str(row.get("pathText") or "").split("->")[0].strip().upper() or ""
            if not symbol and row.get("routeAddresses"):
                symbol = str(row.get("routeAddresses")[0] or "").strip()
            if not symbol and row.get("pathSymbols"):
                symbol = str(row.get("pathSymbols")[0] or "").strip().upper()
        if not symbol:
            symbols = _row_symbols(row)
            symbol = symbols[0] if symbols else ""
        if not symbol:
            symbol = "unknown"
        grouped.setdefault(symbol, []).append(row)
    return {symbol: rows for symbol, rows in sorted(grouped.items()) if rows}


def _split_payload_shards(
    payload: dict[str, Any],
    payload_path: Path,
    *,
    network: str,
    chain_id: int,
    protocols: list[str],
) -> list[dict[str, Any]]:
    """Return per-anchor shard payloads for the large row lists.

    Dict keys that are not naturally anchored (poolRegistry, poolStaticTable,
    flashLoanLiquidity) stay in the compact main file and are not split.
    """
    by_symbol: dict[str, dict[str, Any]] = {}
    for key in SHARDABLE_LIST_KEYS:
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for symbol, symbol_rows in _split_rows_by_shard_key(rows, key=key).items():
            shard = by_symbol.setdefault(
                symbol,
                {
                    "schemaVersion": SHARD_SCHEMA_VERSION,
                    "network": network,
                    "chainId": int(chain_id),
                    "protocol_key": _protocol_key(protocols),
                    "anchorSymbol": symbol,
                    "source": payload.get("source") or payload.get("marketObservationSource") or "",
                    "generatedAt": payload.get("generated_at") or payload.get("generatedAt"),
                    "path": str(_shard_path_for_symbol(payload_path, symbol)),
                    "rows": {},
                },
            )
            shard["rows"][key] = symbol_rows
    return [shard for shard in by_symbol.values() if shard["rows"]]


def _hydrate_payload_from_shards(
    payload: dict[str, Any],
    payload_path: Path,
    *,
    network: str,
    chain_id: int,
    protocols: list[str],
) -> dict[str, Any]:
    """Hydrate the legacy in-memory schema from a compact main file plus shards."""
    if not payload.get("shardRef"):
        return payload
    hydrated = dict(payload)
    shard_records = [
        item
        for item in (payload.get("shardRef") or {}).get("shards") or []
        if isinstance(item, dict)
    ]
    for record in shard_records:
        raw_path = record.get("path") or ""
        if not raw_path:
            continue
        shard_path = _resolve_shard_path(raw_path, payload_path)
        shard = _read_json(shard_path)
        if not shard:
            continue
        for key, rows in (shard.get("rows") or {}).items():
            if not isinstance(rows, list):
                continue
            existing = hydrated.get(key)
            if not isinstance(existing, list):
                existing = []
            hydrated[key] = [*existing, *rows]
    for key in SHARDABLE_LIST_KEYS:
        if key not in hydrated and any(
            key in (record.get("rowKeys") or [])
            for record in shard_records
        ):
            hydrated[key] = []
    hydrated["list_counts"] = _list_counts_io(hydrated)
    return hydrated


def _list_counts_io(payload: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(value)
        for key, value in payload.items()
        if key in LIST_KEYS and isinstance(value, list)
    }
