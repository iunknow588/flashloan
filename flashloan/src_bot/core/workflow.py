from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

from core.contracts import ExecutionGate
from cow_flashloan.network_scope import normalize_cow_networks
from core.market_config import (
    DEFAULT_AVALANCHE_DEX_MARKET_ID,
    DEFAULT_COW_DEX_MARKET_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
)
from core.workflow_market_scope import validate_robinhood_market_network
from core.workflow_pages import PageScopeContract, page_scope_contract

WORKFLOW_LIQUIDATION = "liquidation"
WORKFLOW_DEX_ARBITRAGE = "dex_arbitrage"
WORKFLOW_CHAIN_NEUTRAL = "chain_neutral"

EXECUTION_MODE_DIRECT = "direct"
EXECUTION_MODE_COW = "cow"

SCOPE_SINGLE_CHAIN = "single_chain"
SCOPE_MULTI_CHAIN = "multi_chain"
SCOPE_CHAIN_NEUTRAL = "chain_neutral"
HARD_WORKFLOW_BLOCKS = frozenset(
    {
        "cow_liquidation_adapter_not_implemented",
        "direct_dex_route_not_primary",
        "robinhood_shadow_observation_only",
        "chain_neutral_execution_blocked",
        "atomic_cross_chain_settlement_not_implemented",
    }
)


class WorkflowContextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)



@dataclass(frozen=True)
class WorkflowContext:
    page: PageScopeContract
    execution_mode: str | None
    scope_type: str
    selected_networks: tuple[str, ...]
    primary_network: str | None
    execution_capable: bool
    settlement_model: str
    atomic_cross_chain: bool
    blocked_reasons: tuple[str, ...] = ()

    @property
    def workflow(self) -> str:
        return self.page.workflow

    def as_dict(self) -> dict[str, object]:
        gate = ExecutionGate(
            ready=self.execution_capable and not self.blocked_reasons,
            execution_enabled=self.execution_capable and not self.blocked_reasons,
            broadcast_enabled=False,
            blocked_reasons=self.blocked_reasons,
        )
        return {
            "workflow": self.workflow,
            "page": self.page.as_dict(),
            "execution_mode": self.execution_mode,
            "scope_type": self.scope_type,
            "selected_networks": list(self.selected_networks),
            "primary_network": self.primary_network,
            "execution_capable": self.execution_capable,
            "settlement_model": self.settlement_model,
            "atomic_cross_chain": self.atomic_cross_chain,
            "blocked_reasons": list(self.blocked_reasons),
            "execution_gate": gate.as_dict(),
        }


def _query_values(query: Mapping[str, object] | str | None, name: str) -> list[str]:
    if isinstance(query, str):
        values = parse_qs(urlsplit(query).query).get(name, [])
    elif query is None:
        values = []
    else:
        raw = query.get(name, "")
        values = raw if isinstance(raw, (list, tuple)) else [raw]
    result: list[str] = []
    for value in values:
        for item in str(value or "").split(","):
            normalized = item.strip().lower()
            if normalized and normalized not in result:
                result.append(normalized)
    return result

def resolve_workflow_context(
    path: str,
    query: Mapping[str, object] | str | None = None,
) -> WorkflowContext | None:
    page = page_scope_contract(path)
    if page is None:
        return None

    requested_workflow = (_query_values(query, "workflow") or [page.workflow])[0]
    if requested_workflow != page.workflow:
        raise WorkflowContextError(
            "workflow_mismatch",
            f"path {urlsplit(str(path or '')).path or '/'} belongs to workflow {page.workflow}, not {requested_workflow}",
        )

    if page.workflow == WORKFLOW_CHAIN_NEUTRAL:
        return WorkflowContext(
            page=page,
            execution_mode=None,
            scope_type=SCOPE_CHAIN_NEUTRAL,
            selected_networks=(),
            primary_network=None,
            execution_capable=False,
            settlement_model="analytics_only",
            atomic_cross_chain=False,
            blocked_reasons=("chain_neutral_execution_blocked",),
        )

    raw_selected_networks = tuple(_query_values(query, "cow_networks") or _query_values(query, "cow_network"))
    pathname = urlsplit(str(path or "")).path or "/"
    selected_market_id = (_query_values(query, "market_id") or [""])[0]
    if page.page_key == "robinhood_dex" and not selected_market_id:
        selected_market_id = DEFAULT_ROBINHOOD_DEX_MARKET_ID
    if pathname.startswith("/api/binance-market/cow") and not selected_market_id:
        selected_market_id = DEFAULT_COW_DEX_MARKET_ID
    if page.workflow == WORKFLOW_DEX_ARBITRAGE and not selected_market_id:
        selected_market_id = DEFAULT_AVALANCHE_DEX_MARKET_ID
    is_cow_execution_rail = (
        page.workflow == WORKFLOW_DEX_ARBITRAGE
        and (not selected_market_id or selected_market_id == DEFAULT_COW_DEX_MARKET_ID)
    )
    mode_values = _query_values(query, "execution_mode")
    default_mode = page.default_execution_mode or ""
    if page.workflow == WORKFLOW_DEX_ARBITRAGE and selected_market_id and not is_cow_execution_rail:
        default_mode = EXECUTION_MODE_DIRECT
    requested_mode = (mode_values or [default_mode])[0]
    if requested_mode not in page.allowed_execution_modes:
        raise WorkflowContextError(
            "unsupported_execution_mode",
            f"{requested_mode or 'empty'} is not supported by workflow {page.workflow}",
        )

    if page.workflow == WORKFLOW_DEX_ARBITRAGE:
        if selected_market_id == DEFAULT_ROBINHOOD_DEX_MARKET_ID:
            validate_robinhood_market_network(selected_market_id, raw_selected_networks)
        if raw_selected_networks and not is_cow_execution_rail:
            raise WorkflowContextError(
                "execution_scope_mismatch",
                "cow_networks is only valid for the COW execution rail",
            )
        try:
            selected_networks = normalize_cow_networks(raw_selected_networks) if is_cow_execution_rail else ()
        except ValueError as exc:
            raise WorkflowContextError("unsupported_cow_network", str(exc)) from exc
    else:
        selected_networks = raw_selected_networks
    scope_values = _query_values(query, "scope")
    requested_scope = scope_values[0] if scope_values else ""
    if requested_scope and requested_scope not in {SCOPE_SINGLE_CHAIN, SCOPE_MULTI_CHAIN}:
        raise WorkflowContextError("invalid_scope", f"invalid scope: {requested_scope}")
    if requested_scope == SCOPE_MULTI_CHAIN and requested_mode != EXECUTION_MODE_COW:
        raise WorkflowContextError(
            "multi_chain_requires_cow",
            "multi-chain execution requires execution_mode=cow",
        )
    if (
        page.workflow == WORKFLOW_DEX_ARBITRAGE
        and requested_scope == SCOPE_MULTI_CHAIN
        and selected_market_id
        and selected_market_id != DEFAULT_COW_DEX_MARKET_ID
    ):
        raise WorkflowContextError(
            "execution_scope_mismatch",
            "multi-chain scope is only valid for the COW execution rail",
        )

    if requested_scope:
        scope_type = requested_scope
    elif requested_mode == EXECUTION_MODE_COW and len(selected_networks) > 1:
        scope_type = SCOPE_MULTI_CHAIN
    else:
        scope_type = SCOPE_SINGLE_CHAIN

    blocked_reasons: list[str] = []
    execution_capable = True
    if page.workflow == WORKFLOW_LIQUIDATION and requested_mode == EXECUTION_MODE_COW:
        execution_capable = False
        blocked_reasons.append("cow_liquidation_adapter_not_implemented")
    if page.page_key == "dex_arbitrage" and requested_mode == EXECUTION_MODE_DIRECT:
        execution_capable = False
        blocked_reasons.append("direct_dex_route_not_primary")
    if page.page_key == "robinhood_dex":
        execution_capable = False
        blocked_reasons.append("robinhood_shadow_observation_only")
    if page.page_key == "robinhood_dex" and scope_type == SCOPE_MULTI_CHAIN:
        raise WorkflowContextError(
            "robinhood_single_chain_only",
            "Robinhood DEX shadow observation is restricted to one chain",
        )
    if page.workflow == WORKFLOW_DEX_ARBITRAGE and selected_market_id == "robinhood-dex-shadow":
        if scope_type == SCOPE_MULTI_CHAIN:
            raise WorkflowContextError(
                "robinhood_single_chain_only",
                "Robinhood DEX shadow observation is restricted to one chain",
            )
        execution_capable = False
        if "robinhood_shadow_observation_only" not in blocked_reasons:
            blocked_reasons.append("robinhood_shadow_observation_only")

    settlement_model = (
        "cow_solver_settlement"
        if requested_mode == EXECUTION_MODE_COW and scope_type == SCOPE_SINGLE_CHAIN
        else "independent_per_chain"
        if requested_mode == EXECUTION_MODE_COW
        else "direct_on_chain"
    )
    atomic_cross_chain = False
    if requested_mode == EXECUTION_MODE_COW and scope_type == SCOPE_MULTI_CHAIN:
        blocked_reasons.append("atomic_cross_chain_settlement_not_implemented")

    return WorkflowContext(
        page=page,
        execution_mode=requested_mode,
        scope_type=scope_type,
        selected_networks=selected_networks,
        primary_network=selected_networks[0] if selected_networks else None,
        execution_capable=execution_capable,
        settlement_model=settlement_model,
        atomic_cross_chain=atomic_cross_chain,
        blocked_reasons=tuple(blocked_reasons),
    )

def workflow_context_payload(
    path: str,
    query: Mapping[str, object] | str | None = None,
) -> dict[str, object]:
    context = resolve_workflow_context(path, query)
    return {
        "ok": True,
        "context": context.as_dict() if context else None,
    }

