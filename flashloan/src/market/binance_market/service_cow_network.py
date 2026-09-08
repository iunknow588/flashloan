"""CoW network option and route-support helpers for binance market service."""

from __future__ import annotations

from typing import Any


def cow_network_options() -> dict[str, Any]:
    networks = []
    for name in supported_cow_networks(include_testnet=True):
        config = SUPPORTED_COW_NETWORKS[name]
        if not config.network:
            continue
        account = cow_account_config(config.network)
        networks.append(
            {
                "network": name,
                "label": COW_NETWORK_LABELS.get(name, name),
                "chain_id": config.chain_id,
                "testnet": config.testnet,
                "submission_supported": cow_order_submission_network_supported(config.network),
                "quote_api": config.quote_api,
                "owner": account.owner,
                "owner_source": account.owner_source,
            }
        )
    return {
        "default_network": DEFAULT_COW_TEST_NETWORK,
        "networks": networks,
    }


def _cow_route_support(spec: dict[str, Any], registry: dict[str, Any], *, cow_network: str) -> dict[str, Any]:
    path = [str(item or "").upper() for item in spec.get("path") or []]
    unsupported = []
    tokens = []
    for symbol in path:
        try:
            token = resolve_token(symbol, registry)
            tokens.append(
                {
                    "symbol": token.symbol,
                    "address": token.address,
                    "decimals": token.decimals,
                    "source": token.source,
                }
            )
        except Exception:
            unsupported.append(symbol)
    unsupported = list(dict.fromkeys(unsupported))
    supported = not unsupported and bool(path)
    return {
        "pair": spec.get("pair"),
        "pair_rank": spec.get("pair_rank"),
        "priority_reason": spec.get("priority_reason"),
        "path": path,
        "cow_network": cow_network,
        "supported": supported,
        "status": "supported" if supported else "unsupported_tokens",
        "unsupported_tokens": unsupported,
        "tokens": tokens,
        "error": None if supported else f"unsupported CoW tokens on {cow_network}: {', '.join(unsupported)}",
    }


def _bind_service_globals() -> None:
    from market.binance_market import service as service_module

    globals().update(
        {
            "COW_NETWORK_LABELS": service_module.COW_NETWORK_LABELS,
            "DEFAULT_COW_TEST_NETWORK": service_module.DEFAULT_COW_TEST_NETWORK,
            "SUPPORTED_COW_NETWORKS": service_module.SUPPORTED_COW_NETWORKS,
            "cow_account_config": service_module.cow_account_config,
            "cow_order_submission_network_supported": service_module.cow_order_submission_network_supported,
            "resolve_token": service_module.resolve_token,
            "supported_cow_networks": service_module.supported_cow_networks,
        }
    )


_bind_service_globals()


__all__ = ["_cow_route_support", "cow_network_options"]
