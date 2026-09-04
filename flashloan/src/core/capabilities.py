"""Fail-closed capability registry for chain and workflow combinations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class CapabilityState(StrEnum):
    DISABLED = "disabled"
    READ_ONLY = "read_only"
    QUOTE_READY = "quote_ready"
    EXECUTION_READY = "execution_ready"


@dataclass(frozen=True)
class CapabilityKey:
    chain_id: int
    network: str
    protocol: str
    flow: str

    def normalized(self) -> "CapabilityKey":
        return CapabilityKey(
            int(self.chain_id),
            self.network.strip().lower(),
            self.protocol.strip().lower(),
            self.flow.strip().lower(),
        )


@dataclass(frozen=True)
class Capability:
    key: CapabilityKey
    state: CapabilityState
    reason: str = ""


@dataclass(frozen=True)
class CapabilityDecision:
    status: str
    state: CapabilityState | None
    reason: str
    supported: bool

    @property
    def allowed(self) -> bool:
        return self.status == "ok"


class CapabilityRegistry:
    """Explicit registry; absent entries are never treated as supported."""

    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._items: dict[CapabilityKey, Capability] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: Capability) -> None:
        key = capability.key.normalized()
        if not key.network or not key.protocol or not key.flow:
            raise ValueError("capability key requires network, protocol, and flow")
        self._items[key] = Capability(key, CapabilityState(capability.state), capability.reason)

    def resolve(self, key: CapabilityKey) -> CapabilityDecision:
        capability = self._items.get(key.normalized())
        if capability is None:
            return CapabilityDecision("unsupported", None, "capability_not_registered", False)
        if capability.state == CapabilityState.DISABLED:
            return CapabilityDecision("not_ready", capability.state, capability.reason or "capability_disabled", True)
        if capability.state != CapabilityState.EXECUTION_READY:
            return CapabilityDecision("not_ready", capability.state, capability.reason or "capability_not_execution_ready", True)
        return CapabilityDecision("ok", capability.state, capability.reason, True)

    def snapshot(self) -> tuple[Capability, ...]:
        return tuple(self._items[key] for key in sorted(self._items, key=lambda item: item.__dict__.values()))


__all__ = ["Capability", "CapabilityDecision", "CapabilityKey", "CapabilityRegistry", "CapabilityState"]
