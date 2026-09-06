"""Deterministic execution risk fault injection for preflight tests."""

from __future__ import annotations

from typing import Any, Mapping


RISK_FIELDS = ("gas_cost_usd", "slippage_usd", "flash_fee_usd", "mev_loss_usd", "balance_shortfall_usd", "quote_age_seconds")


def evaluate_execution_risks(
    facts: Mapping[str, Any],
    *,
    max_quote_age_seconds: float = 30.0,
    max_cost_usd: float | None = None,
) -> tuple[bool, tuple[str, ...], dict[str, float]]:
    """Return an auditable, fail-closed decision from caller-provided facts."""
    reasons: list[str] = []
    costs: dict[str, float] = {}
    for field in RISK_FIELDS:
        try:
            value = float(facts.get(field) or 0)
        except (TypeError, ValueError):
            reasons.append(f"{field}_invalid")
            continue
        if value < 0:
            reasons.append(f"{field}_negative")
        costs[field] = value
    if costs.get("balance_shortfall_usd", 0) > 0:
        reasons.append("balance_insufficient")
    if costs.get("quote_age_seconds", 0) > max(0.0, float(max_quote_age_seconds)):
        reasons.append("quote_stale")
    total = sum(costs.get(field, 0) for field in RISK_FIELDS[:5])
    costs["total_cost_usd"] = total
    if max_cost_usd is not None and total > float(max_cost_usd):
        reasons.append("execution_cost_too_high")
    return not reasons, tuple(reasons), costs


__all__ = ["RISK_FIELDS", "evaluate_execution_risks"]
