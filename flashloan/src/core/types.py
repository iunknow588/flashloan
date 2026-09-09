"""Cross-module type facade for the split packages.

During the compatibility phase this re-exports the canonical contracts and
error taxonomy so new split packages can build against ``src.core.types``
without importing each other's concrete implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.capabilities import (
    Capability,
    CapabilityDecision,
    CapabilityKey,
    CapabilityRegistry,
    CapabilityState,
)
from core.contracts import (
    DATA_SOURCES,
    EvidenceEnvelope,
    ExecutionGate,
    ExecutionIntent,
    GateDecision,
    MarketScope,
    validate_scoped_payload,
    normalize_data_source,
    source_metadata,
)
from core.errors import (
    ERROR_CODES,
    RETRY_POLICIES,
    error_payload,
    normalize_error_code,
)
from core.error_registry import ERROR_REGISTRY, ErrorCode, ErrorDefinition, resolve_error_definition
from core.error_stack import ErrorStack, ModuleError, ResultStamp, stack_from_legacy


__all__ = (
    "DATA_SOURCES",
    "ERROR_CODES",
    "RETRY_POLICIES",
    "EvidenceEnvelope",
    "Capability",
    "CapabilityDecision",
    "CapabilityKey",
    "CapabilityRegistry",
    "CapabilityState",
    "ExecutionGate",
    "ExecutionIntent",
    "GateDecision",
    "MarketScope",
    "validate_scoped_payload",
    "error_payload",
    "ERROR_REGISTRY",
    "ErrorCode",
    "ErrorDefinition",
    "ErrorStack",
    "ModuleError",
    "ResultStamp",
    "resolve_error_definition",
    "stack_from_legacy",
    "normalize_data_source",
    "normalize_error_code",
    "source_metadata",
)


@dataclass(frozen=True)
class PoolManagerBalanceEvidence:
    token_address: str
    decimals: int
    balance_raw: str
    balance_decimal: str
    provider_address: str
    observed_at: str
    source: str
    evidence_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "decimals": self.decimals,
            "balance_raw": self.balance_raw,
            "balance_decimal": self.balance_decimal,
            "provider_address": self.provider_address,
            "observed_at": self.observed_at,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class CandidatePoolCoverage:
    candidate_pool_count: int
    min_candidate_pool_count: int
    scan_scope: dict[str, Any]
    generation_id: str
    yc_zc_same_set: bool
    shared_pools_ref: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_pool_count": self.candidate_pool_count,
            "min_candidate_pool_count": self.min_candidate_pool_count,
            "scan_scope": dict(self.scan_scope),
            "generation_id": self.generation_id,
            "yc_zc_same_set": self.yc_zc_same_set,
            "shared_pools_ref": list(self.shared_pools_ref),
        }


@dataclass(frozen=True)
class PriceEvidence:
    price_source: str
    price_observed_at: str
    price_freshness: str
    price_expires_at: str
    fallback_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_source": self.price_source,
            "price_observed_at": self.price_observed_at,
            "price_freshness": self.price_freshness,
            "price_expires_at": self.price_expires_at,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class FreshnessEvidence:
    observed_at: str
    freshness: str
    expires_at: str
    stale_at: str
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "freshness": self.freshness,
            "expires_at": self.expires_at,
            "stale_at": self.stale_at,
            "status": self.status,
        }


@dataclass(frozen=True)
class PoolManagerEvidence:
    balance: PoolManagerBalanceEvidence
    coverage: CandidatePoolCoverage
    price: PriceEvidence
    freshness: FreshnessEvidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "balance": self.balance.as_dict(),
            "coverage": self.coverage.as_dict(),
            "price": self.price.as_dict(),
            "freshness": self.freshness.as_dict(),
        }


def validate_pool_manager_evidence(payload: object) -> dict[str, Any]:
    """Validate the four evidence groups without filling missing fields."""
    if not isinstance(payload, dict):
        raise ValueError("pool manager evidence must be an object")
    required_groups = ("balance", "coverage", "price", "freshness")
    missing_groups = [group for group in required_groups if not isinstance(payload.get(group), dict)]
    if missing_groups:
        raise ValueError(f"pool manager evidence missing groups: {', '.join(missing_groups)}")

    required_fields = {
        "balance": ("token_address", "decimals", "balance_raw", "balance_decimal", "provider_address", "observed_at", "source", "evidence_ref"),
        "coverage": ("candidate_pool_count", "min_candidate_pool_count", "scan_scope", "generation_id", "yc_zc_same_set", "shared_pools_ref"),
        "price": ("price_source", "price_observed_at", "price_freshness", "price_expires_at"),
        "freshness": ("observed_at", "freshness", "expires_at", "stale_at", "status"),
    }
    missing: list[str] = []
    for group, fields in required_fields.items():
        section = payload[group]
        missing.extend(f"{group}.{field}" for field in fields if field not in section)
    if missing:
        raise ValueError(f"pool manager evidence missing fields: {', '.join(missing)}")
    if payload["freshness"]["status"] in {"insufficient_data", "not_configured", "not_ready", "stale", "invalid"}:
        raise ValueError(f"pool manager evidence is not fresh: {payload['freshness']['status']}")
    return dict(payload)


__all__ += (
    "CandidatePoolCoverage",
    "FreshnessEvidence",
    "PoolManagerBalanceEvidence",
    "PoolManagerEvidence",
    "PriceEvidence",
    "validate_pool_manager_evidence",
)
