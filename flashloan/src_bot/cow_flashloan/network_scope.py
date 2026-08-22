from __future__ import annotations

from collections.abc import Iterable

from cow_flashloan.routes import SUPPORTED_COW_NETWORKS, cow_network_config


DEFAULT_COW_NETWORK = "bnb"


class CowNetworkScopeError(ValueError):
    def __init__(self, value: str, message: str | None = None) -> None:
        self.value = str(value or "").strip()
        super().__init__(message or f"unsupported CoW network: {self.value or '<empty>'}")


def supported_cow_networks(*, include_testnet: bool = False) -> tuple[str, ...]:
    return tuple(
        name
        for name, config in SUPPORTED_COW_NETWORKS.items()
        if include_testnet or not config.testnet
    )


def normalize_cow_network(value: object, *, include_testnet: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise CowNetworkScopeError(raw)
    try:
        config = cow_network_config(network=raw)
    except ValueError as exc:
        raise CowNetworkScopeError(raw, str(exc)) from exc
    if config.testnet and not include_testnet:
        raise CowNetworkScopeError(raw, f"CoW testnet network is not enabled for this scope: {config.network}")
    return config.network


def normalize_cow_networks(
    values: Iterable[object],
    *,
    include_testnet: bool = False,
) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        network = normalize_cow_network(value, include_testnet=include_testnet)
        if network not in normalized:
            normalized.append(network)
    return tuple(normalized)


def parse_cow_networks(raw: object, *, include_testnet: bool = False) -> tuple[str, ...]:
    values = str(raw or "").split(",")
    return normalize_cow_networks(
        (value.strip() for value in values if value.strip()),
        include_testnet=include_testnet,
    )


def default_cow_network() -> str:
    return DEFAULT_COW_NETWORK
