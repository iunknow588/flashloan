"""Pure helpers for scoping and ranking CoW execution evidence."""

from __future__ import annotations

from core.sensitive_data import redact_sensitive_text


def data_error_message(error: object | None) -> str | None:
    if error is None:
        return None
    message = str(error)
    if not message:
        message = repr(error)
    if message and message != repr(error):
        message = f"{type(error).__name__}: {message}"
    return redact_sensitive_text(message)


def filter_network_claims(claims: list[dict], networks: list[str]) -> list[dict]:
    selected = {
        str(network or "").strip().lower()
        for network in networks
        if str(network or "").strip()
    }
    if not selected:
        return list(claims or [])
    return [
        claim
        for claim in claims or []
        if str(claim.get("network") or "").strip().lower() in selected
    ]


def _attempt_evidence(row: dict) -> dict:
    review = row.get("review_summary") if isinstance(row.get("review_summary"), dict) else {}
    review_evidence = review.get("onchain_evidence") if isinstance(review.get("onchain_evidence"), dict) else {}
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    quote_evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    return review_evidence or quote_evidence or {}


def _attempt_evidence_rank(row: dict) -> int:
    evidence = _attempt_evidence(row)
    status = str(evidence.get("status") or row.get("priority_reason") or row.get("state") or "").strip().lower()
    if evidence.get("trustedForExecution") or evidence.get("trusted_for_execution"):
        return 50
    if evidence.get("readyForTransactionPreflight") or evidence.get("ready_for_transaction_preflight"):
        return 40
    if status == "live_verified":
        return 35
    if status in {"not_live_verified", "live_quote_failed"}:
        return 30
    if status == "live_evidence_deferred":
        return 10
    return 0


def _attempt_route_key(row: dict) -> tuple[str, str]:
    network = str(row.get("network") or row.get("cow_network") or "").strip().lower()
    pair = str(row.get("pair") or row.get("symbol_pair") or row.get("route_key") or "").strip()
    if not pair:
        route = row.get("route_path") if isinstance(row.get("route_path"), list) else []
        pair = " -> ".join(str(item).strip() for item in route if str(item).strip())
    return network, pair


def _attempt_time_key(row: dict) -> str:
    return str(row.get("created_at") or row.get("observed_at") or "")


def dedupe_attempts_by_route_evidence(
    rows: list[dict],
    *,
    limit: int,
    dedupe_by_route_evidence: bool = True,
) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = _attempt_route_key(row)
        if not dedupe_by_route_evidence:
            tx = str(row.get("tx_hash") or row.get("order_uid") or row.get("uid") or "").strip()
            if tx:
                key = (key[0], key[1], tx)
            else:
                key = (
                    key[0],
                    key[1],
                    _attempt_time_key(row),
                    str(row.get("execution_phase") or row.get("state") or "").strip().lower(),
                )
        if not key[1]:
            key = (key[0], f"__row_{len(best)}_{_attempt_time_key(row)}")
        current = best.get(key)
        candidate_score = (_attempt_evidence_rank(row), _attempt_time_key(row))
        current_score = (_attempt_evidence_rank(current), _attempt_time_key(current)) if current else (-1, "")
        if current is None or candidate_score > current_score:
            best[key] = row
    deduped = list(best.values())
    deduped.sort(key=lambda item: (_attempt_evidence_rank(item), _attempt_time_key(item)), reverse=True)
    return deduped[: max(1, int(limit))]


# Transitional aliases preserve the private names used by the route module.
_filter_network_claims = filter_network_claims
_dedupe_attempts_by_route_evidence = dedupe_attempts_by_route_evidence


__all__ = [
    "data_error_message",
    "filter_network_claims",
    "dedupe_attempts_by_route_evidence",
]
