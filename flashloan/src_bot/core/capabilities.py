"""Compatibility export for the split core capability registry."""

from src.core.capabilities import (
    Capability,
    CapabilityDecision,
    CapabilityKey,
    CapabilityRegistry,
    CapabilityState,
)

__all__ = ["Capability", "CapabilityDecision", "CapabilityKey", "CapabilityRegistry", "CapabilityState"]
