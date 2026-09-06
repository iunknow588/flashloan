"""Core-only validation for workflow CoW network scope.

The workflow layer must not import DEX route implementations merely to
normalize query-string scope. DEX adapters can apply richer route checks
after this boundary has accepted the network names.
"""

from __future__ import annotations

from collections.abc import Iterable


SUPPORTED_COW_NETWORK_NAMES = frozenset(
    {
        "ethereum",
        "gnosis",
        "arbitrum_one",
        "base",
        "polygon",
        "avalanche",
        "bnb",
    }
)


class CowNetworkScopeError(ValueError):
    def __init__(self, value: str, message: str | None = None) -> None:
        self.value = str(value or "").strip()
        super().__init__(message or f"unsupported CoW network: {self.value or '<empty>'}")


def normalize_cow_networks(values: Iterable[object]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        network = str(value or "").strip()
        if not network or network not in SUPPORTED_COW_NETWORK_NAMES:
            raise CowNetworkScopeError(network)
        if network not in normalized:
            normalized.append(network)
    return tuple(normalized)
