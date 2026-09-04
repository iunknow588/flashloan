from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.chain_data_registry import global_chain_id
from core.market_identity import canonical_market_id_from_legacy


DATA_SOURCES = (
    "database",
    "jsonl",
    "runtime_state",
    "runtime_cache",
    "evidence_package",
    "builtin_fallback",
)


@dataclass(frozen=True)
class MarketScope:
    market_id: str
    chain_id: int | None
    network: str
    feature: str
    workflow: str
    execution_mode: str | None
    scope_type: str
    global_chain_id: str = ""
    chain_key: str = ""
    protocol_id: str = ""
    legacy_market_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        canonical_market_id = canonical_market_id_from_legacy(self.market_id) or self.market_id
        legacy_market_id = self.legacy_market_id or (
            self.market_id if self.market_id != canonical_market_id else None
        )
        gid = self.global_chain_id or global_chain_id(
            chain_id=self.chain_id,
            network=self.network,
            market_id=canonical_market_id,
        )
        return {
            "market_id": canonical_market_id,
            "chain_id": self.chain_id,
            "global_chain_id": gid,
            "chain_key": self.chain_key or gid,
            "network": self.network,
            "feature": self.feature,
            "protocol_id": self.protocol_id,
            "protocol": self.protocol_id,
            "legacy_market_id": legacy_market_id,
            "workflow": self.workflow,
            "execution_mode": self.execution_mode,
            "scope_type": self.scope_type,
        }

    @classmethod
    def from_selection(
        cls,
        selection: Any,
        *,
        workflow: str,
        execution_mode: str | None,
        scope_type: str,
    ) -> "MarketScope":
        return cls(
            market_id=str(selection.market_id),
            chain_id=selection.chain_id,
            global_chain_id=str(getattr(selection, "global_chain_id", "") or ""),
            chain_key=str(getattr(selection, "global_chain_id", "") or ""),
            network=str(selection.network),
            feature=str(selection.feature),
            protocol_id=str(getattr(selection, "protocol", "") or getattr(selection, "protocol_id", "") or ""),
            legacy_market_id=getattr(selection, "legacy_market_id", None),
            workflow=str(workflow),
            execution_mode=execution_mode,
            scope_type=str(scope_type),
        )


@dataclass(frozen=True)
class ExecutionGate:
    ready: bool
    execution_enabled: bool
    broadcast_enabled: bool
    blocked_reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "execution_enabled": self.execution_enabled,
            "broadcast_enabled": self.broadcast_enabled,
            "blocked_reasons": list(self.blocked_reasons),
            "evidence_refs": list(self.evidence_refs),
        }


REQUIRED_SCOPE_KEYS = ("market_id", "chain_id", "network", "protocol")


def validate_scope_contract(
    *,
    market_id: object,
    chain_id: object,
    network: object,
    protocol: object,
) -> dict[str, Any]:
    missing = []
    if not str(market_id or "").strip():
        missing.append("market_id")
    if chain_id is None or not str(chain_id).strip():
        missing.append("chain_id")
    if not str(network or "").strip():
        missing.append("network")
    if not str(protocol or "").strip():
        missing.append("protocol")
    if missing:
        raise ValueError(f"scope missing: {', '.join(missing)}")
    try:
        normalized_chain_id = int(chain_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"scope chain_id must be integer: {chain_id!r}") from exc
    return {
        "market_id": str(market_id).strip(),
        "chain_id": normalized_chain_id,
        "network": str(network).strip().lower(),
        "protocol": str(protocol).strip().lower(),
    }


def scope_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    required = REQUIRED_SCOPE_KEYS
    for key in required:
        left_value = left.get(key) if isinstance(left, dict) else None
        right_value = right.get(key) if isinstance(right, dict) else None
        if left_value != right_value:
            raise ValueError(f"scope mismatch on {key}: {left_value!r} != {right_value!r}")
    return True


def validate_scoped_payload(payload: object, expected_scope: dict[str, Any]) -> dict[str, Any]:
    """Validate a persisted payload carries exactly the requested scope."""
    if not isinstance(payload, dict):
        raise ValueError("scoped payload must be an object")
    keys = ("market_id", "chain_id", "network", "protocol")
    normalized_expected = validate_scope_contract(**{key: expected_scope.get(key) for key in keys})
    embedded = payload.get("scope") if isinstance(payload.get("scope"), dict) else payload
    normalized_embedded = validate_scope_contract(**{key: embedded.get(key) for key in keys})
    scope_matches(normalized_embedded, normalized_expected)
    return normalized_embedded


def normalize_data_source(value: object, *, default: str = "builtin_fallback") -> str:
    source = str(value or "").strip().lower()
    return source if source in DATA_SOURCES else default


def source_metadata(
    source: object,
    *,
    freshness: object = None,
    fallback_reason: object = None,
) -> dict[str, Any]:
    return {
        "source": normalize_data_source(source),
        "freshness": freshness,
        "fallback_reason": str(fallback_reason).strip() if fallback_reason else None,
    }


def dedupe_reasons(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        reason = str(value or "").strip()
        if reason and reason not in result:
            result.append(reason)
    return tuple(result)

EVIDENCE_STATUSES = ("valid", "stale", "invalid", "insufficient_data", "not_configured", "not_ready")
CAPABILITY_STATES = ("disabled", "read_only", "quote_ready", "execution_ready")


@dataclass(frozen=True)
class EvidenceEnvelope:
    evidence_id: str
    source: str
    observed_at: str
    freshness: str | None = None
    expires_at: str | None = None
    fallback_reason: str | None = None
    schema_version: str = "1"
    integrity: str = "unverified"
    status: str = "valid"

    def __post_init__(self) -> None:
        if not str(self.evidence_id).strip():
            raise ValueError("evidence_id is required")
        if not str(self.observed_at).strip():
            raise ValueError("observed_at is required")
        if str(self.source).strip().lower() not in DATA_SOURCES:
            raise ValueError(f"unknown evidence source: {self.source}")
        if self.status not in EVIDENCE_STATUSES:
            raise ValueError(f"unknown evidence status: {self.status}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": normalize_data_source(self.source),
            "observed_at": self.observed_at,
            "freshness": self.freshness,
            "expires_at": self.expires_at,
            "fallback_reason": self.fallback_reason,
            "schema_version": self.schema_version,
            "integrity": self.integrity,
            "status": self.status,
        }


@dataclass(frozen=True)
class GateDecision:
    gate: str
    allowed: bool
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    decided_at: str | None = None
    trace_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"gate": self.gate, "allowed": self.allowed, "reasons": list(self.reasons),
                "evidence_refs": list(self.evidence_refs), "decided_at": self.decided_at,
                "trace_id": self.trace_id}


@dataclass(frozen=True)
class ExecutionIntent:
    workflow: str
    scope: dict[str, Any]
    target_hash: str
    idempotency_key: str
    evidence_refs: tuple[str, ...] = ()
    deadline: str | None = None

    def __post_init__(self) -> None:
        if self.workflow not in {"liquidation", "dex_arbitrage"}:
            raise ValueError(f"unsupported execution workflow: {self.workflow}")
        if not isinstance(self.scope, dict):
            raise ValueError("execution scope must be an object")
        try:
            validate_scope_contract(
                market_id=self.scope.get("market_id"),
                chain_id=self.scope.get("chain_id"),
                network=self.scope.get("network"),
                protocol=self.scope.get("protocol"),
            )
        except ValueError as exc:
            raise ValueError(f"execution scope invalid: {exc}") from exc
        if not str(self.target_hash).strip() or not str(self.idempotency_key).strip():
            raise ValueError("target_hash and idempotency_key are required")

    def as_dict(self) -> dict[str, Any]:
        return {"workflow": self.workflow, "scope": dict(self.scope),
                "target_hash": self.target_hash, "idempotency_key": self.idempotency_key,
                "evidence_refs": list(self.evidence_refs), "deadline": self.deadline}
