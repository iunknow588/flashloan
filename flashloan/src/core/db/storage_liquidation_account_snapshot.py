"""Account pool snapshot loader extracted from storage_liquidation_pool."""

from __future__ import annotations

from typing import Any

from core.db.liquidation_account_mapping import core_opportunity_payload_from_row as _core_opportunity_payload_from_row
from core.db.scoped_adapter import validate_scoped_row
from core.db.storage_common import db_connection, require_psycopg
from core.db.storage_liquidation_pool_helpers import market_scope as _market_scope
from core.db.storage_liquidation_reports import (
    account_report_with_summary as _account_report_with_summary,
    json_or_default as _json_or_default,
    load_historical_account_report_sources,
    merge_account_report_sources as _merge_account_report_sources,
)
from core.liquidation_pool_mapping import (
    merge_summary_with_registry as _merge_summary_with_registry,
)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_liquidation_account_pool_snapshot(
    database_url: str,
    account: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    normalized = str(account or "").strip()
    if not normalized:
        return {"found": False, "error": "account is required"}
    scope = _market_scope(market_id, chain_id)
    require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT market_id, chain_id, network, protocol,
                       account, source, active, last_scanned_at, last_health_factor,
                       last_status, last_health_factor_band, last_candidate_count,
                       last_total_collateral_base, last_total_debt_base,
                       activity_tier, last_summary_json, last_report_json
                FROM liquidation_accounts
                WHERE market_id = %s AND chain_id = %s AND lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            registry = cursor.fetchone()
            if registry:
                registry = validate_scoped_row(
                    {
                        "market_id": registry[0],
                        "chain_id": registry[1],
                        "network": registry[2],
                        "protocol": registry[3],
                    },
                    scope,
                ) | {"_raw": registry}
            cursor.execute(
                """
                WITH ranked AS (
                  SELECT row_number() OVER (ORDER BY health_factor ASC, updated_at DESC) AS rn,
                         account, health_factor, status, health_factor_band,
                         total_collateral_base, total_debt_base, candidate_count,
                         last_scanned_at, report_json
                  FROM liquidation_borrow_health_pool
                  WHERE active = TRUE AND market_id = %s AND chain_id = %s
                )
                SELECT * FROM ranked WHERE lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            borrow = cursor.fetchone()
            cursor.execute(
                """
                WITH ranked AS (
                  SELECT row_number() OVER (ORDER BY priority_score DESC, health_factor ASC, last_scanned_at DESC) AS rn,
                         account, health_factor, priority_score, total_collateral_base,
                         total_debt_base, best_debt_asset, best_collateral_asset,
                         debt_to_cover_units, estimated_operator_net_profit_usd,
                         estimated_gas_cost_usd, quote_viable, static_call_status,
                         payload_state, blocked_reasons_json, last_scanned_at, metadata_json
                  FROM liquidation_core_opportunity_pool
                  WHERE active = TRUE AND market_id = %s AND chain_id = %s
                )
                SELECT * FROM ranked WHERE lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            core = cursor.fetchone()
            cursor.execute(
                """
                WITH ranked AS (
                  SELECT row_number() OVER (ORDER BY priority_score DESC, health_factor ASC, updated_at DESC) AS rn,
                         account, health_factor, priority_score, total_collateral_base,
                         total_debt_base, candidate_count, last_scanned_at, report_json
                  FROM liquidation_high_frequency_pool
                  WHERE active = TRUE AND market_id = %s AND chain_id = %s
                )
                SELECT * FROM ranked WHERE lower(account)=lower(%s)
                """,
                (scope["market_id"], scope["chain_id"], normalized),
            )
            high = cursor.fetchone()
            latest_valid_scan, latest_positions_scan = load_historical_account_report_sources(
                cursor,
                market_id=scope["market_id"],
                chain_id=scope["chain_id"],
                account=normalized,
                expected_scope=scope,
            )

    report_sources: list[dict[str, Any]] = []
    recommended_candidate: dict[str, Any] = {}
    if borrow:
        borrow_report = _json_or_default(borrow[9], {})
        if isinstance(borrow_report, dict):
            report_sources.append(borrow_report)
    if core:
        metadata = _json_or_default(core[16], {})
        if isinstance(metadata, dict):
            core_report = metadata.get("report") if isinstance(metadata.get("report"), dict) else {}
            if core_report:
                report_sources.append(core_report)
            recommended_candidate = metadata.get("recommended_candidate") if isinstance(metadata.get("recommended_candidate"), dict) else {}
    if high:
        high_report = _json_or_default(high[8], {})
        if isinstance(high_report, dict):
            report_sources.append(high_report)
    if registry:
        registry_row = registry["_raw"]
        registry_report = _json_or_default(registry_row[16], {})
        if isinstance(registry_report, dict):
            report_sources.append(registry_report)
    current_positions_present = any(
        isinstance(source.get("positions"), list) and bool(source.get("positions"))
        for source in report_sources
        if isinstance(source, dict)
    )
    if latest_valid_scan:
        valid_scan_report = _account_report_with_summary(latest_valid_scan[2], latest_valid_scan[1])
        if valid_scan_report:
            report_sources.append(valid_scan_report)
    if latest_positions_scan:
        positions_scan_report = _account_report_with_summary(latest_positions_scan[2], latest_positions_scan[1])
        if positions_scan_report:
            report_sources.append(positions_scan_report)
    report = _merge_account_report_sources(report_sources)

    summary = dict(report.get("summary") or {})
    if not summary and report:
        summary = {
            "health_factor": report.get("health_factor"),
            "status": report.get("status"),
            "health_factor_band": report.get("health_factor_band"),
            "candidate_count": len(report.get("liquidation_candidates") or []),
            "total_collateral_base": report.get("total_collateral_base") or report.get("total_collateral_in_base_currency"),
            "total_debt_base": report.get("total_debt_base") or report.get("total_debt_in_base_currency"),
        }
    if registry:
        summary = _merge_summary_with_registry(summary, registry["_raw"])
    positions = report.get("positions") or []
    if isinstance(positions, list):
        summary.setdefault("positions_count", len(positions))
        summary.setdefault(
            "debt_positions_count",
            sum(
                1
                for position in positions
                if isinstance(position, dict)
                and (
                    _float_or_zero(position.get("stable_debt_amount")) > 0
                    or _float_or_zero(position.get("variable_debt_amount")) > 0
                    or _float_or_zero(position.get("debt_value_base")) > 0
                )
            ),
        )
        summary.setdefault(
            "collateral_positions_count",
            sum(
                1
                for position in positions
                if isinstance(position, dict)
                and (
                    bool(position.get("usage_as_collateral_enabled"))
                    and _float_or_zero(position.get("collateral_amount")) > 0
                )
            ),
        )

    core_payload = None
    if core:
        core_payload = _core_opportunity_payload_from_row(core, summary)

    return {
        "found": bool(registry or borrow or core or high),
        "cached": True,
        "source": "database_pool_cache",
        "market_id": scope["market_id"],
        "chain_id": scope["chain_id"],
        "account": normalized,
        "summary": summary,
        "liquidation_profit": report.get("liquidation_profit") or {},
        "recommended_candidate": recommended_candidate or None,
        "liquidation_candidates": report.get("liquidation_candidates") or [],
        "positions": positions,
        "positions_source": (
            "database_scan_report"
            if positions and current_positions_present
            else "database_health_scan_history"
            if positions
            else "unavailable"
        ),
        "context": {
            "source": "database_pool_cache",
            "report_scanned_at": (
                latest_valid_scan[0].isoformat()
                if latest_valid_scan and latest_valid_scan[0]
                else None
            ),
            "positions_scanned_at": (
                latest_positions_scan[0].isoformat()
                if latest_positions_scan and latest_positions_scan[0]
                else None
            ),
            "registry": {
                "active": bool(registry["_raw"][6]) if registry else False,
                "market_id": registry["market_id"] if registry else scope["market_id"],
                "chain_id": int(registry["chain_id"]) if registry and registry["chain_id"] is not None else scope["chain_id"],
                "network": registry["network"] if registry else None,
                "protocol": registry["protocol"] if registry else None,
                "source": registry["_raw"][5] if registry else None,
                "activity_tier": registry["_raw"][14] if registry else None,
                "last_scanned_at": registry["_raw"][7].isoformat() if registry and registry["_raw"][7] else None,
            },
            "borrow_pool": {
                "active": bool(borrow),
                "rank": int(borrow[0]) if borrow else None,
                "last_scanned_at": borrow[8].isoformat() if borrow and borrow[8] else None,
            },
            "core_opportunity": core_payload,
            "high_frequency": {
                "active": bool(high),
                "rank": int(high[0]) if high else None,
                "last_scanned_at": high[7].isoformat() if high and high[7] else None,
            },
        },
    }


__all__ = ["load_liquidation_account_pool_snapshot"]
