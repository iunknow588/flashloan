from __future__ import annotations

from typing import Any

from execution.liquidation_protocols.base import ProtocolCapability
from execution.liquidation_scan import build_user_liquidation_report, discover_borrower_addresses


class AaveV3Adapter:
    protocol = "aave_v3"

    def capability(self) -> ProtocolCapability:
        return ProtocolCapability(
            protocol=self.protocol,
            observation_supported=True,
            execution_supported=True,
            reason="Aave V3 path is the existing supported liquidation flow.",
        )

    def discover_accounts(self, *args: Any, **kwargs: Any) -> list[str]:
        return discover_borrower_addresses(*args, **kwargs)

    def build_user_report(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return build_user_liquidation_report(*args, **kwargs)
