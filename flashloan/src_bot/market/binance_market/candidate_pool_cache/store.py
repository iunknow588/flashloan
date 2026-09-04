from __future__ import annotations

import hashlib
import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.chain_data_registry import global_chain_id
from core.contracts import validate_scoped_payload


APP_ROOT = Path(__file__).resolve().parents[3]
CACHE_ROOT = APP_ROOT / "runtime" / "cache" / "candidate_pools"
MANIFEST_FILE = "manifest.json"
SNAPSHOT_FILE = "snapshot.json"
SHARDS_DIR = "shards"
MANIFEST_SCHEMA_VERSION = "candidate-pool-cache-manifest-v1"
SNAPSHOT_SCHEMA_VERSION = "candidate-pool-cache-snapshot-v1"
SHARD_SCHEMA_VERSION = "candidate-pool-cache-shard-v1"

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe_token_key(symbol: object) -> str:
    text = _text(symbol).lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "_", text)
    return normalized.strip("._-") or "unknown"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp_path.replace(path)


@lru_cache(maxsize=32)
def _load_snapshot_cached(path_signature: tuple[str, int, int]) -> dict[str, Any] | None:
    path = Path(path_signature[0])
    return _read_json(path)


def _cache_key(network: str, chain_id: int) -> str:
    return f"{_text(network).lower()}:{int(chain_id)}"


def _cache_lock(network: str, chain_id: int) -> threading.Lock:
    key = _cache_key(network, chain_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def candidate_pool_cache_paths(
    *,
    network: str,
    chain_id: int,
    cache_root: Path | None = None,
) -> dict[str, Path]:
    scope_root = (cache_root or CACHE_ROOT) / _text(network).lower() / f"chain-{int(chain_id)}"
    return {
        "root": scope_root,
        "manifest": scope_root / MANIFEST_FILE,
        "snapshot": scope_root / SNAPSHOT_FILE,
        "shards": scope_root / SHARDS_DIR,
    }


def _shard_payload(
    payload: dict[str, Any],
    group: dict[str, Any],
    *,
    network: str,
    chain_id: int,
    anchor_symbol: str,
) -> dict[str, Any]:
    now = payload.get("candidatePoolCache", {}).get("refreshedAt")
    shard = {
        "schemaVersion": SHARD_SCHEMA_VERSION,
        "network": _text(network).lower(),
        "chainId": int(chain_id),
        "globalChainId": global_chain_id(chain_id=chain_id, network=network),
        "anchorSymbol": anchor_symbol,
        "generatedAt": payload.get("generatedAt"),
        "source": payload.get("source"),
        "freshness": payload.get("freshness"),
        "fallbackReason": payload.get("fallback_reason"),
        "refreshedAt": now,
        "summary": {
            "ycPoolCount": group.get("ycPoolCount"),
            "zcPoolCount": group.get("zcPoolCount"),
            "ycTokenCount": group.get("ycTokenCount"),
            "zcTokenCount": group.get("zcTokenCount"),
            "candidatePoolCount": group.get("candidatePoolCount"),
            "candidateQueueCount": group.get("candidateQueueCount"),
        },
        "anchorGroup": group,
    }
    shard["fingerprint"] = _hash_payload(shard)
    return shard


def _candidate_pool_groups(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("anchorGroups")
    if isinstance(groups, list):
        return [group for group in groups if isinstance(group, dict)]
    return []


def _snapshot_payload(
    payload: dict[str, Any],
    *,
    network: str,
    chain_id: int,
    cache_paths: dict[str, Path],
    refresh_mode: str,
    force_full: bool,
) -> dict[str, Any]:
    groups = _candidate_pool_groups(payload)
    token_symbols = [str(group.get("anchorSymbol") or "").strip().upper() for group in groups if str(group.get("anchorSymbol") or "").strip()]
    cache_payload = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "network": _text(network).lower(),
        "chainId": int(chain_id),
        "globalChainId": global_chain_id(chain_id=chain_id, network=network),
        "snapshotPath": str(cache_paths["snapshot"]),
        "manifestPath": str(cache_paths["manifest"]),
        "shardDir": str(cache_paths["shards"]),
        "tokenCount": len(token_symbols),
        "tokenSymbols": token_symbols,
        "refreshMode": refresh_mode,
        "forceFull": bool(force_full),
        "refreshedAt": None,
    }
    return {**payload, "candidatePoolCache": cache_payload}


def _refresh_manifest(
    *,
    network: str,
    chain_id: int,
    cache_paths: dict[str, Path],
    snapshot: dict[str, Any],
    shards: list[dict[str, Any]],
    refresh_mode: str,
    force_full: bool,
) -> dict[str, Any]:
    now = snapshot.get("candidatePoolCache", {}).get("refreshedAt")
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "network": _text(network).lower(),
        "chainId": int(chain_id),
        "globalChainId": global_chain_id(chain_id=chain_id, network=network),
        "snapshotPath": str(cache_paths["snapshot"]),
        "shardDir": str(cache_paths["shards"]),
        "refreshMode": refresh_mode,
        "forceFull": bool(force_full),
        "refreshedAt": now,
        "generatedAt": snapshot.get("generatedAt"),
        "source": snapshot.get("source"),
        "fallbackReason": snapshot.get("fallback_reason"),
        "snapshotFingerprint": _hash_payload(snapshot),
        "shards": [
            {
                "anchorSymbol": shard["anchorSymbol"],
                "path": str(cache_paths["shards"] / f"{shard['tokenKey']}.json"),
                "fingerprint": shard["fingerprint"],
                "ycPoolCount": shard["payload"].get("summary", {}).get("ycPoolCount"),
                "zcPoolCount": shard["payload"].get("summary", {}).get("zcPoolCount"),
            }
            for shard in shards
        ],
    }
    return manifest


def load_candidate_pool_snapshot(
    *,
    network: str,
    chain_id: int,
    cache_root: Path | None = None,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    cache_paths = candidate_pool_cache_paths(network=network, chain_id=chain_id, cache_root=cache_root)
    snapshot_path = cache_paths["snapshot"]
    if not snapshot_path.exists():
        return None
    try:
        stat = snapshot_path.stat()
    except OSError:
        return None
    snapshot = _load_snapshot_cached((str(snapshot_path), int(stat.st_mtime_ns), int(stat.st_size)))
    if snapshot is not None and expected_scope is not None:
        validate_scoped_payload(snapshot, expected_scope)
    return snapshot


def refresh_candidate_pool_cache(
    payload: dict[str, Any],
    *,
    network: str,
    chain_id: int,
    cache_root: Path | None = None,
    force_full: bool = False,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cache_paths = candidate_pool_cache_paths(network=network, chain_id=chain_id, cache_root=cache_root)
    lock = _cache_lock(network, chain_id)
    with lock:
        snapshot = _snapshot_payload(
            payload,
            network=network,
            chain_id=chain_id,
            cache_paths=cache_paths,
            refresh_mode="full" if force_full else "incremental",
            force_full=force_full,
        )
        if expected_scope is not None:
            validate_scoped_payload({"scope": expected_scope, **snapshot}, expected_scope)
            snapshot["scope"] = dict(expected_scope)
        snapshot["candidatePoolCache"]["refreshedAt"] = snapshot.get("generatedAt") or snapshot.get("freshness")
        groups = _candidate_pool_groups(snapshot)
        shard_records: list[dict[str, Any]] = []
        for group in groups:
            anchor_symbol = str(group.get("anchorSymbol") or "").strip().upper()
            if not anchor_symbol:
                continue
            shard_payload = _shard_payload(
                snapshot,
                group,
                network=network,
                chain_id=chain_id,
                anchor_symbol=anchor_symbol,
            )
            shard_records.append(
                {
                    "anchorSymbol": anchor_symbol,
                    "tokenKey": _safe_token_key(anchor_symbol),
                    "payload": shard_payload,
                    "fingerprint": shard_payload["fingerprint"],
                }
            )

        cache_paths["root"].mkdir(parents=True, exist_ok=True)
        cache_paths["shards"].mkdir(parents=True, exist_ok=True)
        previous_manifest = _read_json(cache_paths["manifest"]) or {}
        previous_shards = {
            str(item.get("anchorSymbol") or "").strip().upper(): item
            for item in previous_manifest.get("shards") or []
            if isinstance(item, dict)
        }
        snapshot_fingerprint = _hash_payload(snapshot)
        for shard in shard_records:
            existing = previous_shards.get(shard["anchorSymbol"], {})
            if force_full or existing.get("fingerprint") != shard["fingerprint"]:
                _write_json(cache_paths["shards"] / f"{shard['tokenKey']}.json", shard["payload"])
        stale_symbols = set(previous_shards) - {shard["anchorSymbol"] for shard in shard_records}
        for stale_symbol in stale_symbols:
            stale_path = cache_paths["shards"] / f"{_safe_token_key(stale_symbol)}.json"
            try:
                stale_path.unlink()
            except OSError:
                pass

        if force_full or previous_manifest.get("snapshotFingerprint") != snapshot_fingerprint or not cache_paths["snapshot"].exists():
            _write_json(cache_paths["snapshot"], snapshot)
        manifest = _refresh_manifest(
            network=network,
            chain_id=chain_id,
            cache_paths=cache_paths,
            snapshot=snapshot,
            shards=shard_records,
            refresh_mode="full" if force_full else "incremental",
            force_full=force_full,
        )
        _write_json(cache_paths["manifest"], manifest)
        return snapshot


__all__ = [
    "CACHE_ROOT",
    "MANIFEST_FILE",
    "SNAPSHOT_FILE",
    "candidate_pool_cache_paths",
    "load_candidate_pool_snapshot",
    "refresh_candidate_pool_cache",
]
