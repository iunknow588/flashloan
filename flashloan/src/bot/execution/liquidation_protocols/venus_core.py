from __future__ import annotations

from typing import Any

from bot.execution.liquidation_protocols.base import ProtocolCapability
from bot.execution.venus_accounts import discover_venus_borrower_accounts
from bot.execution.venus_liquidation_scan import build_venus_user_report


class VenusCoreAdapter:
    protocol = "venus_core"

    def capability(self) -> ProtocolCapability:
        return ProtocolCapability(
            protocol=self.protocol,
            observation_supported=True,
            execution_supported=False,
            reason="Venus Core Pool is wired for read-only observation; transaction execution stays gated.",
        )

    def discover_accounts(self, *args: Any, **kwargs: Any) -> list[str]:
        discovery = discover_venus_borrower_accounts(*args, **kwargs)
        return list(discovery.get("accounts") or [])

    def build_user_report(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return build_venus_user_report(*args, **kwargs)
