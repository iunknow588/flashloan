from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from importlib import import_module
import sys
from typing import Any
from urllib.parse import quote

from core.chain_data_registry import chain_data_by_chain_id
from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID as _ROBINHOOD_CHAIN_ID,
    ROBINHOOD_TESTNET_CHAIN_ID as _ROBINHOOD_TESTNET_CHAIN_ID,
)
from core.market_observation_store import (
    load_market_observation_overview_static,
    load_market_observation_static,
    observation_payload_from_static,
)
from bot.execution.uniswap_v4 import build_v4_quote_request
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from market.binance_market.dex_quote_surface import (
    build_quote_request_payload as build_dex_quote_request_payload,
    build_quote_routes_payload_from_overview,
    build_submission_review_payload as build_dex_submission_review_payload,
)
from market.binance_market.dex_surface_cache import (
    _DEX_SURFACE_CACHE,
    _DEX_SURFACE_CACHE_LOCK,
    _cache_surface_payload,
    _cached_framework_surface_payload,
    _cached_surface_payload,
    _protocols_key,
    _surface_cache_key,
    _surface_payload_has_framework_data,
)
from market.binance_market.dex_surface_analysis import (
    apply_route_overview_profit,
    build_overview_profit_payload,
    build_route_size_analysis,
)
from market.binance_market.dex_surface_quotes import (
    _best_edge_quote,
    _edge_quote,
    _route_quote_analysis,
    load_v4_sdk_adapter,
    quote_v4_exact_input_single,
)

from market.binance_market.dex_surface_observation import (
    _anchor_group_direction_rows,
    _anchor_group_direction_tokens,
    _anchor_observation_sequences,
    _best_pool_rows,
    _borrow_anchor_symbols,
    _borrowable_symbols,
    _candidate_anchor_symbols,
    _candidate_observation_groups,
    _candidate_pair_symbols_from_observation_route,
    _candidate_pairs,
    _chain_borrowable_symbols_from_pool_payload,
    _chain_fund_anchor_pair_observation,
    _chain_network_name,
    _chain_observation_function,
    _chain_observation_module,
    _closed_route_from_sequence,
    _graph_stable_anchor_symbols,
    _group_token_pool_rows,
    _observation_anchor_symbols,
    _observation_route_to_path_candidate,
    _overview_path_candidates,
    _path_candidates_from_market_observation,
    _reentry_route_has_reference_profit,
    _route_anchor_symbol,
    _route_candidate,
    _route_framework_context_from_observation,
    _route_step_row,
    _sequence_pair_row,
    _sequence_rows_by_counter,
    _token_from_pool_for_symbol,
    _token_instance_keys,
    _token_path_from_observation_route,
    _token_path_from_route_steps,
    _v4_index_bridge_pairs_for_anchor_groups,
    robinhood_testnet_v4_bridge_pairs_for_anchor_groups,
    robinhood_testnet_v4_fund_anchor_pair_observation,
)

from market.binance_market.dex_surface_graph import (
    _pair_score,
    _pair_token_entry,
    _pair_symbol_graph,
    _pair_tokens,
    _pools_for_pair,
    _pool_count,
    _pool_matches_edge,
    _price_map_from_pairs,
    _propagate_price_aliases,
    _route_addresses_from_tokens,
    _route_pools,
    _route_score,
    _route_symbols_from_tokens,
    _token_graph,
    _token_key,
    _unique_symbols,
)

from market.binance_market.dex_surface_v4 import (
    _fee_bps,
    _is_constant_product_pool,
    _live_v4_token_decimals,
    _market_reference_rate,
    _pool_reserve_amount,
    _pool_reserve_usd,
    _pool_manager_address,
    _pool_sqrt_price_x96,
    _pool_token_decimals_trusted,
    _pool_tvl_usd,
    _reference_price_quote,
    _static_depth_available,
    _v4_spot_rate,
    _v4_static_depth_impact_bps,
    _v4_token_decimals,
    _v4_token_metadata_cache,
)

from market.binance_market.dex_surface_types import (
    INVALID_ROUTE_SYMBOL_PREFIXES,
    PRICE_ALIAS_GROUPS,
    STABLE_ANCHOR_SYMBOLS,
    _human_from_raw,
    _int,
    _is_stable_anchor,
    _norm_symbol,
    _number,
    _optional_number,
    _pair_symbols,
    _pool_address,
    _pool_protocol,
    _pool_side_index,
    _pool_symbols,
    _pool_tokens,
    _raw_from_human,
    _route_symbol_allowed,
    _split_symbol_components,
    _token_address,
    _text,
)
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD
from market.binance_market.dex_surface_overview import (
    group_overview_paths_by_flash_loan_token,
    _overview_paths_from_static_summary,
    _overview_pool_payload_from_static_observation,
    _overview_profit_from_analysis,
    _route_execution_size_probe,
    _route_size_analysis,
    _route_with_overview_profit,
)
from market.binance_market.dex_surface_framework import (
    _env_flag,
    _robinhood_static_scope_protocols,
    _v4_execution_size_probe_enabled,
    _v4_live_quote_enabled,
    _v4_live_token_metadata_enabled,
    _v4_pool_token_metadata as _framework_v4_pool_token_metadata,
)

from web3 import Web3

QUOTE_PROBE_NOTIONAL_USD = 1000.0
DEFAULT_FLASH_LOAN_FEE_BPS = 5.0
DEFAULT_GAS_RESERVE_USD = 1.0
DEFAULT_AMM_FEE_BPS = 30.0
ROBINHOOD_CHAIN_ID = _ROBINHOOD_CHAIN_ID
ROBINHOOD_TESTNET_CHAIN_ID = _ROBINHOOD_TESTNET_CHAIN_ID
_ROBINHOOD_CHAIN_IDS = {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}
ERC20_DECIMALS_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def _v4_pool_token_metadata(pool: dict[str, Any], index: int, *, chain_id: int | None = None) -> dict[str, Any]:
    """Compatibility entrypoint that keeps old dex_surfaces monkeypatches working."""
    return _framework_v4_pool_token_metadata(
        pool,
        index,
        chain_id=chain_id,
        token_metadata_cache=_v4_token_metadata_cache,
        live_token_metadata_enabled=_v4_live_token_metadata_enabled,
        live_v4_token_decimals=_live_v4_token_decimals,
        pool_manager_address=_pool_manager_address,
    )


def _candidate_payload(chain_id: int | None, protocols: list[str] | None = None) -> dict[str, Any]:
    """Compatibility entrypoint that keeps old dex_surfaces monkeypatches working."""
    return dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols or [])


def dex_market_observation_surface_payload(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    cached = _cached_framework_surface_payload(
        "dex_market_observation",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if cached is not None:
        cached["marketObservationSourceStage"] = cached.get("marketObservationSourceStage") or cached.get("marketObservationSource") or "computed"
        return cached
    if use_static_observation:
        static_snapshot = load_market_observation_static(chain_id=chain_id, protocols=protocols)
        if static_snapshot.get("exists"):
            payload = observation_payload_from_static(static_snapshot, source="cached_snapshot")
            payload["marketObservationSourceStage"] = payload.get("marketObservationSourceStage") or payload.get("marketObservationSource") or "cached_snapshot"
            _cache_surface_payload(
                "dex_market_observation",
                payload,
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
            return payload
    payload = dex_market_observation_payload(chain_id=chain_id, protocols=protocols)
    payload["marketObservationSourceStage"] = payload.get("marketObservationSourceStage") or payload.get("marketObservationSource") or "computed"
    return payload




def dex_overview_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    cached = _cached_framework_surface_payload(
        "dex_overview",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if cached is not None:
        cached["marketObservationSourceStage"] = cached.get("marketObservationSourceStage") or cached.get("marketObservationSource") or "computed"
        return cached
    use_compact_static_overview = bool(
        use_static_observation and chain_id in _ROBINHOOD_CHAIN_IDS
    )
    scope_protocols = _robinhood_static_scope_protocols(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    compact_snapshot = (
        load_market_observation_overview_static(chain_id=chain_id, protocols=scope_protocols)
        if use_compact_static_overview
        else {}
    )
    if compact_snapshot.get("exists"):
        if compact_snapshot.get("closedLoopRouteSummaries"):
            overview_paths = _overview_paths_from_static_summary(compact_snapshot, chain_id=chain_id)
            overview_path_groups = group_overview_paths_by_flash_loan_token(
                overview_paths,
                borrowable_symbols=compact_snapshot.get("borrowableSymbols") or [],
            )
            pool_payload = _overview_pool_payload_from_static_observation(compact_snapshot)
            payload = {
                "mode": "dex_overview",
                "chain_id": chain_id,
                "selected_protocols": protocols or [],
                "summary": {
                    **(compact_snapshot.get("summary") or {}),
                    "arbitragePathCount": len(overview_paths),
                    "candidatePathCount": len(overview_paths),
                    "directAnchorPathCount": sum(1 for route in overview_paths if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
                    "bridgePathCount": sum(1 for route in overview_paths if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
                    "overviewPathGroupCount": len(overview_path_groups),
                    "watchPairCount": len(compact_snapshot.get("chainSpecificObservationPairs") or []),
                    "marketObservationRouteCount": len(overview_paths),
                    "overviewProbeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
                    "overviewProbeCompleteCount": 0,
                    "overviewLiveQuoterRequiredCount": len(overview_paths),
                    "overviewProfitableRouteCount": 0,
                    "overviewExecutableCandidateCount": 0,
                    "overviewChainSubmissionCandidateCount": 0,
                },
                "decisionBasis": "robinhood_testnet_market_observation_closed_loop" if chain_id == ROBINHOOD_TESTNET_CHAIN_ID else "market_observation_closed_loop",
                "marketObservationSource": "cached_snapshot",
                "marketObservationSourceStage": "cached_snapshot",
                "routeFrameworkSource": "market_observation",
                "marketObservation": {
                    "summary": compact_snapshot.get("summary") or {},
                    "chainSpecificObservationPairs": compact_snapshot.get("chainSpecificObservationPairs") or [],
                },
                "marketObservationSnapshotKind": "dex_overview_compact",
                "poolStaticTable": pool_payload.get("poolStaticTable") or {},
                "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
                "dexSettlement": pool_payload.get("dexSettlement") or {},
                "arbitragePaths": overview_paths,
                "arbitragePathGroups": overview_path_groups,
                "watchPairs": compact_snapshot.get("chainSpecificObservationPairs") or [],
            }
            _cache_surface_payload(
                "dex_overview",
                payload,
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
            return payload
        # The overview index owns route summaries, while the observation
        # snapshot owns the compact per-hop evidence used for the rough probe.
        # Read that scoped snapshot here instead of rebuilding candidate pools.
        observation_snapshot = load_market_observation_static(
            chain_id=chain_id,
            protocols=scope_protocols,
        )
        observation = observation_payload_from_static(
            observation_snapshot if observation_snapshot.get("exists") else compact_snapshot,
            source="cached_snapshot",
        )
        observation["marketObservationSnapshotKind"] = "dex_overview_compact"
        pool_payload = _overview_pool_payload_from_static_observation(observation)
    elif use_compact_static_overview:
        # A legacy full snapshot can exceed 100 MB because it retains every
        # observed V4 pool. Do not parse or rebuild it inside a page request.
        observation = {
            "mode": "dex_market_observation",
            "chain_id": chain_id,
            "selected_protocols": protocols or [],
            "summary": {},
            "marketObservationSource": "overview_snapshot_pending",
            "marketObservationSourceStage": "overview_snapshot_pending",
            "marketObservationSnapshotKind": "dex_overview_compact",
            "marketObservationStaticTable": {
                "exists": False,
                "network": _chain_network_name(chain_id),
                "chain_id": chain_id,
                "protocols": protocols or [],
                "source": "overview_snapshot_pending",
            },
        }
        pool_payload = _overview_pool_payload_from_static_observation(observation)
    else:
        observation = dex_market_observation_surface_payload(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
        )
        pool_payload = _candidate_payload(chain_id, protocols)
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    frame_context = _route_framework_context_from_observation(
        observation,
        pool_payload,
        chain_id=chain_id,
        protocols=protocols,
    )
    ready_paths = frame_context["overview_paths"]
    used_market_observation = bool(frame_context["used_market_observation"])
    watch_pairs = list(frame_context["watch_pairs"])
    anchor_symbols = list(frame_context["anchor_symbols"])
    closed_loop_anchor_symbols = list(frame_context["closed_loop_anchor_symbols"])
    observation_anchor_symbols = list(frame_context["observation_anchor_symbols"])
    ready_paths = [_route_with_overview_profit(route, price_map=price_map, chain_id=chain_id) for route in ready_paths]
    overview_path_groups = group_overview_paths_by_flash_loan_token(
        ready_paths,
        borrowable_symbols=anchor_symbols or observation_anchor_symbols or closed_loop_anchor_symbols,
    )
    overview_profits = [route.get("overviewProfit") or {} for route in ready_paths]
    observed_net_profits = [
        _number(profit.get("observedNetProfitUsd"))
        for profit in overview_profits
        if profit.get("observedNetProfitUsd") is not None
    ]
    executable_net_profits = [
        _number(profit.get("netProfitUsd"))
        for profit in overview_profits
        if profit.get("executableCandidate") and profit.get("netProfitUsd") is not None
    ]
    token_basis_returns = [
        _number(profit.get("referenceNetReturnBps"))
        for profit in overview_profits
        if profit.get("referenceNetReturnBps") is not None
    ]
    market_observation_source = observation.get("marketObservationSource") or "computed"
    market_observation_source_stage = observation.get("marketObservationSourceStage") or market_observation_source
    if market_observation_source in {"generated_live_snapshot", "refreshed_live_snapshot"}:
        market_observation_source = "computed"
    decision_basis = (
        "robinhood_testnet_market_observation_closed_loop"
        if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else "market_observation_closed_loop"
    )
    if not used_market_observation:
        decision_basis = (
            "symbol_anchor_to_non_anchor_candidate_path"
            if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
            else "borrow_anchor_to_non_anchor_candidate_path"
        )
    payload = {
        "mode": "dex_overview",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "summary": {
            **(pool_payload.get("summary") or {}),
            "anchorBorrowSymbols": anchor_symbols,
            "closedLoopAnchorSymbols": closed_loop_anchor_symbols,
            "closedLoopAnchorCount": len(closed_loop_anchor_symbols),
            "observationAnchorSymbols": observation_anchor_symbols,
            "observationAnchorCount": len(observation_anchor_symbols),
            "arbitragePathCount": len(ready_paths),
            "candidatePathCount": len(ready_paths),
            "directAnchorPathCount": sum(1 for route in ready_paths if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
            "bridgePathCount": sum(1 for route in ready_paths if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "overviewPathGroupCount": len(overview_path_groups),
            "watchPairCount": len(watch_pairs),
            "marketObservationRouteCount": len(observation.get("closedLoopRoutes") or []),
            "marketObservationSequenceCount": _int((observation.get("summary") or {}).get("observationSequenceCount")),
            "chainSpecificObservationPairCount": len(observation.get("chainSpecificObservationPairs") or []),
            "overviewProbeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
            "overviewProbeCompleteCount": sum(1 for profit in overview_profits if profit.get("complete")),
            "overviewLiveQuoterRequiredCount": sum(1 for profit in overview_profits if profit.get("requiresLiveQuoter")),
            "overviewSdkAdapterRequiredCount": sum(1 for profit in overview_profits if profit.get("adapterRequired")),
            "overviewProfitableRouteCount": sum(1 for profit in overview_profits if profit.get("profitable")),
            "overviewExecutableCandidateCount": sum(1 for profit in overview_profits if profit.get("executableCandidate")),
            "overviewChainSubmissionCandidateCount": sum(1 for profit in overview_profits if profit.get("chainSubmissionCandidate")),
            "overviewNonStableChainSubmissionCandidateCount": sum(
                1
                for profit in overview_profits
                if profit.get("chainSubmissionCandidate") and not profit.get("borrowAssetIsStable")
            ),
            "overviewBestObservedNetProfitUsd": max(observed_net_profits) if observed_net_profits else None,
            "overviewBestExecutableNetProfitUsd": max(executable_net_profits) if executable_net_profits else None,
            "overviewTokenBasisRouteCount": sum(1 for profit in overview_profits if profit.get("referenceReturnBasis") == "borrow_token"),
            "overviewTokenBasisProfitableRouteCount": sum(1 for profit in overview_profits if profit.get("tokenBasisProfitable")),
            "overviewBestTokenBasisNetReturnBps": max(token_basis_returns) if token_basis_returns else None,
        },
        "decisionBasis": decision_basis,
        "marketObservationSource": market_observation_source,
        "marketObservationSourceStage": market_observation_source_stage,
        "routeFrameworkSource": "market_observation" if used_market_observation else "candidate_pool_fallback",
        "marketObservation": {
            "summary": observation.get("summary") or {},
            "chainSpecificObservationPairs": observation.get("chainSpecificObservationPairs") or [],
            "chainSpecificObservationPools": observation.get("chainSpecificObservationPools") or [],
        },
        "marketObservationSnapshotKind": observation.get("marketObservationSnapshotKind"),
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "dexSettlement": pool_payload.get("dexSettlement") or {},
        "arbitragePaths": ready_paths,
        "arbitragePathGroups": overview_path_groups,
        "watchPairs": watch_pairs,
    }
    _cache_surface_payload(
        "dex_overview",
        payload,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    return payload


def dex_market_observation_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    from market.binance_market.dex_observation_surface import build_market_observation_payload

    return build_market_observation_payload(
        sys.modules[__name__],
        chain_id=chain_id,
        protocols=protocols,
    )


def dex_quote_routes_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    cached = _cached_framework_surface_payload(
        "dex_quote_routes",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if cached is not None:
        cached["marketObservationSourceStage"] = cached.get("marketObservationSourceStage") or cached.get("marketObservationSource") or "computed"
        return cached
    scope_protocols = _robinhood_static_scope_protocols(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    pool_payload = _candidate_payload(chain_id, scope_protocols)
    settlement = pool_payload.get("dexSettlement") or {}
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    if chain_id in _ROBINHOOD_CHAIN_IDS:
        payload = _quote_routes_payload_for_robinhood_family(
            chain_id=chain_id,
            protocols=scope_protocols,
            use_static_observation=use_static_observation,
            price_map=price_map,
        )
        payload["selected_protocols"] = protocols or scope_protocols
        _cache_surface_payload(
            "dex_quote_routes",
            payload,
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
        )
        return payload

    observation = dex_market_observation_surface_payload(
        chain_id=chain_id,
        protocols=scope_protocols,
        use_static_observation=use_static_observation,
    )
    frame_context = _route_framework_context_from_observation(
        observation,
        pool_payload,
        chain_id=chain_id,
        protocols=scope_protocols,
    )
    overview = _quote_routes_overview_payload(
        observation=observation,
        pool_payload=pool_payload,
        frame_context=frame_context,
        settlement=settlement,
    )
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=scope_protocols,
        route_quote_analysis_fn=_route_quote_analysis,
        route_size_analysis_fn=_route_size_analysis,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
        price_map=price_map,
    )
    payload["marketObservationSource"] = observation.get("marketObservationSource") or "computed"
    payload["marketObservationSourceStage"] = (
        observation.get("marketObservationSourceStage")
        or payload["marketObservationSource"]
    )
    _cache_surface_payload(
        "dex_quote_routes",
        payload,
        chain_id=chain_id,
        protocols=scope_protocols,
        use_static_observation=use_static_observation,
    )
    return payload


def _quote_routes_payload_from_overview(
    overview: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    price_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    return build_quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=protocols,
        route_quote_analysis_fn=_route_quote_analysis,
        route_size_analysis_fn=_route_size_analysis,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
        price_map=price_map,
    )


def _quote_routes_payload_for_robinhood_family(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool,
    price_map: dict[str, float],
) -> dict[str, Any]:
    observation = _cached_framework_surface_payload(
        "dex_market_observation",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if observation is None and use_static_observation:
        observation = dex_market_observation_surface_payload(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        )
    if observation is not None:
        pool_payload = _candidate_payload(chain_id, protocols)
        frame_context = _route_framework_context_from_observation(
            observation,
            pool_payload,
            chain_id=chain_id,
            protocols=protocols,
        )
        overview = _quote_routes_overview_payload(
            observation=observation,
            pool_payload=pool_payload,
            frame_context=frame_context,
            settlement=pool_payload.get("dexSettlement") or {},
        )
    else:
        overview = _cached_surface_payload(
            "dex_overview",
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
        )
        if overview is None:
            overview = dex_overview_payload(
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
            _cache_surface_payload(
                "dex_overview",
                overview,
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
    payload = _quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=protocols,
        price_map=price_map,
    )
    source_observation = observation if observation is not None else overview
    payload["marketObservationSource"] = source_observation.get("marketObservationSource") or "computed"
    payload["marketObservationSourceStage"] = (
        source_observation.get("marketObservationSourceStage")
        or payload["marketObservationSource"]
    )
    observed_anchor_symbols = list(observation.get("observationAnchorSymbols") or []) if observation is not None else []
    if observed_anchor_symbols:
        payload_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        payload_summary["observationAnchorSymbols"] = observed_anchor_symbols
        payload_summary.setdefault("anchorBorrowSymbols", observed_anchor_symbols)
        payload_summary.setdefault("closedLoopAnchorSymbols", observed_anchor_symbols)
        payload["summary"] = payload_summary
    _cache_surface_payload(
        "dex_quote_routes",
        payload,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    return payload


def _quote_routes_overview_payload(
    *,
    observation: dict[str, Any],
    pool_payload: dict[str, Any],
    frame_context: dict[str, Any],
    settlement: dict[str, Any],
) -> dict[str, Any]:
    observation_summary = observation.get("summary") if isinstance(observation.get("summary"), dict) else {}
    closed_loop_route_count = len(observation.get("closedLoopRoutes") or []) or int(
        observation_summary.get("closedLoopRouteCount") or 0
    )
    return {
        "arbitragePaths": frame_context["overview_paths"],
        "summary": {
            "probeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
            "anchorBorrowSymbols": frame_context["anchor_symbols"],
            "closedLoopAnchorSymbols": frame_context["closed_loop_anchor_symbols"],
            "closedLoopAnchorCount": len(frame_context["closed_loop_anchor_symbols"]),
            "observationAnchorSymbols": frame_context["observation_anchor_symbols"],
            "observationAnchorCount": len(frame_context["observation_anchor_symbols"]),
            "pathSource": frame_context["path_source"],
            "marketObservationRouteCount": closed_loop_route_count,
            "marketObservationSequenceCount": int(observation_summary.get("observationSequenceCount") or 0),
            "chainSpecificObservationPairCount": len(observation.get("chainSpecificObservationPairs") or []),
        },
        "marketObservation": {
            "summary": observation.get("summary") or {},
            "chainSpecificObservationPairs": observation.get("chainSpecificObservationPairs") or [],
        },
        "routeFrameworkSource": frame_context["path_source"],
        "marketObservationSource": observation.get("marketObservationSource") or "computed",
        "marketObservationSourceStage": observation.get("marketObservationSourceStage")
        or observation.get("marketObservationSource")
        or "computed",
        "dexSettlement": settlement,
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
    }


def dex_quote_request_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    pair: str | None = None,
    venue: str | None = None,
    amount: str | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    routes = dex_quote_routes_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    return build_dex_quote_request_payload(
        quote_routes_payload=routes,
        pair=pair,
        venue=venue,
        amount=amount,
    )


def dex_submission_review_payload(
    *,
    attempts: list[dict[str, Any]] | None = None,
    groups: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    return build_dex_submission_review_payload(
        attempts=attempts,
        groups=groups,
        source=source,
    )


__all__ = [
    "dex_market_observation_payload",
    "dex_overview_payload",
    "dex_quote_request_payload",
    "dex_quote_routes_payload",
    "dex_submission_review_payload",
    "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
    "robinhood_testnet_v4_fund_anchor_pair_observation",
]
