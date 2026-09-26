from __future__ import annotations

from typing import Any


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _row_profit_metric(row: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in ("observedNetProfitUsd", "netProfitUsd", "referenceNetProfitUsd"):
        value = row.get(key)
        if value is not None:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            return parsed, "usd"
    for key in ("referenceNetProfitAmount", "netProfitAmount"):
        value = row.get(key)
        if value is not None:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            return parsed, "borrow_token"
    return None, None


def _probe_records_from_size_analysis(
    *,
    size: dict[str, Any],
    symbol: str | None,
    selected_amount: float | None,
    recommended_amount: float | None,
) -> list[dict[str, Any]]:
    rows = size.get("rows") if isinstance(size.get("rows"), list) else []
    records: list[dict[str, Any]] = []
    seen: dict[float, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        borrow_amount = _safe_float(row.get("borrowAmount"))
        reference_input = _safe_float(row.get("referenceInputAmount"))
        record_amount = borrow_amount or reference_input or _safe_float(row.get("amount") or row.get("inputAmount"))
        if record_amount is None:
            continue
        record_symbol = (
            row.get("borrowSymbol")
            or row.get("referenceInputSymbol")
            or row.get("symbol")
            or symbol
        )
        profit_amount, profit_basis = _row_profit_metric(row)
        record: dict[str, Any] = {
            "amount": float(record_amount),
            "borrowAmount": float(record_amount),
            "borrowSymbol": record_symbol,
            "symbol": record_symbol,
            "referenceInputSymbol": row.get("referenceInputSymbol") or record_symbol,
            "referenceFinalOutputAmount": row.get("referenceFinalOutputAmount"),
            "referenceFinalOutputSymbol": row.get("referenceFinalOutputSymbol") or record_symbol,
            "referenceNetProfitAmount": row.get("referenceNetProfitAmount"),
            "referenceNetReturnBps": row.get("referenceNetReturnBps"),
            "profitAmount": profit_amount,
            "profitBasis": profit_basis,
            "executableCandidate": bool(row.get("executableCandidate")),
            "staticDepthStatus": row.get("staticDepthStatus"),
            "staticDepthAvailable": row.get("staticDepthAvailable"),
            "maxStaticDepthImpactBps": row.get("maxStaticDepthImpactBps"),
            "observedNetProfitUsd": row.get("observedNetProfitUsd"),
            "observedNetProfitBps": row.get("observedNetProfitBps"),
            "notionalUsd": row.get("notionalUsd"),
            "borrowBasis": row.get("borrowBasis"),
            "chainSubmissionCandidate": row.get("chainSubmissionCandidate"),
            "recommendationLevel": row.get("recommendationLevel"),
            "sourceRow": row,
        }
        key = round(float(record_amount), 12)
        seen[key] = record
    if not seen:
        token_steps = size.get("tokenAmountSteps") if isinstance(size.get("tokenAmountSteps"), list) else []
        for step in token_steps:
            step_amount = _safe_float(step)
            if step_amount is None:
                continue
            record = {
                "amount": float(step_amount),
                "borrowAmount": float(step_amount),
                "borrowSymbol": symbol,
                "symbol": symbol,
                "referenceInputSymbol": symbol,
                "referenceFinalOutputAmount": None,
                "referenceFinalOutputSymbol": symbol,
                "referenceNetProfitAmount": None,
                "referenceNetReturnBps": None,
                "profitAmount": None,
                "profitBasis": None,
                "executableCandidate": False,
                "staticDepthStatus": None,
                "sourceRow": None,
            }
            key = round(float(step_amount), 12)
            seen[key] = record
        recommended = _safe_float(size.get("recommendedBorrowInputAmount"))
        if recommended is not None and round(float(recommended), 12) not in seen:
            record = {
                "amount": float(recommended),
                "borrowAmount": float(recommended),
                "borrowSymbol": symbol,
                "symbol": symbol,
                "referenceInputSymbol": symbol,
                "referenceFinalOutputAmount": None,
                "referenceFinalOutputSymbol": symbol,
                "referenceNetProfitAmount": None,
                "referenceNetReturnBps": None,
                "profitAmount": None,
                "profitBasis": None,
                "executableCandidate": True,
                "staticDepthStatus": None,
                "sourceRow": None,
            }
            seen[round(float(recommended), 12)] = record
    for record in seen.values():
        record_amount = float(record.get("amount") or 0)
        record["isBest"] = bool(
            selected_amount is not None and abs(record_amount - float(selected_amount)) < float(selected_amount) * 1e-9
        )
        record["isRecommended"] = bool(
            recommended_amount is not None and abs(record_amount - float(recommended_amount)) < float(recommended_amount) * 1e-9
        )
    return [seen[key] for key in sorted(seen)]


__all__ = [
    "_probe_records_from_size_analysis",
    "_row_profit_metric",
    "_safe_float",
]
