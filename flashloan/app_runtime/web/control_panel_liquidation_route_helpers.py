from __future__ import annotations

import logging

from flask import jsonify

from bot.liquidation.route_response import (
    liquidation_failure_response as build_liquidation_failure_response,
    stale_liquidatable_context_response,
)
from core.route_context import RouteContext
from web.data_routes.liquidation_route_request import (
    validate_request_liquidatable_context as _validate_request_liquidatable_context,
)

ROUTE_CONTEXT = RouteContext()
logger = logging.getLogger(__name__)


def panel_call(name: str, *args, **kwargs):
    return ROUTE_CONTEXT.panel_call(name, *args, **kwargs)


def validate_request_liquidatable_context(account: str) -> dict:
    return _validate_request_liquidatable_context(account, panel_call)


def ensure_request_liquidatable_context(account: str) -> dict | None:
    validation = validate_request_liquidatable_context(account)
    if validation.get("present") and not validation.get("fresh"):
        response, status = stale_liquidatable_context_response(account, validation)
        return jsonify(response), status
    return None


def liquidation_coverage_payload(
    pool_address: str,
    panel=None,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict:
    source_panel = panel or ROUTE_CONTEXT.panel
    try:
        progress = getattr(source_panel, "liquidation_discovery_progress")(
            pool_address,
            market_id=market_id,
            chain_id=chain_id,
        )
    except TypeError:
        if market_id is not None or chain_id is not None:
            raise
        progress = getattr(source_panel, "liquidation_discovery_progress")(pool_address)
    latest_to = progress.get("latest_recent_to_block")
    earliest_from = progress.get("earliest_backfill_from_block")
    latest_gap_from = None
    latest_gap_to = None
    if latest_to is not None and earliest_from is not None and int(earliest_from) > int(latest_to) + 1:
        latest_gap_from = int(latest_to) + 1
        latest_gap_to = int(earliest_from) - 1
    return {
        "pool_address": pool_address or None,
        "covered_from_block": earliest_from,
        "covered_to_block": latest_to,
        "latest_gap_from_block": latest_gap_from,
        "latest_gap_to_block": latest_gap_to,
        "has_gap": latest_gap_from is not None,
        "progress": progress,
    }


def liquidation_failure_response(account: str, payload: dict | None, error: Exception) -> dict:
    """Keep route-context lookups at the route boundary."""
    cached_snapshot = None
    execution_controls = None
    if not isinstance(payload, dict):
        try:
            cached_snapshot = panel_call("liquidation_account_cached_payload", account)
        except Exception:
            cached_snapshot = {}
    else:
        try:
            execution_controls = panel_call("liquidation_execution_controls")
        except Exception:
            execution_controls = None
    return build_liquidation_failure_response(
        account,
        payload,
        error,
        cached_snapshot=cached_snapshot,
        execution_controls=execution_controls,
    )
