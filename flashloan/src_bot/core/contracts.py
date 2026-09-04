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
