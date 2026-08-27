from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _network(value: object) -> str:
    return _text(value).lower()


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_text(*values: object) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _first_network(*values: object) -> str:
    for value in values:
        network = _network(value)
        if network:
            return network
    return ""


def _first_int(*values: object) -> int | None:
    for value in values:
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


@dataclass(frozen=True)
class EvidenceScope:
    network: str = ""
    chain_id: int | None = None
    market_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "chain_id": self.chain_id,
            "market_id": self.market_id,
        }


def infer_evidence_scope(payload: dict[str, Any]) -> EvidenceScope:
    quote = _as_dict(payload.get("quote"))
    precheck = _as_dict(payload.get("precheck"))
    evidence = _as_dict(quote.get("live_onchain_evidence"))
    market_state = _as_dict(payload.get("market_state"))
    cow_filter = _as_dict(market_state.get("cow_filter"))
    trigger_context = _as_dict(evidence.get("triggerContext") or evidence.get("trigger_context"))

    return EvidenceScope(
        network=_first_network(
            payload.get("network"),
            evidence.get("network"),
            cow_filter.get("network"),
            trigger_context.get("network"),
        ),
        chain_id=_first_int(
            payload.get("chain_id"),
            payload.get("chainId"),
            evidence.get("chainId"),
            evidence.get("chain_id"),
            cow_filter.get("chain_id"),
            trigger_context.get("chainId"),
            trigger_context.get("chain_id"),
        ),
        market_id=_first_text(
            payload.get("market_id"),
            quote.get("market_id"),
            precheck.get("market_id"),
            market_state.get("market_id"),
            cow_filter.get("market_id"),
        ),
    )


def _wanted_networks(values: Iterable[object] | None) -> set[str]:
    return {_network(item) for item in values or [] if _network(item)}


def _wanted_chain_ids(values: Iterable[object] | None) -> set[int]:
    result: set[int] = set()
    for item in values or []:
        parsed = _int_or_none(item)
        if parsed is not None:
            result.add(parsed)
    return result


def _wanted_market_ids(values: Iterable[object] | None) -> set[str]:
    return {_text(item).lower() for item in values or [] if _text(item)}


def evidence_scope_matches(
    payload: dict[str, Any],
    *,
    networks: Iterable[object] | None = None,
    chain_ids: Iterable[object] | None = None,
    market_ids: Iterable[object] | None = None,
) -> bool:
    scope = infer_evidence_scope(payload)
    wanted_networks = _wanted_networks(networks)
    wanted_chain_ids = _wanted_chain_ids(chain_ids)
    wanted_market_ids = _wanted_market_ids(market_ids)

    if wanted_networks and scope.network not in wanted_networks:
        return False
    if wanted_chain_ids and scope.chain_id not in wanted_chain_ids:
        return False
    if wanted_market_ids and scope.market_id.lower() not in wanted_market_ids:
        return False
    return True


def evidence_scope_mismatch_reasons(
    payload: dict[str, Any],
    *,
    expected_network: object = None,
    expected_chain_id: object = None,
    expected_market_id: object = None,
) -> list[str]:
    scope = infer_evidence_scope(payload)
    reasons: list[str] = []
    network = _network(expected_network)
    chain_id = _int_or_none(expected_chain_id)
    market_id = _text(expected_market_id).lower()
    if network and scope.network != network:
        reasons.append("network_scope_mismatch")
    if chain_id is not None and scope.chain_id != chain_id:
        reasons.append("chain_id_scope_mismatch")
    if market_id and scope.market_id.lower() != market_id:
        reasons.append("market_id_scope_mismatch")
    return reasons


__all__ = [
    "EvidenceScope",
    "evidence_scope_matches",
    "evidence_scope_mismatch_reasons",
    "infer_evidence_scope",
]
