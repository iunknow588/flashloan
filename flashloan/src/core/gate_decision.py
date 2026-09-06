"""Pure execution-gate validation helpers.

The validator deliberately has no RPC, database, or broadcast side effects. It
turns an execution-intent shaped mapping into auditable reasons so callers can
fail closed before acquiring a nonce or submitting a transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from core.contracts import validate_scope_contract


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def validate_execution_gate(
    intent: Mapping[str, Any],
    *,
    now: datetime | None = None,
    nonce_reserved: bool = False,
    seen_idempotency_keys: set[str] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Validate the local, pre-broadcast execution contract.

    ``nonce_reserved`` is an explicit caller fact; this helper never allocates
    a nonce. A key present in ``seen_idempotency_keys`` is rejected to avoid a
    repeated submission in the same execution context.
    """
    reasons: list[str] = []
    scope = intent.get("scope") if isinstance(intent.get("scope"), Mapping) else {}
    try:
        validate_scope_contract(
            market_id=scope.get("market_id"),
            chain_id=scope.get("chain_id"),
            network=scope.get("network"),
            protocol=scope.get("protocol"),
        )
    except ValueError:
        reasons.append("invalid_scope")

    target_hash = str(intent.get("target_hash") or "").strip()
    if not target_hash:
        reasons.append("target_hash_missing")

    key = str(intent.get("idempotency_key") or "").strip()
    if not key:
        reasons.append("idempotency_key_missing")
    elif seen_idempotency_keys is not None and key in seen_idempotency_keys:
        reasons.append("idempotency_key_reused")

    deadline = _parse_time(intent.get("deadline"))
    if intent.get("deadline") and deadline is None:
        reasons.append("deadline_invalid")
    elif deadline is not None:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if deadline <= current:
            reasons.append("deadline_expired")

    if not nonce_reserved:
        reasons.append("nonce_not_reserved")

    return not reasons, tuple(reasons)


__all__ = ["validate_execution_gate"]
