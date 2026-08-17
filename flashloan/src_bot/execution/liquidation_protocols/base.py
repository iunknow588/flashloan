from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProtocolCapability:
    protocol: str
    observation_supported: bool
    execution_supported: bool
    reason: str = ""


class LiquidationProtocolAdapter(Protocol):
    protocol: str

    def capability(self) -> ProtocolCapability:
        ...

    def discover_accounts(self, *args: Any, **kwargs: Any) -> list[str]:
        ...

    def build_user_report(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        ...
