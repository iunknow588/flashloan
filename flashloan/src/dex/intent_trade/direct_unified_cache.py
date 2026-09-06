"""ABI, JSON, registry, and runtime cache helpers for unified direct execution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dex.intent_trade.direct_unified_reserve import aave_reserve_assets

_UNIFIED_EXECUTOR_ABI_CACHE: list[dict[str, Any]] | None = None


def _impl():
    from dex.intent_trade import direct_impl

    return direct_impl


def unified_executor_artifact_path() -> Path:
    impl = _impl()
    configured = impl._env(
        "UNIFIED_EXECUTOR_ARTIFACT",
        "TRIANGULAR_UNIFIED_EXECUTOR_ARTIFACT",
        default="",
    )
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else impl.SRC_ROOT / path
    return (
        impl.SRC_ROOT.parents[1]
        / "contract"
        / "contracts-dex"
        / "artifacts"
        / "src"
        / "UnifiedFlashLoanMevExecutor.sol"
        / "UnifiedFlashLoanMevExecutor.json"
    )


def load_unified_executor_abi() -> list[dict[str, Any]]:
    global _UNIFIED_EXECUTOR_ABI_CACHE
    if _UNIFIED_EXECUTOR_ABI_CACHE is not None:
        return _UNIFIED_EXECUTOR_ABI_CACHE
    path = unified_executor_artifact_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        abi = payload.get("abi")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"UnifiedFlashLoanMevExecutor ABI artifact is unavailable: {path}") from exc
    if not isinstance(abi, list) or not abi:
        raise RuntimeError(f"UnifiedFlashLoanMevExecutor ABI artifact is invalid: {path}")
    _UNIFIED_EXECUTOR_ABI_CACHE = abi
    return abi


def unified_token_registry_path() -> Path:
    impl = _impl()
    raw = impl._env(
        "UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE",
        "TRIANGULAR_TOKEN_REGISTRY_FILE",
        default="runtime/config/unified_token_registry.json",
    )
    path = Path(raw)
    return path if path.is_absolute() else impl.SRC_ROOT / path


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_unified_token_registry(path: Path | None = None) -> dict[str, Any]:
    impl = _impl()
    payload = read_json_file(path or unified_token_registry_path())
    if int(payload.get("schemaVersion") or 0) != 1:
        return {}
    tokens = payload.get("tokens")
    if not isinstance(tokens, list):
        return {}
    expected_hash = (
        impl._env("UNIFIED_EXECUTOR_TOKEN_REGISTRY_HASH", "TRIANGULAR_TOKEN_REGISTRY_HASH")
        or str(
            payload.get("canonicalHash")
            or payload.get("canonical_hash")
            or payload.get("registryHash")
            or payload.get("registry_hash")
            or ""
        )
    ).strip()
    hash_payload = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "canonicalHash",
            "canonical_hash",
            "registryHash",
            "registry_hash",
            "signature",
            "signatures",
        }
    }
    actual_hash = canonical_json_hash(hash_payload)
    payload["_canonicalHash"] = actual_hash
    payload["_integrityRequired"] = bool(expected_hash)
    payload["_integrityOk"] = not expected_hash or expected_hash == actual_hash
    if expected_hash:
        payload["_expectedCanonicalHash"] = expected_hash
    return payload


def runtime_pool_cache_path() -> Path:
    impl = _impl()
    raw = impl._env(
        "TRIANGULAR_RUNTIME_POOL_CACHE_FILE",
        "PINAX_POOL_DISCOVERY_CACHE_FILE",
        default="runtime/cache/avalanche_l1_pool_static_table.json",
    )
    path = Path(raw)
    return path if path.is_absolute() else impl.SRC_ROOT / path


def load_runtime_pool_cache(path: Path | None = None) -> dict[str, Any]:
    return read_json_file(path or runtime_pool_cache_path())


def aave_reserve_cache_path() -> Path:
    impl = _impl()
    raw = impl._env("AAVE_RESERVE_CACHE_FILE", default="runtime/cache/aave_reserve_assets.json")
    path = Path(raw)
    return path if path.is_absolute() else impl.SRC_ROOT / path


def direct_circuit_breaker_path() -> Path:
    impl = _impl()
    raw = impl._env(
        "DIRECT_ONCHAIN_CIRCUIT_BREAKER_FILE",
        "UNIFIED_EXECUTOR_CIRCUIT_BREAKER_FILE",
        default="runtime/cache/direct_onchain_circuit_breaker.json",
    )
    path = Path(raw)
    return path if path.is_absolute() else impl.SRC_ROOT / path


def direct_pre_pause_path() -> Path:
    impl = _impl()
    raw = impl._env(
        "DIRECT_ONCHAIN_PRE_PAUSE_FILE",
        "UNIFIED_EXECUTOR_PRE_PAUSE_FILE",
        default="runtime/cache/direct_onchain_pre_pause.json",
    )
    path = Path(raw)
    return path if path.is_absolute() else impl.SRC_ROOT / path


def load_aave_reserve_cache(path: Path | None = None) -> dict[str, Any]:
    return read_json_file(path or aave_reserve_cache_path())


def cache_observed_block(cache: dict[str, Any]) -> int:
    impl = _impl()
    for key in (
        "block_number",
        "blockNumber",
        "observed_block",
        "observedBlock",
        "latest_block",
        "latestBlock",
    ):
        try:
            value = impl._positive_int_value(cache.get(key))
        except ValueError:
            value = 0
        if value:
            return value
    return 0


def max_runtime_cache_age_blocks(protocol: dict[str, Any] | None = None) -> int:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        names=("max_cache_age_blocks", "maxCacheAgeBlocks", "runtime_cache_max_age_blocks"),
    )
    if raw in (None, ""):
        raw = impl._env("TRIANGULAR_RUNTIME_CACHE_MAX_AGE_BLOCKS", "UNIFIED_EXECUTOR_CACHE_MAX_AGE_BLOCKS")
    try:
        return impl._positive_int_value(raw, default=impl.DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS)
    except ValueError:
        return impl.DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS


def runtime_cache_validation(
    cache: dict[str, Any],
    *,
    network: str,
    current_block: int | None = None,
    max_age_blocks: int | None = None,
    required_factory: str = "",
) -> dict[str, Any]:
    impl = _impl()
    if not isinstance(cache, dict) or not cache:
        return {"ok": False, "reason": "cache_missing"}
    normalized_network = impl._normalize_direct_onchain_network(network)
    expected_chain_id = impl.DIRECT_ONCHAIN_NETWORKS.get(normalized_network, {}).get("chain_id")
    cache_chain_id = cache.get("chain_id", cache.get("chainId"))
    if expected_chain_id is not None:
        try:
            if int(cache_chain_id) != int(expected_chain_id):
                return {
                    "ok": False,
                    "reason": "cache_chain_id_mismatch",
                    "expectedChainId": str(expected_chain_id),
                    "cacheChainId": str(cache_chain_id),
                }
        except (TypeError, ValueError):
            return {
                "ok": False,
                "reason": "cache_chain_id_missing",
                "expectedChainId": str(expected_chain_id),
            }
    observed_block = cache_observed_block(cache)
    max_age = impl.DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS if max_age_blocks is None else int(max_age_blocks)
    if current_block is not None and max_age > 0:
        if observed_block <= 0:
            return {"ok": False, "reason": "cache_block_missing", "currentBlock": str(current_block)}
        age = int(current_block) - observed_block
        if age < 0:
            return {
                "ok": False,
                "reason": "cache_block_ahead",
                "currentBlock": str(current_block),
                "cacheBlock": str(observed_block),
            }
        if age > max_age:
            return {
                "ok": False,
                "reason": "cache_stale",
                "currentBlock": str(current_block),
                "cacheBlock": str(observed_block),
                "maxAgeBlocks": str(max_age),
                "ageBlocks": str(age),
            }
    expected_factory = impl._normalize_pair_address(required_factory)
    checked = 0
    if expected_factory:
        cache_factory = impl._normalize_pair_address(cache.get("factory") or cache.get("factory_address"))
        if cache_factory and cache_factory != expected_factory:
            return {
                "ok": False,
                "reason": "cache_factory_mismatch",
                "expectedFactory": expected_factory,
                "cacheFactory": cache_factory,
            }
        mismatches: list[str] = []
        for entry in cache.get("pools") or []:
            if not isinstance(entry, dict):
                continue
            nested = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
            for pool in nested:
                if not isinstance(pool, dict):
                    continue
                pool_address = impl._normalize_pair_address(pool.get("pool") or pool.get("pool_address") or pool.get("address"))
                entry_factory = impl._normalize_pair_address(
                    pool.get("factory")
                    or pool.get("factory_address")
                    or entry.get("factory")
                    or entry.get("factory_address")
                )
                if not pool_address and not entry_factory:
                    continue
                checked += 1
                if entry_factory and entry_factory != expected_factory:
                    mismatches.append(pool_address or f"entry:{checked}")
                    if len(mismatches) >= 3:
                        break
            if len(mismatches) >= 3:
                break
        if mismatches:
            return {
                "ok": False,
                "reason": "cache_factory_mismatch",
                "expectedFactory": expected_factory,
                "samplePools": mismatches,
            }
    return {
        "ok": True,
        "reason": "cache_verified",
        "cacheBlock": str(observed_block),
        "maxAgeBlocks": str(max_age),
        "currentBlock": str(current_block or ""),
        "factoryCheckedCount": str(checked) if expected_factory else "0",
    }


def unified_required_factory(protocol: dict[str, Any] | None, cache: dict[str, Any] | None = None) -> str:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        names=("factory", "factory_address", "v3_factory", "v3Factory", "unified_v3_factory"),
    )
    if raw in (None, ""):
        raw = impl._env("UNIFIED_V3_FACTORY", "TRIANGULAR_V3_FACTORY")
    if raw in (None, "") and isinstance(cache, dict):
        raw = cache.get("factory") or cache.get("factory_address")
    return impl._normalize_pair_address(raw)


def reserve_cache_validation(
    cache: dict[str, Any],
    *,
    network: str,
    current_block: int | None = None,
    max_age_blocks: int | None = None,
) -> dict[str, Any]:
    impl = _impl()
    result = runtime_cache_validation(
        cache,
        network=network,
        current_block=current_block,
        max_age_blocks=max_age_blocks,
    )
    if not result.get("ok"):
        result["cacheKind"] = "aave_reserve"
        return result
    assets = aave_reserve_assets(cache)
    if not assets:
        return {"ok": False, "reason": "reserve_cache_assets_missing", "cacheKind": "aave_reserve"}
    result["cacheKind"] = "aave_reserve"
    result["assetCount"] = str(len(assets))
    return result
