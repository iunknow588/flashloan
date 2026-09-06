"""Pure mapping and validation helpers for CoW execution persistence."""

from __future__ import annotations

from typing import Any

from core.evidence_scope import infer_evidence_scope


def scope_consistency_reasons(row: dict[str, Any]) -> list[str]:
    """Return contradictions between top-level and nested evidence scope."""
    scope = infer_evidence_scope(row)
    reasons: list[str] = []

    def as_int(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    for key, nested_key, reason in (
        ("network", "network", "network_scope_mismatch"),
        ("chain_id", "chainId", "chain_id_scope_mismatch"),
    ):
        top = row.get(key)
        nested = evidence.get(nested_key)
        if top not in (None, "") and nested not in (None, ""):
            top_value = str(top).strip().lower() if key == "network" else as_int(top)
            nested_value = str(nested).strip().lower() if key == "network" else as_int(nested)
            if top_value != nested_value:
                reasons.append(reason)

    top_market = row.get("market_id")
    nested_market = evidence.get("market_id")
    if top_market and nested_market and str(top_market).strip().lower() != str(nested_market).strip().lower():
        reasons.append("market_id_scope_mismatch")
    if scope.network == "" and (row.get("network") or evidence.get("network")):
        reasons.append("network_scope_unreadable")
    return reasons


__all__ = ["scope_consistency_reasons"]
