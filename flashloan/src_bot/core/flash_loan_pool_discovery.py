from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.market_config import supported_market_summaries
from core.robinhood_flash_liquidity import load_robinhood_flash_liquidity_snapshot


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]
DEFAULT_SNAPSHOT_PATH = APP_ROOT / "runtime" / "cache" / "flash_loan_pool_discovery_snapshot.json"
DEFAULT_AAVE_CACHE_PATH = APP_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"
DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH = (
    REPO_ROOT / "data_info" / "BNB" / "liquidation" / "venus_market_static_table.json"
)
DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"
DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"

PROTOCOL_ORDER = {
    "morpho_blue": 0,
    "venus_core": 1,
    "aave_v3": 2,
    "uniswap_v2": 3,
    "uniswap_v3": 4,
    "uniswap_v4": 5,
    "pancakeswap_v3": 6,
    "pancakeswap_v2": 7,
    "biswap_v2": 8,
    "quickswap_v2": 9,
    "traderjoe_v2": 10,
    "balancer_v2": 11,
    "curve": 12,
}
PROTOCOL_LABELS = {
    "aave_v3": "Aave V3",
    "venus_core": "Venus Core",
    "morpho_blue": "Morpho Blue",
    "uniswap_v2": "Uniswap V2",
    "uniswap_v3": "Uniswap V3",
    "uniswap_v4": "Uniswap V4",
    "pancakeswap_v2": "PancakeSwap V2",
    "pancakeswap_v3": "PancakeSwap V3",
    "biswap_v2": "Biswap V2",
    "quickswap_v2": "QuickSwap V2",
    "traderjoe_v2": "Trader Joe V2",
    "balancer_v2": "Balancer V2",
    "curve": "Curve",
}
SUPPORTED_FLASH_POOL_PROTOCOLS = set(PROTOCOL_LABELS)
AVALANCHE_V2_DEX_PROTOCOLS = {
    "trader_joe_v1": "traderjoe_v2",
    "trader_joe_v2": "traderjoe_v2",
    "traderjoe_v2": "traderjoe_v2",
}
DEFAULT_CHAIN_PROTOCOL_CANDIDATES = {
    56: {"network": "bnb", "protocols": ["aave_v3", "venus_core", "pancakeswap_v3", "pancakeswap_v2", "biswap_v2", "uniswap_v4"]},
    43114: {"network": "avalanche", "protocols": ["aave_v3", "uniswap_v3", "uniswap_v4", "traderjoe_v2", "curve"]},
    137: {"network": "polygon", "protocols": ["aave_v3", "uniswap_v3", "uniswap_v4", "quickswap_v2", "balancer_v2"]},
    4663: {"network": "robinhood", "protocols": ["morpho_blue", "uniswap_v2", "uniswap_v3", "uniswap_v4", "aave_v3"]},
    1: {"network": "ethereum", "protocols": ["aave_v3", "uniswap_v3", "uniswap_v4", "uniswap_v2", "balancer_v2"]},
}

_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else default


def _protocol_label(protocol: str) -> str:
    normalized = str(protocol or "").strip().lower()
    return PROTOCOL_LABELS.get(normalized, normalized.replace("_", " ").title())


def _chain_key(chain_id: int | None, network: str) -> str:
    return f"chain:{int(chain_id)}" if chain_id is not None else f"network:{network}"


def _summary(
    *,
    chain_id: int | None,
    network: str,
    protocol: str,
    pool_count: int,
    borrowable_pool_count: int,
    asset_count: int,
    depth_score_usd: float,
    source: str,
    refreshed_at: object = None,
    raw_snapshot_key: str | None = None,
) -> dict[str, Any]:
    return {
        "chain_id": chain_id,
        "network": network,
        "chain_key": _chain_key(chain_id, network),
        "protocol": protocol,
        "display_name": _protocol_label(protocol),
        "pool_count": int(pool_count),
        "borrowable_pool_count": int(borrowable_pool_count),
        "asset_count": int(asset_count),
        "depth_score_usd": float(depth_score_usd or 0),
        "source": source,
        "refreshed_at": refreshed_at,
        "raw_snapshot_key": raw_snapshot_key,
    }


def _aave_summary(path: Path | None) -> dict[str, Any] | None:
    cache = _read_json(path)
    assets = [item for item in (cache or {}).get("assets") or [] if isinstance(item, dict)]
    if not cache or not assets:
        return None
    chain_id = _as_int(cache.get("chain_id") or cache.get("chainId"))
    return _summary(
        chain_id=chain_id or None,
        network={43114: "avalanche", 56: "bnb", 1: "ethereum", 137: "polygon"}.get(chain_id, "unknown"),
        protocol="aave_v3",
        pool_count=len(assets),
        borrowable_pool_count=sum(1 for item in assets if _as_float(item.get("available_liquidity")) > 0),
        asset_count=len({str(item.get("token_symbol") or "").upper() for item in assets if item.get("token_symbol")}),
        depth_score_usd=sum(_as_float(item.get("depth_score_usd")) for item in assets),
        source=str(path or ""),
        refreshed_at=cache.get("refreshed_at"),
    )


def _venus_summary(path: Path | None) -> dict[str, Any] | None:
    cache = _read_json(path)
    markets = [
        item
        for item in (cache or {}).get("markets") or []
        if isinstance(item, dict) and item.get("is_listed") is not False
    ]
    if not cache or not markets:
        return None
    chain_id = _as_int(cache.get("chain_id") or cache.get("chainId") or 56, 56)
    borrowable = 0
    for market in markets:
        if market.get("borrow_disabled"):
            continue
        if _as_float(market.get("cash")) > 0:
            borrowable += 1
    return _summary(
        chain_id=chain_id,
        network="bnb" if chain_id == 56 else "unknown",
        protocol="venus_core",
        pool_count=len(markets),
        borrowable_pool_count=borrowable,
        asset_count=len(markets),
        depth_score_usd=0.0,
        source=str(path or ""),
        refreshed_at=cache.get("generated_at"),
    )


def _dex_cache_summaries(
    *,
    path: Path | None,
    chain_id: int,
    network: str,
    protocol_field: str,
    protocol_map: dict[str, str] | None = None,
    drop_unmapped: bool = False,
) -> list[dict[str, Any]]:
    cache = _read_json(path)
    if not cache:
        return []
    cache_chain_id = _as_int(cache.get("chainId") or cache.get("chain_id") or chain_id, chain_id)
    if cache_chain_id != int(chain_id):
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pool in cache.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        raw_protocol = str(pool.get(protocol_field) or "").strip().lower()
        if drop_unmapped and protocol_map is not None and raw_protocol not in protocol_map:
            continue
        protocol = (protocol_map or {}).get(raw_protocol, raw_protocol)
        if not protocol or protocol not in SUPPORTED_FLASH_POOL_PROTOCOLS:
            continue
        grouped.setdefault(protocol, []).append(pool)
    summaries: list[dict[str, Any]] = []
    for protocol, pools in grouped.items():
        available = [pool for pool in pools if _as_float(pool.get("tvlUsd") or pool.get("token0Usd") or pool.get("token1Usd")) > 0]
        if not available:
            continue
        summaries.append(
            _summary(
                chain_id=chain_id,
                network=network,
                protocol=protocol,
                pool_count=len(available),
                borrowable_pool_count=len(available),
                asset_count=len(available),
                depth_score_usd=sum(_as_float(pool.get("tvlUsd")) for pool in available),
                source=str(path or ""),
                refreshed_at=cache.get("generatedAt") or cache.get("fetched_at"),
            )
        )
    return summaries


def _avalanche_v3_summaries(path: Path | None) -> list[dict[str, Any]]:
    cache = _read_json(path)
    if not cache:
        return []
    pools: list[dict[str, Any]] = []
    for pair in cache.get("pools") or []:
        if not isinstance(pair, dict):
            continue
        for pool in pair.get("pools") or pair.get("executionPools") or []:
            if isinstance(pool, dict) and _as_float(pool.get("tvlUsd")) > 0:
                pools.append(pool)
    if not pools:
        return []
    return [
        _summary(
            chain_id=43114,
            network="avalanche",
            protocol="uniswap_v3",
            pool_count=len(pools),
            borrowable_pool_count=len(pools),
            asset_count=len(pools),
            depth_score_usd=sum(_as_float(pool.get("tvlUsd")) for pool in pools),
            source=str(path or ""),
            refreshed_at=cache.get("generatedAt"),
        )
    ]


def _robinhood_summary(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for source in snapshot.get("sources") or []:
        if not isinstance(source, dict):
            continue
        if not bool(source.get("flash_borrowable")):
            continue
        protocol = str(source.get("protocol") or "").strip().lower()
        if protocol:
            grouped.setdefault(protocol, []).append(source)
    summaries = [
        _summary(
            chain_id=4663,
            network="robinhood",
            protocol=protocol,
            pool_count=len(sources),
            borrowable_pool_count=sum(1 for source in sources if source.get("flash_borrowable")),
            asset_count=len(sources),
            depth_score_usd=sum(_as_float(source.get("depth_score_usd")) for source in sources),
            source="robinhood_flash_liquidity",
            refreshed_at=snapshot.get("generatedAt"),
            raw_snapshot_key="robinhood_flash_liquidity",
        )
        for protocol, sources in grouped.items()
    ]
    return summaries, {"robinhood_flash_liquidity": snapshot}


def _sorted_protocols(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            item.get("chain_id") is None,
            item.get("chain_id") or 0,
            PROTOCOL_ORDER.get(str(item.get("protocol") or ""), 1000),
            str(item.get("protocol") or ""),
        ),
    )


def _chain_protocol_candidates(
    market_rows: list[dict[str, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {
        int(chain_id): {"network": str(item["network"]), "protocols": list(item["protocols"])}
        for chain_id, item in DEFAULT_CHAIN_PROTOCOL_CANDIDATES.items()
    }
    for row in market_rows if market_rows is not None else supported_market_summaries():
        if not isinstance(row, dict) or not bool(row.get("enabled", True)):
            continue
        chain_id = row.get("chain_id")
        if not isinstance(chain_id, int):
            continue
        protocol = str(row.get("protocol") or "").strip().lower()
        if protocol not in SUPPORTED_FLASH_POOL_PROTOCOLS:
            continue
        chain = candidates.setdefault(
            int(chain_id),
            {
                "network": str(row.get("network") or "").strip().lower(),
                "protocols": [],
            },
        )
        if not chain.get("network") and row.get("network"):
            chain["network"] = str(row.get("network") or "").strip().lower()
        protocols = chain.setdefault("protocols", [])
        if protocol not in protocols:
            protocols.append(protocol)
    for chain in candidates.values():
        chain["protocols"] = [
            item["protocol"]
            for item in _sorted_protocols([{"protocol": protocol} for protocol in chain.get("protocols") or []])
        ]
    return candidates


def _expected_removed_protocols(
    *,
    present: set[tuple[int | None, str]],
    existing_removed: list[dict[str, Any]],
    chain_protocol_candidates: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    removed_keys = {
        (item.get("chain_id"), str(item.get("protocol") or "").strip().lower())
        for item in existing_removed
    }
    removed: list[dict[str, Any]] = []
    for chain_id, chain in chain_protocol_candidates.items():
        for protocol in chain["protocols"]:
            key = (chain_id, protocol)
            if key in present or key in removed_keys:
                continue
            removed.append(
                _summary(
                    chain_id=chain_id,
                    network=str(chain["network"]),
                    protocol=protocol,
                    pool_count=0,
                    borrowable_pool_count=0,
                    asset_count=0,
                    depth_score_usd=0.0,
                    source="flash_loan_pool_discovery_candidate_scan",
                    refreshed_at=None,
                )
                | {"removed_reason": "protocol_pool_cache_not_available"}
            )
    return removed


def _scanned_chains(
    *,
    chains: list[dict[str, Any]],
    removed_protocols: list[dict[str, Any]],
    chain_protocol_candidates: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    chain_by_id = {item.get("chain_id"): item for item in chains}
    removed_by_chain: dict[int, list[dict[str, Any]]] = {}
    for item in removed_protocols:
        chain_id = item.get("chain_id")
        if isinstance(chain_id, int):
            removed_by_chain.setdefault(chain_id, []).append(item)
    scanned = []
    for chain_id, candidate in chain_protocol_candidates.items():
        available = chain_by_id.get(chain_id, {})
        scanned.append(
            {
                "chain_id": chain_id,
                "chain_key": _chain_key(chain_id, str(candidate["network"])),
                "network": candidate["network"],
                "candidate_protocols": list(candidate["protocols"]),
                "available_protocols": [item["protocol"] for item in available.get("protocols") or []],
                "removed_protocols": [
                    {
                        "protocol": item.get("protocol"),
                        "display_name": item.get("display_name"),
                        "removed_reason": item.get("removed_reason"),
                    }
                    for item in _sorted_protocols(removed_by_chain.get(chain_id, []))
                ],
                "pool_count": int(available.get("pool_count") or 0),
                "borrowable_pool_count": int(available.get("borrowable_pool_count") or 0),
            }
        )
    return scanned


def discover_flash_loan_pool_snapshot(
    *,
    aave_cache_path: Path | None = DEFAULT_AAVE_CACHE_PATH,
    venus_market_path: Path | None = DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH,
    bnb_dex_pool_static_path: Path | None = DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH,
    avalanche_v3_pool_static_path: Path | None = DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH,
    avalanche_v2_pool_discovery_path: Path | None = DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH,
    robinhood_loader: Callable[[], dict[str, Any]] | None = None,
    market_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    protocol_summaries: list[dict[str, Any]] = []
    raw_snapshots: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    chain_protocol_candidates = _chain_protocol_candidates(market_rows)

    for item in (_aave_summary(aave_cache_path), _venus_summary(venus_market_path)):
        if item:
            protocol_summaries.append(item)
    protocol_summaries.extend(
        _dex_cache_summaries(
            path=bnb_dex_pool_static_path,
            chain_id=56,
            network="bnb",
            protocol_field="dex",
        )
    )
    protocol_summaries.extend(_avalanche_v3_summaries(avalanche_v3_pool_static_path))
    protocol_summaries.extend(
        _dex_cache_summaries(
            path=avalanche_v2_pool_discovery_path,
            chain_id=43114,
            network="avalanche",
            protocol_field="dex",
            protocol_map=AVALANCHE_V2_DEX_PROTOCOLS,
            drop_unmapped=True,
        )
    )

    try:
        robinhood_snapshot = (robinhood_loader or load_robinhood_flash_liquidity_snapshot)()
        robinhood_summaries, robinhood_raw = _robinhood_summary(robinhood_snapshot)
        protocol_summaries.extend(robinhood_summaries)
        raw_snapshots.update(robinhood_raw)
    except Exception as exc:
        errors.append({"source": "robinhood_flash_liquidity", "error": str(exc)[:240]})

    removed_protocols: list[dict[str, Any]] = []
    usable_protocol_summaries: list[dict[str, Any]] = []
    for item in protocol_summaries:
        if int(item.get("pool_count") or 0) <= 0:
            removed_protocols.append({**item, "removed_reason": "protocol_pool_cache_not_available"})
            continue
        if str(item.get("protocol") or "").strip().lower() not in SUPPORTED_FLASH_POOL_PROTOCOLS:
            removed_protocols.append({**item, "removed_reason": "protocol_display_not_supported"})
            continue
        usable_protocol_summaries.append(item)

    by_chain: dict[str, dict[str, Any]] = {}
    for item in usable_protocol_summaries:
        key = str(item.get("chain_key") or "")
        chain = by_chain.setdefault(
            key,
            {
                "chain_key": key,
                "chain_id": item.get("chain_id"),
                "network": item.get("network") or "",
                "protocols": [],
                "pool_count": 0,
                "borrowable_pool_count": 0,
            },
        )
        chain["protocols"].append(item)
        chain["pool_count"] += int(item.get("pool_count") or 0)
        chain["borrowable_pool_count"] += int(item.get("borrowable_pool_count") or 0)

    chains = []
    for chain in by_chain.values():
        chain["protocols"] = _sorted_protocols(chain["protocols"])
        chains.append(chain)
    chains.sort(key=lambda item: (item.get("chain_id") is None, item.get("chain_id") or 0, item.get("network") or ""))
    present_protocols = {
        (item.get("chain_id"), str(item.get("protocol") or "").strip().lower())
        for item in usable_protocol_summaries
    }
    removed_protocols.extend(
        _expected_removed_protocols(
            present=present_protocols,
            existing_removed=removed_protocols,
            chain_protocol_candidates=chain_protocol_candidates,
        )
    )
    removed_protocols = _sorted_protocols(removed_protocols)

    return {
        "ok": True,
        "schema_version": "flash-loan-pool-discovery-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol_count": len(usable_protocol_summaries),
        "chain_count": len(chains),
        "chains": chains,
        "scanned_chains": _scanned_chains(
            chains=chains,
            removed_protocols=removed_protocols,
            chain_protocol_candidates=chain_protocol_candidates,
        ),
        "protocols": _sorted_protocols(usable_protocol_summaries),
        "removed_protocols": removed_protocols,
        "raw_snapshots": raw_snapshots,
        "errors": errors,
    }


def refresh_flash_loan_pool_discovery_cache(
    *,
    snapshot_path: Path | None = DEFAULT_SNAPSHOT_PATH,
    **kwargs: Any,
) -> dict[str, Any]:
    snapshot = discover_flash_loan_pool_snapshot(**kwargs)
    if snapshot_path is not None:
        _write_json(snapshot_path, snapshot)
    return snapshot


def load_cached_flash_loan_pool_discovery(
    *,
    snapshot_path: Path | None = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    snapshot = _read_json(snapshot_path)
    if snapshot:
        return snapshot
    return {
        "ok": True,
        "schema_version": "flash-loan-pool-discovery-v1",
        "generated_at": None,
        "protocol_count": 0,
        "chain_count": 0,
        "chains": [],
        "scanned_chains": [],
        "protocols": [],
        "removed_protocols": [],
        "raw_snapshots": {},
        "errors": [{"source": "cache", "error": "flash loan pool discovery cache is not generated"}],
    }


def query_flash_loan_pool_discovery(
    *,
    chain_id: int | None = None,
    protocol: str | None = None,
    snapshot_path: Path | None = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    snapshot = load_cached_flash_loan_pool_discovery(snapshot_path=snapshot_path)
    normalized_protocol = str(protocol or "").strip().lower()
    protocols = [
        item
        for item in snapshot.get("protocols") or []
        if (chain_id is None or item.get("chain_id") == int(chain_id))
        and (not normalized_protocol or str(item.get("protocol") or "") == normalized_protocol)
    ]
    removed_protocols = [
        item
        for item in snapshot.get("removed_protocols") or []
        if (chain_id is None or item.get("chain_id") == int(chain_id))
        and (not normalized_protocol or str(item.get("protocol") or "") == normalized_protocol)
    ]
    chain_ids = {item.get("chain_id") for item in protocols}
    chains = [
        chain
        for chain in snapshot.get("chains") or []
        if (
            (chain_id is None and chain.get("chain_id") in chain_ids)
            or (chain_id is not None and chain.get("chain_id") == int(chain_id))
        )
    ]
    scanned_chains = [
        chain
        for chain in snapshot.get("scanned_chains") or []
        if chain_id is None or chain.get("chain_id") == int(chain_id)
    ]
    if normalized_protocol:
        filtered_scanned_chains = []
        for chain in scanned_chains:
            if normalized_protocol not in {str(item or "").strip().lower() for item in chain.get("candidate_protocols") or []}:
                continue
            filtered = dict(chain)
            filtered["candidate_protocols"] = [
                item for item in chain.get("candidate_protocols") or [] if str(item or "").strip().lower() == normalized_protocol
            ]
            filtered["available_protocols"] = [
                item for item in chain.get("available_protocols") or [] if str(item or "").strip().lower() == normalized_protocol
            ]
            filtered["removed_protocols"] = [
                item
                for item in chain.get("removed_protocols") or []
                if str(item.get("protocol") or "").strip().lower() == normalized_protocol
            ]
            filtered_scanned_chains.append(filtered)
        scanned_chains = filtered_scanned_chains
    raw_snapshots = {}
    for key, raw in (snapshot.get("raw_snapshots") or {}).items():
        if not isinstance(raw, dict):
            continue
        raw_chain_id = _as_int(raw.get("chainId") or raw.get("chain_id"))
        if chain_id is not None and raw_chain_id != int(chain_id):
            continue
        if normalized_protocol:
            sources = [
                source
                for source in raw.get("sources") or []
                if isinstance(source, dict) and str(source.get("protocol") or "").strip().lower() == normalized_protocol
            ]
            if not sources:
                continue
            raw = {**raw, "sources": sources}
        raw_snapshots[key] = raw
    return {
        **snapshot,
        "chains": chains,
        "scanned_chains": scanned_chains,
        "protocols": protocols,
        "removed_protocols": removed_protocols,
        "raw_snapshots": raw_snapshots,
        "chain_count": len(chains),
        "protocol_count": len(protocols),
        "query": {"chain_id": chain_id, "protocol": normalized_protocol or None},
    }


def cached_robinhood_flash_liquidity_snapshot(
    *,
    snapshot_path: Path | None = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    snapshot = load_cached_flash_loan_pool_discovery(snapshot_path=snapshot_path)
    raw = (snapshot.get("raw_snapshots") or {}).get("robinhood_flash_liquidity")
    if isinstance(raw, dict):
        return raw
    return {
        "ok": False,
        "network": "robinhood",
        "chainId": 4663,
        "generatedAt": snapshot.get("generated_at"),
        "sources": [],
        "errors": [{"source": "cache", "error": "Robinhood flash liquidity cache is not generated"}],
        "evidence": ["flash_loan_pool_discovery_cache"],
    }


def protocol_options_for_chain(
    *,
    chain_id: int | None,
    snapshot_path: Path | None = DEFAULT_SNAPSHOT_PATH,
) -> list[dict[str, str]]:
    snapshot = query_flash_loan_pool_discovery(chain_id=chain_id, snapshot_path=snapshot_path)
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _sorted_protocols(snapshot.get("protocols") or []):
        protocol = str(item.get("protocol") or "").strip().lower()
        if not protocol or protocol in seen:
            continue
        seen.add(protocol)
        options.append({"protocol": protocol, "display_name": str(item.get("display_name") or _protocol_label(protocol))})
    return options


def _interval_seconds() -> float:
    raw = os.getenv("FLASH_LOAN_POOL_DISCOVERY_INTERVAL_SECONDS", "900").strip()
    try:
        return max(float(raw), 30.0)
    except ValueError:
        return 900.0


def start_flash_loan_pool_discovery_idle_worker(
    *,
    snapshot_path: Path | None = DEFAULT_SNAPSHOT_PATH,
    initial_delay_seconds: float = 5.0,
    interval_seconds: float | None = None,
    refresh_kwargs: dict[str, Any] | None = None,
) -> bool:
    if os.getenv("FLASH_LOAN_POOL_DISCOVERY_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return False
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return False
        _WORKER_STARTED = True

    def runner() -> None:
        if initial_delay_seconds > 0:
            time.sleep(initial_delay_seconds)
        while True:
            try:
                refresh_flash_loan_pool_discovery_cache(snapshot_path=snapshot_path, **(refresh_kwargs or {}))
            except Exception:
                pass
            time.sleep(interval_seconds if interval_seconds is not None else _interval_seconds())

    thread = threading.Thread(target=runner, name="flash-loan-pool-discovery", daemon=True)
    thread.start()
    return True
