from __future__ import annotations

from execution.liquidation_protocols.aave_v3 import AaveV3Adapter
from execution.liquidation_protocols.base import LiquidationProtocolAdapter, ProtocolCapability
from execution.liquidation_protocols.venus_core import VenusCoreAdapter


ADAPTERS = {
    "aave_v3": AaveV3Adapter,
    "venus_core": VenusCoreAdapter,
}


def liquidation_protocol_adapter(protocol: str) -> LiquidationProtocolAdapter:
    key = str(protocol or "").strip().lower()
    adapter_class = ADAPTERS.get(key)
    if adapter_class is None:
        raise ValueError(f"unsupported liquidation protocol adapter: {protocol}")
    return adapter_class()


__all__ = [
    "AaveV3Adapter",
    "ADAPTERS",
    "LiquidationProtocolAdapter",
    "ProtocolCapability",
    "VenusCoreAdapter",
    "liquidation_protocol_adapter",
]
