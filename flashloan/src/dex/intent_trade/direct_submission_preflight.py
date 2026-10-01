"""Input and route preflight for unified direct submission."""

from __future__ import annotations

from typing import Any


def prepare_submission_inputs(
    deps: Any,
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
) -> dict[str, Any]:
    intent = quote_payload.get("cow_flashloan_intent") if isinstance(quote_payload, dict) else None
    protocol = intent.get("direct_onchain_protocol") if isinstance(intent, dict) else None
    if isinstance(protocol, dict) and not protocol.get("enabled", True):
        blocked = _disabled_protocol_result(deps, protocol)
        if blocked is not None:
            return {"blocked": blocked}
    if not isinstance(protocol, dict) or not protocol.get("enabled", True):
        return {"blocked": _missing_protocol_result()}

    network_result = _resolve_network(deps, protocol)
    if network_result.get("blocked"):
        return network_result
    network = network_result["network"]
    expected_chain_id = network_result["expected_chain_id"]

    if not deps.unified_executor_supported_for_network(network):
        return {
            "blocked": _executor_adapter_unverified_result(
                deps,
                network=network,
                chain_id=expected_chain_id,
            )
        }

    executor_address = _executor_address(deps, protocol, network=network)
    if not executor_address:
        return {"blocked": _missing_executor_result()}

    opportunity_context = opportunity if isinstance(opportunity, dict) else None
    usdc_address = deps.usdc_address_for_network(network)
    broadcast_enabled = deps.env_bool(
        "TRIANGULAR_DIRECT_BROADCAST_ENABLED",
        "TRIANGULAR_AB_BROADCAST_ENABLED",
        "UNIFIED_EXECUTOR_BROADCAST_ENABLED",
    )
    explicit_route_groups = _has_explicit_route_groups(deps, protocol, opportunity_context)
    route_groups = deps.runtime_route_group_candidates(protocol, opportunity_context)
    prepared_groups, rejected_groups = deps.prepare_unified_route_groups(
        route_groups,
        usdc_address=usdc_address,
        allow_single_pair_diagnostic=deps.allow_single_pair_diagnostic(protocol, opportunity_context),
        allow_stable_targets=deps.allow_stable_exercise_targets(protocol, opportunity_context),
        require_token_risk_metadata=broadcast_enabled,
    )
    if not prepared_groups:
        return {
            "blocked": _empty_route_group_result(
                route_group_count=len(route_groups),
                rejected_groups=rejected_groups,
                error="runtime_trades_empty" if not route_groups else "no_valid_unified_route_group",
            )
        }

    rpc_url, rpc_env_names = deps.direct_rpc_url(network)
    if not rpc_url:
        return {"blocked": _missing_rpc_result(rpc_env_names)}

    return {
        "blocked": None,
        "protocol": protocol,
        "network": network,
        "expected_chain_id": expected_chain_id,
        "executor_address": executor_address,
        "opportunity_context": opportunity_context,
        "usdc_address": usdc_address,
        "broadcast_enabled": broadcast_enabled,
        "explicit_route_groups": explicit_route_groups,
        "prepared_groups": prepared_groups,
        "rejected_groups": rejected_groups,
        "rpc_url": rpc_url,
    }


def _disabled_protocol_result(deps: Any, protocol: dict[str, Any]) -> dict[str, Any] | None:
    network = deps.normalize_direct_onchain_network(protocol.get("network") or "")
    if network not in deps.direct_onchain_networks:
        return None
    status = deps.executor_adapter_unverified_status(network)
    return {
        "ok": False,
        "submitted": False,
        "status": status,
        "blocked_reason": status,
        "error": deps.executor_adapter_unverified_error(network),
        "network": network,
        "chain_id": deps.direct_onchain_networks[network]["chain_id"],
    }


def _missing_protocol_result() -> dict[str, Any]:
    return {
        "ok": False,
        "submitted": False,
        "status": "direct_protocol_missing",
        "blocked_reason": "direct_protocol_missing",
        "error": "direct_onchain_protocol is required",
    }


def _resolve_network(deps: Any, protocol: dict[str, Any]) -> dict[str, Any]:
    try:
        network, expected_chain_id, _expected_testnet = deps.resolve_direct_onchain_network(protocol)
    except ValueError as exc:
        return {
            "blocked": {
                "ok": False,
                "submitted": False,
                "status": "order_submission_network_unsupported",
                "blocked_reason": "order_submission_network_unsupported",
                "error": str(exc),
            }
        }
    return {"blocked": None, "network": network, "expected_chain_id": expected_chain_id}


def _executor_adapter_unverified_result(deps: Any, *, network: str, chain_id: int) -> dict[str, Any]:
    status = deps.executor_adapter_unverified_status(network)
    return {
        "ok": False,
        "submitted": False,
        "status": status,
        "blocked_reason": status,
        "error": deps.executor_adapter_unverified_error(network),
        "network": network,
        "chain_id": chain_id,
    }


def _executor_address(deps: Any, protocol: dict[str, Any], *, network: str) -> str:
    chain_lookup = getattr(deps, "unified_executor_address_for_network", None)
    chain_value = str(chain_lookup(network)).strip() if callable(chain_lookup) else ""
    return str(
        protocol.get("unified_executor_address")
        or protocol.get("executor_address")
        or chain_value
        or deps.env("UNIFIED_EXECUTOR_ADDRESS", "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS")
    ).strip()


def _missing_executor_result() -> dict[str, Any]:
    return {
        "ok": False,
        "submitted": False,
        "status": "direct_protocol_incomplete",
        "blocked_reason": "direct_protocol_incomplete",
        "error": "unified executor address is required",
    }


def _has_explicit_route_groups(deps: Any, protocol: dict[str, Any], opportunity_context: dict[str, Any] | None) -> bool:
    payload_has_groups = any(
        isinstance(payload, dict)
        and any(isinstance(payload.get(key), list) for key in ("runtime_route_groups", "runtime_trades", "candidate_trades", "trades"))
        for payload in (protocol, opportunity_context)
    )
    return payload_has_groups or bool(deps.env("TRIANGULAR_RUNTIME_TRADES_JSON"))


def _empty_route_group_result(*, route_group_count: int, rejected_groups: list[dict[str, Any]], error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "submitted": False,
        "status": "direct_protocol_incomplete",
        "blocked_reason": "direct_protocol_incomplete",
        "error": error,
        "routeGroupCount": route_group_count,
        "rejectedRouteGroups": rejected_groups,
    }


def _missing_rpc_result(rpc_env_names: tuple[str, ...]) -> dict[str, Any]:
    return {
        "ok": False,
        "submitted": False,
        "status": "network_config_missing",
        "blocked_reason": "network_config_missing",
        "error": f"{' or '.join(rpc_env_names)} is required",
    }
