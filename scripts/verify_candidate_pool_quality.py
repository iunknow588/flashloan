#!/usr/bin/env python3
"""Audit and optionally clean candidate pools across all three closed-loop hops.

The candidate surface includes the YC, Y-Z and ZC legs, not only the first and
third recommended pools.  A pool is eligible only when it is a fixed-fee
``0 < fee <= 3000`` pool with no dynamic/special fee, no hook, valid token
addresses and either confirmed depth or live PoolManager liquidity evidence.
The pool manager tool owns the candidate surface; this script is the read-only
verification/reporting companion and never performs another pool scan.

The default mode is read-only.  ``--apply`` deletes unsuitable candidate rows
and the same group's recommendation references; closed-loop tables are never
modified by this tool.  Raw ``v4_exchange_pools`` and liquidity snapshots are
retained so a later scan can recover them if they are ever confirmed as deep
again.  Downstream consumers should rebuild from candidate pools after a
change, using the ``consumersNeedRebuild`` count as the signal.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
MAX_CANDIDATE_FIXED_FEE = 3000
CONFIRMED_DEPTH_STATUSES = {"deep", "liquid", "live_liquid", "unpriced"}
LIVE_STATE_EVIDENCE_SOURCES = {
    "pool_manager_extsload_live_state_batch",
    "pool_manager_extsload_live_state_batch_rust",
}
CHUNK_SIZE = 500


def _network_for_chain_id(chain_id: int) -> str:
    if chain_id == 4663:
        return "robinhood"
    if chain_id == 46630:
        return "robinhood-testnet"
    return f"chain-{chain_id}"


def _connect(store: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(store), timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def _is_zero_address(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        return False
    try:
        return int(text, 16) == 0
    except ValueError:
        return False


def _is_valid_currency(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        return False
    try:
        number = int(text, 16)
    except ValueError:
        return False
    if number == 0:
        return True
    return len(text) == 42


def _is_zero_hook(value: Any) -> bool:
    return str(value or "").strip().lower() in ("", ZERO_ADDRESS)


def _truthy(value: Any) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _pool_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    try:
        fee = int(row.get("fee") or 0)
    except (TypeError, ValueError):
        fee = 0
        reasons.append("fee_invalid")
    if fee <= 0:
        reasons.append("fee_non_positive")
    if fee > MAX_CANDIDATE_FIXED_FEE:
        reasons.append("fee_too_high")
    if _truthy(row.get("dynamic_fee")) or _truthy(row.get("special_fee")):
        reasons.append("dynamic_or_special_fee")
    if not _is_zero_hook(row.get("hooks")):
        reasons.append("non_zero_hook")
    if not _is_valid_currency(row.get("token0")):
        reasons.append("invalid_token0")
    if not _is_valid_currency(row.get("token1")):
        reasons.append("invalid_token1")
    if not _is_valid_currency(row.get("token0")) or not _is_valid_currency(row.get("token1")):
        pass
    if str(row.get("token0") or "").strip().lower() == str(row.get("token1") or "").strip().lower():
        reasons.append("same_token_addresses")
    depth_status = str(row.get("depth_status") or row.get("depthStatus") or "").strip().lower()
    has_liquidity = _truthy(row.get("has_liquidity") if "has_liquidity" in row else row.get("hasLiquidity"))
    liquidity_index = row.get("liquidity_index")
    try:
        raw_liquidity = float(row.get("liquidity") or 0)
    except (TypeError, ValueError):
        raw_liquidity = 0.0
    try:
        liquidity_index_value = float(liquidity_index or 0)
    except (TypeError, ValueError):
        liquidity_index_value = 0.0
    evidence_source = str(row.get("evidence_source") or "").strip().lower()
    live_evidence = evidence_source in LIVE_STATE_EVIDENCE_SOURCES
    positive_liquidity = liquidity_index_value > 0 or raw_liquidity > 0
    confirmed_depth = (
        has_liquidity
        and (
            (
                depth_status in CONFIRMED_DEPTH_STATUSES
                and (depth_status != "unpriced" or positive_liquidity)
            )
            or (depth_status == "thin" and live_evidence and positive_liquidity)
        )
    )
    if not confirmed_depth:
        reasons.append(f"depth_not_deep:{depth_status or 'missing'}")
    if not has_liquidity:
        reasons.append("missing_liquidity")
    return reasons


def _chunk(values: list[Any], size: int = CHUNK_SIZE) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def load_candidate_audit(connection: sqlite3.Connection, *, chain_id: int) -> dict[str, Any]:
    network = _network_for_chain_id(chain_id)
    rows = connection.execute(
        """
        SELECT p.network, p.chain_id, p.protocol, p.anchor_scope, p.anchor_symbol,
               p.pool_id, p.fee, p.dynamic_fee, p.special_fee, p.hooks,
               p.token0, p.token1, p.depth_status, p.has_liquidity,
               p.liquidity_index, p.estimated_tvl_usd, p.liquidity,
               p.usable_for_observation, s.evidence_source
        FROM dex_candidate_pools p
        LEFT JOIN v4_exchange_pool_liquidity_snapshots s
          ON s.network = p.network AND s.chain_id = p.chain_id
         AND s.protocol = p.protocol AND s.pool_id = p.pool_id
        WHERE p.network = ? AND p.chain_id = ?
        ORDER BY p.pool_id
        """,
        (network, chain_id),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    bad_pool_ids: set[str] = set()
    reasons_by_pool: dict[str, list[str]] = {}
    for raw in rows:
        row = dict(raw)
        reasons = _pool_reasons(row)
        row["qualityReasons"] = reasons
        candidates.append(row)
        if reasons:
            bad_pool_ids.add(str(row["pool_id"] or "").strip().lower())
            reasons_by_pool[str(row["pool_id"] or "").strip().lower()] = reasons

    bad_pool_list = sorted(bad_pool_ids)
    affected_loop_keys: set[str] = set()
    if bad_pool_list:
        placeholder_groups: list[str] = []
        params: list[Any] = [network, chain_id]
        for chunk in _chunk(bad_pool_list):
            placeholders = ",".join("?" for _ in chunk)
            placeholder_groups.append(placeholders)
            params.extend(chunk)
        joined = " OR ".join(f"(h.pool_id IN ({piece}))" for piece in placeholder_groups)
        hop_rows = connection.execute(
            f"""
            SELECT DISTINCT h.loop_key
            FROM v4_closed_loop_hops h
            WHERE h.network = ? AND h.chain_id = ? AND ({joined})
            """,
            params,
        ).fetchall()
        affected_loop_keys.update(str(row["loop_key"] or "").strip() for row in hop_rows if row["loop_key"])

        for chunk in _chunk(bad_pool_list):
            placeholders = ",".join("?" for _ in chunk)
            json_rows = connection.execute(
                f"""
                SELECT DISTINCT loop_key
                FROM v4_closed_loops
                WHERE network = ? AND chain_id = ?
                  AND ({ " OR ".join(f"pool_ids_json LIKE ?" for _ in chunk) })
                """,
                (network, chain_id, *[f"%{pool_id}%" for pool_id in chunk]),
            ).fetchall()
            affected_loop_keys.update(str(row["loop_key"] or "").strip() for row in json_rows if row["loop_key"])

    recommendation_count = 0
    if bad_pool_list:
        for chunk in _chunk(bad_pool_list):
            placeholders = ",".join("?" for _ in chunk)
            recommendation_count += int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM dex_candidate_pool_recommendations
                    WHERE network = ? AND chain_id = ? AND pool_id IN ({placeholders})
                    """,
                    (network, chain_id, *chunk),
                ).fetchone()[0]
                or 0
            )

    return {
        "network": network,
        "chainId": chain_id,
        "candidateTotal": len(candidates),
        "eligibleTotal": len(candidates) - len(bad_pool_ids),
        "ineligibleTotal": len(bad_pool_ids),
        "ineligiblePoolIds": bad_pool_list,
        "reasonsByPool": reasons_by_pool,
        "affectedClosedLoopCount": len(affected_loop_keys),
        "affectedClosedLoopKeys": sorted(affected_loop_keys),
        "affectedRecommendationCount": recommendation_count,
    }


def _delete_in_chunks(
    connection: sqlite3.Connection,
    *,
    sql: str,
    prefix_params: list[Any],
    values: list[Any],
    placeholder_counts: int = 1,
) -> int:
    total = 0
    for chunk in _chunk(values):
        placeholders = ",".join("?" for _ in chunk)
        statement = sql.format(placeholders=placeholders)
        params = prefix_params + chunk
        total += int(connection.execute(statement, params).rowcount or 0)
    return total


def apply_cleanup(connection: sqlite3.Connection, audit: dict[str, Any]) -> dict[str, int]:
    bad_pool_ids = audit["ineligiblePoolIds"]
    counts: dict[str, int] = {}
    if bad_pool_ids:
        counts["candidatePoolsDeleted"] = _delete_in_chunks(
            connection,
            sql=(
                "DELETE FROM dex_candidate_pools "
                "WHERE network = ? AND chain_id = ? AND pool_id IN ({placeholders})"
            ),
            prefix_params=[audit["network"], audit["chainId"]],
            values=bad_pool_ids,
        )
        counts["recommendationsDeleted"] = _delete_in_chunks(
            connection,
            sql=(
                "DELETE FROM dex_candidate_pool_recommendations "
                "WHERE network = ? AND chain_id = ? AND pool_id IN ({placeholders})"
            ),
            prefix_params=[audit["network"], audit["chainId"]],
            values=bad_pool_ids,
        )
        counts["consumersNeedRebuild"] = int(audit["affectedClosedLoopCount"])
    return counts


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--apply", action="store_true", help="delete ineligible rows")
    parser.add_argument("--json", action="store_true", help="print JSON report")
    args = parser.parse_args()

    if not args.store.exists():
        print(f"store not found: {args.store}", file=sys.stderr)
        return 2
    connection = _connect(args.store)
    try:
        audit = load_candidate_audit(connection, chain_id=args.chain_id)
        cleanup: dict[str, int] = {}
        if args.apply:
            cleanup = apply_cleanup(connection, audit)
            connection.commit()
        report = {
            "generatedAt": _utc_now_iso(),
            "mode": "apply" if args.apply else "read-only",
            "store": str(args.store),
            **audit,
            "cleanup": cleanup,
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(
                f"{report['mode']} candidateTotal={report['candidateTotal']} "
                f"eligible={report['eligibleTotal']} "
                f"ineligible={report['ineligibleTotal']} "
                f"affectedClosedLoops={report['affectedClosedLoopCount']} "
                f"affectedRecommendations={report['affectedRecommendationCount']}"
            )
            for pool_id in report["ineligiblePoolIds"][:20]:
                reasons = ",".join(report["reasonsByPool"].get(pool_id, []))
                print(f"  {pool_id}: {reasons}")
            if len(report["ineligiblePoolIds"]) > 20:
                print(f"  ... {len(report['ineligiblePoolIds']) - 20} more")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
