"""Direction symmetry checks for static database-first build outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from core.db.database_path import absolute_database_path
from core.db.storage_business_io import query
from core.db.storage_dex_surface_snapshot import load_dex_overview_snapshot


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
MAINNET_BASE_ANCHORS = ("ETH", "USDG", "USDE", "TZ", "WETH")
TESTNET_BASE_ANCHORS = ("ETH", "WETH", "TZ", "USDE", "USDG")
MAINNET_BASE_ANCHOR_ADDRESSES = {
    "ETH": ZERO_ADDRESS,
    "WETH": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
    "USDG": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
    "USDE": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
    "TZ": "0xa8cf7c51eee671646ba440ffa180f970902fd2fb",
}
TESTNET_BASE_ANCHOR_ADDRESSES = {
    "ETH": ZERO_ADDRESS,
    "WETH": "0x7943e237c7f95da44e0301572d358911207852fa",
    "TZ": "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e",
    "USDE": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
    "USDG": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
}


def _text(value: object) -> str:
    return str(value or "").strip().lower()


def _json_loads(value: object) -> Any:
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _json_array_loads(value: object) -> list[Any] | None:
    parsed = _json_loads(value if value not in (None, "") else "[]")
    return parsed if isinstance(parsed, list) else None


def _normalized(direction_key: object) -> tuple[str, str] | None:
    text = _text(direction_key)
    parts = [part for part in text.split(":") if part]
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])


def _mirror(direction_key: object) -> str:
    pair = _normalized(direction_key)
    return f"{pair[1]}:{pair[0]}" if pair else ""


def _loop_direction(loop_key: object) -> tuple[str, str, str] | None:
    text = _text(loop_key)
    parts = [part for part in text.split(":") if part]
    if len(parts) == 3:
        return (parts[0], parts[1], parts[2])
    if len(parts) == 4 and parts[0] == "anchor":
        return (parts[1], parts[2], parts[3])
    return None


def direction_symmetry_violations(
    *,
    candidate_pairs: list[dict[str, Any]],
    observation_directions: list[dict[str, Any]],
    loop_keys: list[object],
) -> list[dict[str, Any]]:
    """Return every direction whose mirror has pools while it is empty.

    Direction may choose a different optimal/backup pool, but a physical pair
    either has depth in both directions or in neither.  This catches any stale
    build that accidentally reintroduces a one-sided direction gate.
    """
    violations: list[dict[str, Any]] = []

    candidate_by_direction = {
        _text(row.get("direction_key")): row
        for row in candidate_pairs
        if _text(row.get("direction_key"))
    }
    for direction_key, row in candidate_by_direction.items():
        mirror_key = _mirror(direction_key)
        if not direction_key or not mirror_key:
            continue
        has_pool = bool(
            row.get("yc_optimal_pool_id")
            or row.get("zc_optimal_pool_id")
        )
        mirror_row = candidate_by_direction.get(mirror_key)
        if mirror_row is None:
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_optimal_pool": has_pool,
                    "mirror_has_optimal_pool": None,
                }
            )
            continue
        mirror_has_pool = bool(
            mirror_row.get("yc_optimal_pool_id")
            or mirror_row.get("zc_optimal_pool_id")
        )
        if has_pool != mirror_has_pool:
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_optimal_pool": has_pool,
                    "mirror_has_optimal_pool": mirror_has_pool,
                }
            )
        forward_backup_ids = _json_array_loads(row.get("yc_backup_pool_ids_json"))
        reverse_backup_ids = _json_array_loads(mirror_row.get("zc_backup_pool_ids_json"))
        if forward_backup_ids is None or reverse_backup_ids is None:
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "invalid_backup_json": True,
                }
            )
        elif len(forward_backup_ids) != len(reverse_backup_ids):
            violations.append(
                {
                    "table": "dex_candidate_pool_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "backup_pool_count": len(forward_backup_ids),
                    "mirror_backup_pool_count": len(reverse_backup_ids),
                }
            )

    observation_by_direction = {
        _text(row.get("direction_key")): row
        for row in observation_directions
        if _text(row.get("direction_key"))
    }
    for direction_key, row in observation_by_direction.items():
        mirror_key = _mirror(direction_key)
        if not direction_key or not mirror_key:
            continue
        has_pool = bool(row.get("has_direct_swap_pool"))
        mirror_row = observation_by_direction.get(mirror_key)
        if mirror_row is None:
            violations.append(
                {
                    "table": "market_observation_yz_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_direct_swap_pool": has_pool,
                    "mirror_has_direct_swap_pool": None,
                }
            )
            continue
        mirror_has_pool = bool(mirror_row.get("has_direct_swap_pool"))
        pool_count = row.get("direct_swap_pool_count")
        mirror_pool_count = mirror_row.get("direct_swap_pool_count")
        if has_pool != mirror_has_pool or pool_count != mirror_pool_count:
            violations.append(
                {
                    "table": "market_observation_yz_pairs",
                    "direction_key": direction_key,
                    "mirror_direction_key": mirror_key,
                    "has_direct_swap_pool": has_pool,
                    "mirror_has_direct_swap_pool": mirror_has_pool,
                    "direct_swap_pool_count": pool_count,
                    "mirror_direct_swap_pool_count": mirror_pool_count,
                }
            )

    loop_pairs: set[tuple[str, str, str]] = set()
    for loop_key in loop_keys:
        pair = _loop_direction(loop_key)
        if pair:
            loop_pairs.add(pair)
    for pair in sorted(loop_pairs):
        mirror = (pair[0], pair[2], pair[1])
        if mirror not in loop_pairs:
            violations.append(
                {
                    "table": "v4_closed_loops",
                    "direction_key": ":".join(pair[1:]),
                    "mirror_direction_key": ":".join(mirror[1:]),
                    "has_loop": True,
                    "mirror_has_loop": False,
                }
            )
    return violations


def _tri_state(value: object) -> int | None:
    """Return 1/0 for explicit tri-state evidence and None otherwise."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return 1
        if text in {"0", "false", "no", "off", ""}:
            return 0
        return None
    return 1 if bool(value) else 0


def _json_array_loads(value: object) -> list[Any]:
    parsed = _json_loads(value if value not in (None, "") else "[]")
    return parsed if isinstance(parsed, list) else None


def _json_list_or_unpack(value: object) -> list[Any]:
    """Coerce JSON-encoded arrays into Python lists without hiding invalid JSON."""
    if isinstance(value, list):
        return value
    parsed = _json_array_loads(value)
    if parsed is not None:
        return parsed
    if isinstance(value, str):
        return [value]
    return []


def _pool_has_liquidity(pool: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(confirmed, basis)`` for one raw pool/liquidity row."""
    tri = _tri_state(pool.get("has_liquidity"))
    depth_status = str(pool.get("depth_status") or "").strip().lower()
    active = _tri_state(pool.get("recent_swap_active"))
    if tri == 1:
        return True, "has_liquidity"
    if active == 1:
        return True, "recent_swap_active"
    try:
        raw = str(pool.get("liquidity") or "").strip()
        raw_number = int(raw) if raw else 0
        if raw_number > 0:
            return True, "raw_liquidity"
    except (TypeError, ValueError):
        pass
    try:
        estimated = float(pool.get("estimated_tvl_usd") or 0)
        if estimated > 0:
            return True, "estimated_tvl_usd"
    except (TypeError, ValueError):
        pass
    try:
        index = float(pool.get("liquidity_index") or 0)
        if index > 0:
            return True, "liquidity_index"
    except (TypeError, ValueError):
        pass
    if tri == 0:
        return False, depth_status or "has_liquidity"
    return False, depth_status or "no_evidence"


def _norm_symbols(path: object) -> list[str]:
    if isinstance(path, str):
        stripped = path.strip()
        if stripped.startswith("["):
            values = _json_list_or_unpack(stripped)
            return [
                str(symbol or "").strip().upper()
                for symbol in values
                if str(symbol or "").strip()
            ]
        return [str(part).strip().upper() for part in stripped.split("->") if str(part).strip()]
    return [
        str(symbol or "").strip().upper()
        for symbol in (path or [])
        if str(symbol or "").strip()
    ]


def _norm_addresses(path: object) -> list[str]:
    if isinstance(path, str):
        stripped = str(path or "").strip()
        if stripped.startswith("["):
            values = _json_list_or_unpack(stripped)
        else:
            values = [stripped]
    else:
        values = path or []
    return [
        str(address or "").strip().lower()
        for address in values
        if str(address or "").strip()
    ]


def _pool_id(value: dict[str, Any]) -> str:
    return str(value.get("poolId") or value.get("pool") or value.get("pool_id") or "").strip().lower()


def _legal_anchor_context(
    database_url: str,
    network: str,
    chain_id: int,
) -> tuple[tuple[str, ...], set[str]]:
    """Resolve base + official stock anchors from constants and SQLite only."""
    mainnet = str(network or "").strip().lower() != "robinhood-testnet"
    base_addresses = dict(
        MAINNET_BASE_ANCHOR_ADDRESSES
        if mainnet
        else TESTNET_BASE_ANCHOR_ADDRESSES
    )
    stock_rows = query(
        database_url,
        "SELECT token_address, token_symbol, token_metadata_json FROM chain_tokens WHERE network = ? AND chain_id = ?",
        (network, chain_id),
    )
    for row in stock_rows:
        metadata = row.get("token_metadata_json")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
        if not isinstance(metadata, dict):
            continue
        symbol = str(row.get("token_symbol") or "").strip().upper()
        address = str(row.get("token_address") or "").strip().lower()
        if (
            symbol
            and address
            and str(metadata.get("category") or "").strip().lower() == "robinhood_stock"
        ):
            base_addresses[symbol] = address
    symbols = tuple(dict.fromkeys(base_addresses))
    addresses = {str(address or "").strip().lower() for address in base_addresses.values() if str(address or "").strip()}
    return symbols, addresses


def build_v4_integrity_report(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    market_id: str | None = None,
    overview_snapshot: dict[str, Any] | None = None,
    anchors: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a five-stage integrity report over the SQLite DEX surfaces.

    Stages are consumed in strict order: raw pools, candidate rows,
    observation rows, closed loops, then the persisted compact overview that
    feeds DEX overview and quote-routes pages.  Every executable pool must be
    traceable back to a confirmed-liquidity raw pool.  The report never reads
    JSON evidence files.
    """
    resolved = str(absolute_database_path(database_url))
    network = str(network or "").strip().lower()
    protocol = str(protocol or "uniswap_v4").strip().lower()
    configured_symbols, configured_addresses = _legal_anchor_context(
        resolved,
        network,
        chain_id,
    )
    legal_symbols = tuple(
        str(symbol or "").strip().upper()
        for symbol in (anchors or configured_symbols)
        if str(symbol or "").strip()
    )
    legal_addresses = {
        str(address or "").strip().lower()
        for address in configured_addresses
    }
    legal_symbol_set = set(legal_symbols)
    stages: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def _record_failure(
        *,
        stage: str,
        code: str,
        **detail: Any,
    ) -> None:
        failed.append({"stage": stage, "code": code, **detail})

    def _add_layer(name: str, stats: dict[str, Any], violations: list[dict[str, Any]]) -> None:
        for violation in violations:
            if violation.get("level") == "warning":
                warnings.append({"stage": name, **violation})
            else:
                failed.append({"stage": name, **violation})
        stages.append({"name": name, "stats": stats, "violationCount": len(violations)})

    # Layer 1: raw pool index and liquidity evidence.
    pools = query(
        resolved,
        """
        SELECT p.pool_id, p.token0, p.token1, p.fee, p.dynamic_fee, p.special_fee,
               p.hooks, l.has_liquidity, l.liquidity, l.liquidity_index,
               l.estimated_tvl_usd, l.depth_status, l.recent_swap_active,
               l.liquidity_block_number, l.evidence_source
        FROM v4_exchange_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots l
          ON l.network = p.network AND l.chain_id = p.chain_id
         AND l.protocol = p.protocol AND l.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ? AND p.protocol = ?
        ORDER BY p.pool_id
        """,
        (network, chain_id, protocol),
    )
    chain_token_addresses = {
        str(row.get("token_address") or "").strip().lower()
        for row in query(
            resolved,
            "SELECT token_address FROM chain_tokens WHERE network = ? AND chain_id = ?",
            (network, chain_id),
        )
        if str(row.get("token_address") or "").strip()
    }
    pool_stats = {
        "poolCount": len(pools),
        "confirmedLiquidityCount": 0,
        "noLiquidityCount": 0,
        "unknownLiquidityCount": 0,
        "missingLiquiditySnapshotCount": 0,
        "unscannedPoolCount": 0,
        "negativeEvidenceCount": 0,
        "poolWithUnknownTokenCount": 0,
        "refreshBlockNumbers": sorted(
            {
                int(row["liquidity_block_number"])
                for row in pools
                if row.get("liquidity_block_number") is not None
            }
        )[-3:],
        "evidenceSources": sorted(
            {
                str(row["evidence_source"] or "").strip()
                for row in pools
                if str(row.get("evidence_source") or "").strip()
            }
        ),
    }
    pool_violations: list[dict[str, Any]] = []
    confirmed_pool_ids: set[str] = set()
    for pool in pools:
        pool_id = str(pool.get("pool_id") or "").strip().lower()
        confirmed, basis = _pool_has_liquidity(pool)
        explicit_negative = (
            pool.get("has_liquidity") is not None
            and _tri_state(pool.get("has_liquidity")) == 0
        )
        if confirmed:
            confirmed_pool_ids.add(pool_id)
            pool_stats["confirmedLiquidityCount"] += 1
        elif explicit_negative:
            pool_stats["negativeEvidenceCount"] += 1
        else:
            status = str(pool.get("depth_status") or "").strip()
            if status:
                pool_stats["noLiquidityCount"] += 1
            else:
                pool_stats["unknownLiquidityCount"] += 1
                pool_stats["unscannedPoolCount"] += 1
        if not str(pool.get("has_liquidity") or "").strip() and not str(pool.get("depth_status") or "").strip():
            pool_stats["missingLiquiditySnapshotCount"] += 1
        for token in (pool.get("token0"), pool.get("token1")):
            address = str(token or "").strip().lower()
            if address and address not in chain_token_addresses:
                pool_stats["poolWithUnknownTokenCount"] += 1
                pool_violations.append(
                    {
                        "code": "pool_token_not_seeded",
                        "poolId": pool_id,
                        "tokenAddress": address,
                    }
                )
                break
        if not confirmed and not explicit_negative and str(pool.get("depth_status") or "").strip():
            pool_violations.append(
                {
                    "level": "warning",
                    "code": "pool_liquidity_evidence_inconsistent",
                    "poolId": pool_id,
                    "basis": basis,
                    "hasLiquidity": pool.get("has_liquidity"),
                    "depthStatus": pool.get("depth_status"),
                }
            )
    _add_layer("pool_index", pool_stats, pool_violations)

    liquidity_by_pool = {
        str(row.get("pool_id") or "").strip().lower(): row
        for row in query(
            resolved,
            """
            SELECT pool_id, has_liquidity, liquidity, liquidity_index,
                   estimated_tvl_usd, depth_status, recent_swap_active
            FROM v4_exchange_pool_liquidity_snapshots
            WHERE network = ? AND chain_id = ? AND protocol = ?
            """,
            (network, chain_id, protocol),
        )
        if str(row.get("pool_id") or "").strip()
    }
    pool_ids = {
        str(row.get("pool_id") or "").strip().lower()
        for row in pools
        if str(row.get("pool_id") or "").strip()
    }
    for pool_id, liquidity in liquidity_by_pool.items():
        if pool_id not in pool_ids:
            _record_failure(
                stage="pool_index",
                code="liquidity_without_pool",
                poolId=pool_id,
            )

    def _validated_pool(
        pool_id: object,
        context: str,
        *,
        check_liquidity: bool = True,
    ) -> None:
        normalized = str(pool_id or "").strip().lower()
        if not normalized:
            return
        if normalized not in pool_ids:
            _record_failure(
                stage=context,
                code="pool_missing_from_index",
                poolId=normalized,
            )
            return
        if check_liquidity:
            confirmed, basis = _pool_has_liquidity(liquidity_by_pool.get(normalized) or {})
            if not confirmed:
                _record_failure(
                    stage=context,
                    code="executable_pool_without_confirmed_liquidity",
                    poolId=normalized,
                    basis=basis,
                )

    # Layer 2: candidate pools.
    candidate_pairs = query(
        resolved,
        """
        SELECT anchor_symbol, direction_key, yc_optimal_pool_id, zc_optimal_pool_id,
               yc_backup_pool_ids_json, zc_backup_pool_ids_json
        FROM dex_candidate_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    candidate_stats = {
        "pairCount": len(candidate_pairs),
        "abnormalPoolCount": int(
            query(
                resolved,
                """
                SELECT COUNT(*) AS c FROM dex_candidate_abnormal_pools
                WHERE network = ? AND chain_id = ? AND protocol = ?
                """,
                (network, chain_id, protocol),
            )[0]["c"]
            or 0
        ),
        "anchorSymbols": sorted(
            {
                str(row.get("anchor_symbol") or "").strip().upper()
                for row in candidate_pairs
                if str(row.get("anchor_symbol") or "").strip()
            }
        ),
    }
    candidate_violations: list[dict[str, Any]] = []
    anchor_symbol_counts: dict[str, int] = {}
    for row in candidate_pairs:
        anchor = str(row.get("anchor_symbol") or "").strip().upper()
        if not anchor:
            continue
        anchor_symbol_counts[anchor] = anchor_symbol_counts.get(anchor, 0) + 1
        if anchor not in legal_symbol_set:
            candidate_violations.append(
                {
                    "code": "candidate_anchor_not_legal_endpoint",
                    "anchorSymbol": anchor,
                    "directionKey": row.get("direction_key"),
                }
            )
        for column in ("yc_optimal_pool_id", "zc_optimal_pool_id"):
            pool_id = str(row.get(column) or "").strip().lower()
            if pool_id:
                _validated_pool(pool_id, "candidate")
        for column in ("yc_backup_pool_ids_json", "zc_backup_pool_ids_json"):
            for pool_id in _json_array_loads(row.get(column)):
                _validated_pool(pool_id, "candidate")
    candidate_stats["anchorCount"] = len(anchor_symbol_counts)
    _add_layer("candidate", candidate_stats, candidate_violations)

    # Layer 3: market observation.
    observation_rows = query(
        resolved,
        """
        SELECT anchor_symbol, direction_key, token_x_address, token_y_address,
               has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
               optimal_pool_id
        FROM market_observation_yz_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    observation_stats = {
        "directionCount": len(observation_rows),
        "abnormalPoolCount": int(
            query(
                resolved,
                """
                SELECT COUNT(*) AS c FROM market_observation_abnormal_intermediate_pools
                WHERE network = ? AND chain_id = ? AND protocol = ?
                """,
                (network, chain_id, protocol),
            )[0]["c"]
            or 0
        ),
        "directPoolCount": sum(
            1
            for row in observation_rows
            if bool(row.get("has_direct_swap_pool")) or _json_array_loads(row.get("pool_ids_json"))
        ),
    }
    observation_violations: list[dict[str, Any]] = []
    for row in observation_rows:
        anchor = str(row.get("anchor_symbol") or "").strip().upper()
        if anchor and anchor not in legal_symbol_set:
            observation_violations.append(
                {
                    "code": "observation_anchor_not_legal_endpoint",
                    "anchorSymbol": anchor,
                    "directionKey": row.get("direction_key"),
                }
            )
        row_pool_ids = _json_array_loads(row.get("pool_ids_json"))
        has_pool = bool(row.get("has_direct_swap_pool")) or bool(row_pool_ids)
        if row.get("optimal_pool_id"):
            _validated_pool(row.get("optimal_pool_id"), "observation")
        for pool_id in row_pool_ids:
            _validated_pool(pool_id, "observation")
        if has_pool and not _tri_state(row.get("has_direct_swap_pool")):
            observation_violations.append(
                {
                    "code": "observation_pool_flag_missing",
                    "directionKey": row.get("direction_key"),
                    "poolCount": len(row_pool_ids),
                }
            )
    observation_stats["anchorCount"] = len(
        {
            str(row.get("anchor_symbol") or "").strip().upper()
            for row in observation_rows
            if str(row.get("anchor_symbol") or "").strip()
        }
    )
    _add_layer("observation", observation_stats, observation_violations)

    # Layer 4: closed loops and hop lineage.
    loops = query(
        resolved,
        """
        SELECT loop_key, anchor_address, anchor_symbol, path_text,
               path_addresses_json, path_symbols_json, pool_ids_json, status
        FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ?
        """,
        (network, chain_id, protocol),
    )
    hops = query(
        resolved,
        """
        SELECT loop_key, hop_index, hop_name, input_address, output_address,
               pool_id
        FROM v4_closed_loop_hops
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY loop_key, hop_index
        """,
        (network, chain_id, protocol),
    )
    loops_by_key = {str(row.get("loop_key") or "").strip().lower(): row for row in loops}
    loop_ids = set(loops_by_key)
    hop_rows_by_loop: dict[str, list[dict[str, Any]]] = {}
    loop_pool_ids: dict[str, list[str]] = {}
    loop_violations: list[dict[str, Any]] = []
    for hop in hops:
        key = str(hop.get("loop_key") or "").strip().lower()
        pool_id = str(hop.get("pool_id") or "").strip().lower()
        if not key:
            continue
        hop_rows_by_loop.setdefault(key, []).append(hop)
        if pool_id:
            loop_pool_ids.setdefault(key, []).append(pool_id)
        if key not in loop_ids:
            loop_violations.append(
                {
                    "code": "hop_without_loop",
                    "loopKey": key,
                    "poolId": pool_id,
                }
            )
        _validated_pool(pool_id, "closed_loop")
    for loop in loops:
        key = str(loop.get("loop_key") or "").strip().lower()
        anchor_symbol = str(loop.get("anchor_symbol") or "").strip().upper()
        anchor_address = str(loop.get("anchor_address") or "").strip().lower()
        path_symbols = _norm_symbols(loop.get("path_symbols_json"))
        path_addresses = _norm_addresses(loop.get("path_addresses_json"))
        loop_pool_ids_json = _json_array_loads(loop.get("pool_ids_json"))
        hop_rows = hop_rows_by_loop.get(key) or []
        if anchor_symbol and anchor_symbol not in legal_symbol_set:
            loop_violations.append(
                {
                    "code": "loop_anchor_not_legal_endpoint",
                    "loopKey": key,
                    "anchorSymbol": anchor_symbol,
                }
            )
        if anchor_address and anchor_address not in legal_addresses:
            loop_violations.append(
                {
                    "code": "loop_anchor_address_not_legal",
                    "loopKey": key,
                    "anchorAddress": anchor_address,
                }
            )
        if len(path_symbols) < 4 or path_symbols[0] != path_symbols[-1]:
            loop_violations.append(
                {
                    "code": "loop_path_not_closed",
                    "loopKey": key,
                    "pathSymbols": path_symbols,
                }
            )
        else:
            path_legal = all(symbol in legal_symbol_set for symbol in (path_symbols[0], path_symbols[-1]))
            if not path_legal:
                loop_violations.append(
                    {
                        "code": "loop_path_endpoint_not_legal",
                        "loopKey": key,
                        "pathSymbols": path_symbols,
                    }
                )
        for pool_id in loop_pool_ids_json:
            _validated_pool(pool_id, "closed_loop")
        expected_hop_count = len(path_symbols) - 1 if len(path_symbols) >= 2 else 0
        if hop_rows and len(hop_rows) != expected_hop_count:
            loop_violations.append(
                {
                    "code": "loop_hop_count_mismatch",
                    "loopKey": key,
                    "pathSymbolCount": len(path_symbols),
                    "hopCount": len(hop_rows),
                }
            )
        if hop_rows:
            first = hop_rows[0]
            last = hop_rows[-1]
            if path_addresses and len(path_addresses) >= len(hop_rows) + 1:
                if first.get("input_address") and first["input_address"] != path_addresses[0]:
                    loop_violations.append(
                        {
                            "code": "loop_first_hop_entry_mismatch",
                            "loopKey": key,
                            "hopInput": first.get("input_address"),
                            "pathStart": path_addresses[0],
                        }
                    )
                if last.get("output_address") and last["output_address"] != path_addresses[-1]:
                    loop_violations.append(
                        {
                            "code": "loop_last_hop_exit_mismatch",
                            "loopKey": key,
                            "hopOutput": last.get("output_address"),
                            "pathEnd": path_addresses[-1],
                        }
                    )
            for index, hop in enumerate(hop_rows):
                if index + 1 < len(hop_rows):
                    current = hop.get("output_address")
                    following = hop_rows[index + 1].get("input_address")
                    if current and following and str(current or "").strip().lower() != str(following or "").strip().lower():
                        loop_violations.append(
                            {
                                "code": "loop_hop_chain_break",
                                "loopKey": key,
                                "hopIndex": index,
                                "outputAddress": current,
                                "nextInputAddress": following,
                            }
                        )
    loop_stats = {
        "loopCount": len(loops),
        "hopCount": len(hops),
        "anchorSymbols": sorted(
            {
                str(row.get("anchor_symbol") or "").strip().upper()
                for row in loops
                if str(row.get("anchor_symbol") or "").strip()
            }
        ),
        "confirmedLoopCount": sum(
            1
            for row in loops
            if all(
                pool_id in confirmed_pool_ids
                for pool_id in loop_pool_ids.get(str(row.get("loop_key") or "").strip().lower(), [])
            )
        ),
        "mirrorDirectionCount": 0,
    }
    _add_layer("closed_loop", loop_stats, loop_violations)

    # Layer 5: persisted DEX overview snapshot consumed by overview/quote pages.
    overview = overview_snapshot
    if overview is None:
        overview = load_dex_overview_snapshot(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            database_url=resolved,
        )
    overview_violations: list[dict[str, Any]] = []
    overview_stats: dict[str, Any] = {
        "snapshotPresent": overview is not None,
        "routeSummaryCount": 0,
        "anchorSymbols": [],
        "routePoolCount": 0,
    }
    if overview is None:
        overview_violations.append(
            {
                "level": "warning",
                "code": "overview_snapshot_missing",
                "detail": "DEX overview/quote pages will lazily rebuild from closed loops when served",
            }
        )
    else:
        summaries = [item for item in overview.get("closedLoopRouteSummaries") or [] if isinstance(item, dict)]
        overview_stats["routeSummaryCount"] = len(summaries)
        anchor_from_routes = sorted(
            {
                str(summary.get("anchorSymbol") or "").strip().upper()
                for summary in summaries
                if str(summary.get("anchorSymbol") or "").strip()
            }
        )
        overview_stats["anchorSymbols"] = anchor_from_routes
        for summary in summaries:
            anchor = str(summary.get("anchorSymbol") or "").strip().upper()
            path_symbols = _norm_symbols(summary.get("pathSymbols") or summary.get("pathText"))
            route_pool_ids = [
                _pool_id(pool)
                for pool in summary.get("routePools") or []
                if isinstance(pool, dict) and _pool_id(pool)
            ]
            route_pool_ids.extend(
                str(pool_id or "").strip().lower()
                for pool_id in _json_list_or_unpack(summary.get("poolIds"))
                if str(pool_id or "").strip()
            )
            route_pool_ids = list(dict.fromkeys(route_pool_ids))
            overview_stats["routePoolCount"] += len(route_pool_ids)
            if anchor and anchor not in legal_symbol_set:
                overview_violations.append(
                    {
                        "code": "overview_anchor_not_legal_endpoint",
                        "anchorSymbol": anchor,
                        "pathText": summary.get("pathText"),
                    }
                )
            if path_symbols:
                if len(path_symbols) < 4 or path_symbols[0] != path_symbols[-1]:
                    overview_violations.append(
                        {
                            "code": "overview_route_not_closed",
                            "pathText": summary.get("pathText"),
                        }
                    )
                if path_symbols[0] not in legal_symbol_set or path_symbols[-1] not in legal_symbol_set:
                    overview_violations.append(
                        {
                            "code": "overview_route_endpoint_not_legal",
                            "pathText": summary.get("pathText"),
                            "pathSymbols": path_symbols,
                        }
                    )
            for pool_id in route_pool_ids:
                _validated_pool(pool_id, "overview")
    expected_anchor_symbols = loop_stats["anchorSymbols"]
    overview_anchors = overview_stats["anchorSymbols"]
    if overview is not None and expected_anchor_symbols != overview_anchors:
        overview_violations.append(
            {
                "code": "overview_anchors_differ_from_closed_loops",
                "closedLoopAnchors": expected_anchor_symbols,
                "overviewAnchors": overview_anchors,
            }
        )
    _add_layer("overview_quote", overview_stats, overview_violations)

    return {
        "ok": not failed,
        "database": resolved,
        "network": network,
        "chainId": chain_id,
        "protocol": protocol,
        "legalAnchorSymbols": legal_symbols,
        "stages": stages,
        "violations": failed,
        "warnings": warnings,
        "violationCount": len(failed),
        "warningCount": len(warnings),
        "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


__all__ = [
    "build_v4_integrity_report",
    "direction_symmetry_violations",
]
