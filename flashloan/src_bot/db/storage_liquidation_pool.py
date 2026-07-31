import json
from typing import Any

from db.storage_common import require_psycopg


def sync_liquidation_borrow_health_pool(
    database_url: str,
    rows: list[dict[str, Any]],
    watch_health_factor: float = 1.5,
) -> int:
    psycopg = require_psycopg()
    active_accounts: list[str] = []
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            for row in rows:
                account = str(row.get("account") or "").strip()
                health_factor = row.get("health_factor")
                if not account or not isinstance(health_factor, (int, float)):
                    continue
                is_active = float(health_factor) < float(watch_health_factor)
                if is_active:
                    active_accounts.append(account)
                summary = {
                    "health_factor": health_factor,
                    "status": row.get("status"),
                    "health_factor_band": row.get("health_factor_band"),
                    "candidate_count": len(row.get("liquidation_candidates") or []),
                }
                cursor.execute(
                    """
                    INSERT INTO liquidation_borrow_health_pool (
                        account, health_factor, status, health_factor_band,
                        total_collateral_base, total_debt_base, candidate_count,
                        summary_json, report_json, active, last_scanned_at, updated_at
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                    ON CONFLICT (account) DO UPDATE SET
                        health_factor = EXCLUDED.health_factor,
                        status = EXCLUDED.status,
                        health_factor_band = EXCLUDED.health_factor_band,
                        total_collateral_base = EXCLUDED.total_collateral_base,
                        total_debt_base = EXCLUDED.total_debt_base,
                        candidate_count = EXCLUDED.candidate_count,
                        summary_json = EXCLUDED.summary_json,
                        report_json = EXCLUDED.report_json,
                        active = EXCLUDED.active,
                        last_scanned_at = NOW(),
                        updated_at = NOW()
                    """,
                    (
                        account,
                        health_factor,
                        row.get("status"),
                        row.get("health_factor_band"),
                        row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                        row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
                        len(row.get("liquidation_candidates") or []),
                        json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                        json.dumps(row, ensure_ascii=True, separators=(",", ":")),
                        is_active,
                    ),
                )
            cursor.execute(
                "UPDATE liquidation_borrow_health_pool SET active = FALSE, updated_at = NOW() "
                "WHERE active = TRUE AND health_factor >= %s",
                (float(watch_health_factor),),
            )
    return len(active_accounts)


def load_liquidation_borrow_health_pool(database_url: str, limit: int = 500) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with psycopg.connect(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT account, health_factor, status, health_factor_band,
                       total_collateral_base, total_debt_base, candidate_count,
                       last_scanned_at, report_json
                FROM liquidation_borrow_health_pool
                WHERE active = TRUE
                ORDER BY health_factor ASC, updated_at DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        report = {}
        try:
            report = json.loads(row[8]) if row[8] else {}
        except json.JSONDecodeError:
            report = {}
        result.append(
            {
                "account": row[0],
                "health_factor": row[1],
                "status": row[2],
                "health_factor_band": row[3],
                "total_collateral_base": row[4],
                "total_debt_base": row[5],
                "candidate_count": row[6],
                "last_scanned_at": row[7].isoformat() if row[7] else None,
                "report": report,
            }
        )
    return result
