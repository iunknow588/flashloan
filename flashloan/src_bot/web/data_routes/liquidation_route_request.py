"""Request DTO parsing for liquidation routes."""

from __future__ import annotations

from flask import request

from core.market_config import DEFAULT_BNB_CHAIN_ID, DEFAULT_BNB_MARKET_ID
from core.market_identity import canonical_market_id_from_legacy
from bot.debt_pool import validate_liquidatable_context


def request_int_arg(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def optional_request_int_arg(name: str) -> int | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def request_market_kwargs() -> dict:
    try:
        args = request.args
    except RuntimeError:
        return {}
    market_id = args.get("market_id", "").strip() or None
    raw_chain_id = args.get("chain_id", "").strip()
    try:
        chain_id = int(raw_chain_id) if raw_chain_id else None
    except (TypeError, ValueError):
        chain_id = None
    return {
        **({"market_id": market_id} if market_id is not None else {}),
        **({"chain_id": chain_id} if chain_id is not None else {}),
    }


def request_execution_target_kwargs() -> dict:
    kwargs = {}
    borrow_index = optional_request_int_arg("borrow_index")
    candidate_index = optional_request_int_arg("candidate_index")
    if borrow_index is not None:
        kwargs["borrow_index"] = borrow_index
    if candidate_index is not None:
        kwargs["candidate_index"] = candidate_index
    return kwargs


def is_bnb_venus_request(market_id: str | None = None, chain_id: int | None = None) -> bool:
    normalized_market_id = canonical_market_id_from_legacy(market_id) or str(market_id or "").strip().lower()
    bnb_venus_market_id = canonical_market_id_from_legacy(DEFAULT_BNB_MARKET_ID) or DEFAULT_BNB_MARKET_ID
    if normalized_market_id == bnb_venus_market_id:
        return True
    try:
        return market_id is None and int(chain_id) == DEFAULT_BNB_CHAIN_ID
    except (TypeError, ValueError):
        return False


def request_liquidatable_context(account: str) -> dict | None:
    context_keys = {"checked_at", "block_number", "candidate_hash", "source_pool", "health_factor"}
    if not any(request.args.get(key) for key in context_keys) and request.args.get("from", "").strip() != "debt_pool":
        return None
    return {"account": account, **{key: request.args.get(key) for key in context_keys}}


def validate_request_liquidatable_context(account: str, panel_call) -> dict:
    context = request_liquidatable_context(account)
    if context is None:
        return {"present": False, "fresh": True, "blocked_reasons": [], "context": None}
    controls = panel_call("liquidation_execution_controls")
    return {
        "present": True,
        **validate_liquidatable_context(
            context,
            account=account,
            max_age_seconds=int(controls.get("max_payload_age_seconds") or 30),
            latest_block_number=optional_request_int_arg("latest_block_number"),
            max_block_lag=request_int_arg("max_block_lag", 3, minimum=0),
        ),
    }


__all__ = [
    "is_bnb_venus_request",
    "optional_request_int_arg",
    "request_execution_target_kwargs",
    "request_int_arg",
    "request_liquidatable_context",
    "request_market_kwargs",
    "validate_request_liquidatable_context",
]
