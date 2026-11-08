#!/usr/bin/env python3
"""Reconcile explicit zero-liquidity pools against SQLite candidate/loop rows.

Read-only evidence script for the Robinhood testnet V4 maintenance loop.  It
compares ``v4_exchange_pool_liquidity_snapshots.has_liquidity=0`` pools with
``dex_candidate_*`` / ``v4_closed_loop_*`` references and with the persisted
``v4_live_refresh`` explicit-zero count, then writes a latest evidence report.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from core.db.database_path import resolve_database_path

DEFAULT_STORE = resolve_database_path()
DEFAULT_EVIDENCE = (
    SRC_ROOT
    / "runtime"
    / "evidence"
    / "robinhood-testnet"
    / "zero_liquidity_vs_candidates"
    / "latest.json"
)
DEFAULT_IDLE_EVIDENCE_ROOT = (
    SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "idle_maintenance"
)
NETWORK = "robinhood-testnet"
CHAIN_ID = 46630
PROTOCOL = "uniswap_v4"
SCHEMA_VERSION = "robinhood-testnet-zero-liquidity-vs-candidates-v1"
SAMPLE_LIMIT = 200


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_sys_path() -> None:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))


def _query(database: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    _ensure_sys_path()
    from core.db.storage_business_io import query

    return query(database, sql, params)


def load_explicit_zero_pools(database: str) -> list[dict[str, Any]]:
    return _query(
        database,
        """
        SELECT pool_id, has_liquidity, liquidity, liquidity_index, depth_status,
               liquidity_read_at, liquidity_block_number, evidence_source
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND has_liquidity = 0
        ORDER BY pool_id
        """,
        (NETWORK, CHAIN_ID, PROTOCOL),
    )


def count_residual_references(database: str, pool_ids: list[str]) -> dict[str, Any]:
    result = {
        "dex_candidate_pools": 0,
        "dex_candidate_pool_recommendations": 0,
        "v4_closed_loop_hops": 0,
        "v4_closed_loop_optimizations": 0,
        "v4_closed_loops": 0,
        "v4_closed_loops_by_hop_refs": 0,
        "v4_closed_loops_by_pool_ids_json": 0,
    }
    if not pool_ids:
        return result
    placeholders = ",".join("?" for _ in pool_ids)
    for table, column in (
        ("dex_candidate_pools", "pool_id"),
        ("dex_candidate_pool_recommendations", "pool_id"),
        ("v4_closed_loop_hops", "pool_id"),
        ("v4_closed_loop_optimizations", "optimized_pool_id"),
    ):
        rows = _query(
            database,
            (
                f"SELECT COUNT(*) AS c FROM {table} "
                "WHERE network = ? AND chain_id = ? AND protocol = ? "
                f"AND {column} IN ({placeholders})"
            ),
            (NETWORK, CHAIN_ID, PROTOCOL, *pool_ids),
        )
        result[table] = int(rows[0]["c"] or 0) if rows else 0

    affected = _query(
        database,
        (
            "SELECT DISTINCT loop_key FROM v4_closed_loop_hops "
            "WHERE network = ? AND chain_id = ? AND protocol = ? "
            f"AND pool_id IN ({placeholders}) "
            "UNION "
            "SELECT DISTINCT loop_key FROM v4_closed_loop_optimizations "
            "WHERE network = ? AND chain_id = ? AND protocol = ? "
            f"AND optimized_pool_id IN ({placeholders})"
        ),
        (NETWORK, CHAIN_ID, PROTOCOL, *pool_ids, NETWORK, CHAIN_ID, PROTOCOL, *pool_ids),
    )
    hop_loop_keys = {
        str(row.get("loop_key") or "").strip()
        for row in affected
        if str(row.get("loop_key") or "").strip()
    }

    json_conditions = " OR ".join(["pool_ids_json LIKE ?"] * len(pool_ids))
    json_rows = _query(
        database,
        (
            "SELECT DISTINCT loop_key FROM v4_closed_loops "
            "WHERE network = ? AND chain_id = ? AND protocol = ? "
            f"AND ({json_conditions})"
        ),
        (NETWORK, CHAIN_ID, PROTOCOL, *[f"%{pool_id}%" for pool_id in pool_ids]),
    )
    json_loop_keys = {
        str(row.get("loop_key") or "").strip()
        for row in json_rows
        if str(row.get("loop_key") or "").strip()
    }

    all_loop_keys = hop_loop_keys | json_loop_keys
    loop_count = 0
    if all_loop_keys:
        loop_placeholders = ",".join("?" for _ in all_loop_keys)
        rows = _query(
            database,
            (
                "SELECT COUNT(*) AS c FROM v4_closed_loops "
                "WHERE network = ? AND chain_id = ? AND protocol = ? "
                f"AND loop_key IN ({loop_placeholders})"
            ),
            (NETWORK, CHAIN_ID, PROTOCOL, *sorted(all_loop_keys)),
        )
        loop_count = int(rows[0]["c"] or 0) if rows else 0
    result["v4_closed_loops_by_hop_refs"] = len(hop_loop_keys)
    result["v4_closed_loops_by_pool_ids_json"] = len(json_loop_keys)
    result["v4_closed_loops"] = loop_count
    return result


def load_scan_state(database: str) -> dict[str, Any]:
    try:
        _ensure_sys_path()
        from core.db.v4_scan_state import load_v4_scan_state as _load

        state = _load(
            database,
            network=NETWORK,
            chain_id=CHAIN_ID,
            protocol=PROTOCOL,
        )
        return {
            "ok": True,
            "explicitZeroLiquidityPoolCount": state.get(
                "explicit_zero_liquidity_pool_count"
            ),
            "refreshedAt": state.get("refreshed_at"),
            "scannedPoolCount": state.get("scanned_pool_count"),
            "poolCount": state.get("pool_count"),
            "confirmedLiquidityPoolCount": state.get(
                "confirmed_liquidity_pool_count"
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)[:500],
        }


def load_cleanup_evidence(idle_evidence_root: Path, limit: int = 20) -> dict[str, Any]:
    cleanups: list[dict[str, Any]] = []
    failed_count = 0
    if not idle_evidence_root.is_dir():
        return {
            "cleanupEvidenceCount": 0,
            "failedCount": 0,
            "recent": [],
            "latest": None,
        }
    directories = sorted(
        (path for path in idle_evidence_root.iterdir() if path.is_dir()),
        reverse=True,
    )
    for directory in directories:
        report_path = directory / "report.json"
        if not report_path.is_file():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict) or report.get("mode") != "zero_liquidity_pool_cleanup":
            continue
        if not report.get("ok"):
            failed_count += 1
            continue
        pool_ids = [str(item) for item in report.get("poolIds") or []]
        cleanups.append(
            {
                "generatedAt": report.get("generatedAt"),
                "phase": report.get("phase"),
                "cleanedPoolCount": report.get("cleanedPoolCount"),
                "poolIdCount": len(pool_ids),
                "poolIds": pool_ids[:SAMPLE_LIMIT],
                "cleaned": report.get("cleaned") or {},
            }
        )
        if len(cleanups) >= limit:
            break
    return {
        "cleanupEvidenceCount": len(cleanups),
        "failedCount": failed_count,
        "recent": cleanups[:5],
        "latest": cleanups[0] if cleanups else None,
    }


def build_report(
    database: str,
    *,
    idle_evidence_root: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    zero_pools = load_explicit_zero_pools(database)
    pool_ids = [str(row["pool_id"]) for row in zero_pools]
    residuals = count_residual_references(database, pool_ids)
    scan_state = load_scan_state(database)
    cleanup = load_cleanup_evidence(idle_evidence_root)
    residual_tables = (
        "dex_candidate_pools",
        "dex_candidate_pool_recommendations",
        "v4_closed_loop_hops",
        "v4_closed_loop_optimizations",
        "v4_closed_loops",
    )
    residual_total = sum(int(residuals[table]) for table in residual_tables)
    scan_matches: bool | None = None
    if scan_state.get("ok") and scan_state.get("explicitZeroLiquidityPoolCount") is not None:
        scan_matches = int(scan_state["explicitZeroLiquidityPoolCount"]) == len(pool_ids)

    cleanup_residuals: dict[str, Any] = {
        "latestCleanupPoolIdCount": 0,
        "residualReferences": None,
    }
    latest_cleanup = cleanup.get("latest")
    if latest_cleanup:
        latest_pool_ids = sorted(
            {
                str(pool_id)
                for pool_id in latest_cleanup.get("poolIds") or []
                if str(pool_id).startswith("0x")
            }
        )
        cleanup_residuals["latestCleanupPoolIdCount"] = len(latest_pool_ids)
        cleanup_residuals["residualReferences"] = count_residual_references(
            database,
            latest_pool_ids,
        )

    if not zero_pools and latest_cleanup is None:
        conclusion = (
            "No explicit zero-liquidity pools and no zero-liquidity cleanup "
            "evidence currently recorded."
        )
    elif residual_total == 0:
        conclusion = (
            "All explicit zero-liquidity pools have no residual candidate/loop "
            "references; raw pools are retained for liquidity recovery."
        )
    else:
        conclusion = (
            f"Found {residual_total} residual candidate/loop references to "
            "explicit zero-liquidity pools requiring cleanup attention."
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at or _utc_now_iso(),
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "protocol": PROTOCOL,
        "source": str(Path(database).resolve()),
        "explicitZeroLiquidityPoolCount": len(pool_ids),
        "explicitZeroLiquidityPoolsSample": zero_pools[:SAMPLE_LIMIT],
        "explicitZeroLiquidityPoolsTruncated": len(zero_pools) > SAMPLE_LIMIT,
        "scanState": scan_state,
        "scanStateMatchesExplicitPools": scan_matches,
        "cleanupEvidence": cleanup,
        "cleanupEvidenceResiduals": cleanup_residuals,
        "residualReferences": residuals,
        "residualReferenceTotal": residual_total,
        "conclusion": conclusion,
        "safety": {
            "writesSqlite": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "mainnetBroadcastEnabled": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=str(DEFAULT_STORE),
        help="local SQLite store; an absolute path is required for storage helpers.",
    )
    parser.add_argument(
        "--idle-evidence-root",
        default=str(DEFAULT_IDLE_EVIDENCE_ROOT),
        help="idle_maintenance evidence directory scanned for cleanup reports.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_EVIDENCE),
        help="evidence output path; defaults to zero_liquidity_vs_candidates/latest.json.",
    )
    parser.add_argument("--schema-version", default=SCHEMA_VERSION)
    args = parser.parse_args()

    database = str(Path(args.database).expanduser().resolve())
    if not Path(database).is_file():
        parser.error(f"database not found: {database}")
    report = build_report(
        database,
        idle_evidence_root=Path(args.idle_evidence_root),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary = {
        "schemaVersion": report["schemaVersion"],
        "generatedAt": report["generatedAt"],
        "explicitZeroLiquidityPoolCount": report["explicitZeroLiquidityPoolCount"],
        "scanStateMatchesExplicitPools": report["scanStateMatchesExplicitPools"],
        "cleanupEvidenceCount": report["cleanupEvidence"]["cleanupEvidenceCount"],
        "residualReferenceTotal": report["residualReferenceTotal"],
        "conclusion": report["conclusion"],
        "evidence": str(output.resolve()),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
