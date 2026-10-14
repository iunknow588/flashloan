from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from importlib import import_module
import json
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
    _anchor_observation_sequences,
    _anchor_group_direction_rows,
    _anchor_group_direction_tokens,
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
    _candidate_pool_overview_profit,
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


def _query_count(
    database_url: str,
    table: str,
    conditions: str,
    params: tuple[Any, ...] = (),
) -> int:
    """Return a row count without materialising every row into Python."""
    from core.db.storage_business_io import query

    allowed_tables = {
        "market_observation_yz_pairs",
        "market_observation_abnormal_yz_pairs",
        "dex_candidate_anchor_tokens",
    }
    if table not in allowed_tables:
        raise ValueError(f"unsupported count table: {table}")
    rows = query(
        database_url,
        f"SELECT COUNT(*) FROM {table} WHERE {conditions}",
        params,
    )
    return int(rows[0]["COUNT(*)"] or 0) if rows else 0


def _json_loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


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


def _dex_table_route(route: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    """Tag a route with the DEX_TABLE provenance contract used by quote pages."""
    return {
        **route,
        "tableSource": "dex_table",
        "referencedTable": "ob_table",
        "poolIndexVersion": observation.get("poolIndexVersion") or {},
        "pathSource": "market_observation",
    }


def _database_first_v4_robinhood(*, chain_id: int | None, protocols: list[str] | None) -> bool:
    """Return whether the Robinhood V4 surface must read SQLite only."""
    return bool(
        int(chain_id or 0) in _ROBINHOOD_CHAIN_IDS
        and (
            not protocols
            or "uniswap_v4" in {str(protocol or "").strip().lower() for protocol in protocols}
        )
    )


_database_first_v4_mainnet = _database_first_v4_robinhood


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
    database_first = _database_first_v4_robinhood(chain_id=chain_id, protocols=protocols)
    if database_first:
        payload = _database_first_robinhood_observation_payload(
            chain_id=chain_id,
            protocols=protocols,
        )
        payload["marketObservationSourceStage"] = (
            payload.get("marketObservationSourceStage")
            or payload.get("marketObservationSource")
            or "computed"
        )
        _cache_surface_payload(
            "dex_market_observation",
            payload,
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
            ttl_seconds=0,
        )
        return payload
    if use_static_observation and not database_first:
        static_snapshot = load_market_observation_static(chain_id=chain_id, protocols=protocols)
        if static_snapshot.get("exists"):
            payload = observation_payload_from_static(static_snapshot, source="cached_snapshot")
            # A previously generated empty snapshot must not mask the live
            # YC/ZC observation builder.  This is especially important for a
            # newly scoped protocol snapshot: the legacy file can exist while
            # containing no anchors, groups, or concrete sequences at all.
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            group_sequences = [
                sequence
                for group in (payload.get("candidateObservationGroups") or [])
                if isinstance(group, dict)
                for sequence in (group.get("observationSequences") or [])
            ]
            # A snapshot that only remembers anchors/candidate groups but has
            # no concrete sequences is a legacy empty handoff.  It must not
            # shadow the live observation builder, which now composes real
            # Y-Z bridge pools into observationSequences.
            is_empty_snapshot = not any(
                (
                    payload.get("observationSequences"),
                    payload.get("chainSpecificObservationPairs"),
                    summary.get("observationSequenceCount"),
                    summary.get("candidateObservationSequenceCount"),
                    summary.get("chainSpecificObservationSequenceCount"),
                    group_sequences,
                )
            )
            if not is_empty_snapshot:
                payload["marketObservationSourceStage"] = payload.get("marketObservationSourceStage") or payload.get("marketObservationSource") or "cached_snapshot"
                _cache_surface_payload(
                    "dex_market_observation",
                    payload,
                    chain_id=chain_id,
                    protocols=protocols,
                    use_static_observation=use_static_observation,
                    ttl_seconds=0,
                )
                return payload
    payload = dex_market_observation_payload(
        chain_id=chain_id,
        protocols=protocols,
    )
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
        use_static_observation
        and chain_id in _ROBINHOOD_CHAIN_IDS
        and not _database_first_v4_robinhood(chain_id=chain_id, protocols=protocols)
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
    overview_snapshot_has_route_schema = any(
        key in compact_snapshot
        for key in (
            "closedLoopRouteSummaries",
            "chainSpecificObservationPairs",
            "observationSequences",
            "poolRegistry",
        )
    )
    if (
        compact_snapshot.get("exists")
        and not compact_snapshot.get("closedLoopRouteSummaries")
        and not overview_snapshot_has_route_schema
    ):
        # A compact overview that carries anchor symbols but no route summaries
        # can still be a stale snapshot that shadows the live builder.  Only
        # keep it when the scoped observation snapshot has concrete sequences.
        observation_snapshot_for_check = (
            load_market_observation_static(chain_id=chain_id, protocols=scope_protocols)
            if chain_id in _ROBINHOOD_CHAIN_IDS
            else {}
        )
        observation_summary = observation_snapshot_for_check.get("summary") or {}
        overview_has_route_data = bool(
            compact_snapshot.get("closedLoopRouteSummaries")
            or compact_snapshot.get("chainSpecificObservationPairs")
            or observation_snapshot_for_check.get("observationSequences")
            or observation_snapshot_for_check.get("closedLoopRoutes")
            or observation_snapshot_for_check.get("chainSpecificObservationPairs")
            or observation_summary.get("observationSequenceCount")
            or any(
                sequence
                for group in observation_snapshot_for_check.get("candidateObservationGroups") or []
                if isinstance(group, dict)
                for sequence in (group.get("observationSequences") or [])
            )
        )
        if not overview_has_route_data:
            compact_snapshot = {}
            use_compact_static_overview = False
    if compact_snapshot.get("exists"):
        if compact_snapshot.get("closedLoopRouteSummaries"):
            overview_paths = _overview_paths_from_static_summary(compact_snapshot, chain_id=chain_id)
            overview_paths = [_dex_table_route(route, compact_snapshot) for route in overview_paths]
            overview_path_groups = group_overview_paths_by_flash_loan_token(
                overview_paths,
                borrowable_symbols=compact_snapshot.get("borrowableSymbols") or [],
            )
            pool_payload = _overview_pool_payload_from_static_observation(compact_snapshot)
            payload = {
                "mode": "dex_overview",
                "chain_id": chain_id,
                "selected_protocols": protocols or [],
                "tableSource": "dex_table",
                "referencedTable": "ob_table",
                "poolIndexVersion": compact_snapshot.get("poolIndexVersion") or {},
                "summary": {
                    **(compact_snapshot.get("summary") or {}),
                    "arbitragePathCount": len(overview_paths),
                    "candidatePathCount": len(overview_paths),
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
                    "hopCandidateRouteCount": sum(1 for path in overview_paths if (path.get("hopCandidates") or [])),
                    "hopCandidateCombinationCount": sum(
                        max(1, int(path.get("hopCandidateCombinationCount") or 1))
                        for path in overview_paths
                    ),
                    "hopCandidatePoolCount": sum(
                        sum(int(candidate.get("poolCount") or 0) for candidate in path.get("hopCandidates") or [])
                        for path in overview_paths
                    ),
                },
                "decisionBasis": "robinhood_testnet_market_observation_closed_loop" if chain_id == ROBINHOOD_TESTNET_CHAIN_ID else "market_observation_closed_loop",
                "marketObservationSource": "cached_snapshot",
                "marketObservationSourceStage": "cached_snapshot",
                "routeFrameworkSource": "market_observation",
                "marketObservation": {
                    "summary": compact_snapshot.get("summary") or {},
                    "chainSpecificObservationPairs": compact_snapshot.get("chainSpecificObservationPairs") or [],
                    "liquidPoolRegistry": compact_snapshot.get("liquidPoolRegistry") or {},
                },
                "marketObservationSnapshotKind": "dex_overview_compact",
                "liquidPoolRegistry": compact_snapshot.get("liquidPoolRegistry") or {},
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
                ttl_seconds=0,
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
    else:
        if _database_first_v4_robinhood(chain_id=chain_id, protocols=protocols):
            observation = _database_first_robinhood_observation_payload(
                chain_id=chain_id,
                protocols=protocols,
            )
        else:
            observation = dex_market_observation_surface_payload(
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
    price_map = _price_map_from_pairs(observation.get("pairs") or observation.get("bridgePairs") or [])
    frame_context = _route_framework_context_from_observation(
        observation,
        None,
        chain_id=chain_id,
        protocols=protocols,
    )
    ready_paths = frame_context["overview_paths"]
    used_market_observation = bool(frame_context["used_market_observation"])
    watch_pairs = list(frame_context["watch_pairs"])
    anchor_symbols = list(frame_context["anchor_symbols"])
    closed_loop_anchor_symbols = list(frame_context["closed_loop_anchor_symbols"])
    observation_anchor_symbols = list(frame_context["observation_anchor_symbols"])
    ready_paths = [
        _route_with_overview_profit(
            route,
            price_map=price_map,
            chain_id=chain_id,
            use_static_observation=use_static_observation,
        )
        for route in ready_paths
    ]
    ready_paths = [_dex_table_route(route, observation) for route in ready_paths]
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
    payload = {
        "mode": "dex_overview",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "tableSource": "dex_table",
        "referencedTable": "ob_table",
        "poolIndexVersion": observation.get("poolIndexVersion") or {},
        "summary": {
            **(observation.get("summary") or {}),
            "anchorBorrowSymbols": anchor_symbols,
            "closedLoopAnchorSymbols": closed_loop_anchor_symbols,
            "closedLoopAnchorCount": len(closed_loop_anchor_symbols),
            "observationAnchorSymbols": observation_anchor_symbols,
            "observationAnchorCount": len(observation_anchor_symbols),
            "arbitragePathCount": len(ready_paths),
            "candidatePathCount": len(ready_paths),
            "bridgePathCount": sum(1 for route in ready_paths if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "overviewPathGroupCount": len(overview_path_groups),
            "watchPairCount": len(watch_pairs),
            "marketObservationRouteCount": len(ready_paths),
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
            "hopCandidateRouteCount": sum(1 for path in ready_paths if (path.get("hopCandidates") or [])),
            "hopCandidateCombinationCount": sum(
                max(1, int(path.get("hopCandidateCombinationCount") or 1))
                for path in ready_paths
            ),
            "hopCandidatePoolCount": sum(
                sum(int(candidate.get("poolCount") or 0) for candidate in path.get("hopCandidates") or [])
                for path in ready_paths
            ),
        },
        "decisionBasis": decision_basis,
        "marketObservationSource": market_observation_source,
        "marketObservationSourceStage": market_observation_source_stage,
        "routeFrameworkSource": "market_observation",
        "marketObservation": {
            "summary": observation.get("summary") or {},
            "chainSpecificObservationPairs": observation.get("chainSpecificObservationPairs") or [],
            "chainSpecificObservationPools": observation.get("chainSpecificObservationPools") or [],
            "liquidPoolRegistry": observation.get("liquidPoolRegistry") or {},
        },
        "liquidPoolRegistry": observation.get("liquidPoolRegistry") or {},
        "marketObservationSnapshotKind": observation.get("marketObservationSnapshotKind"),
        "poolStaticTable": observation.get("poolStaticTable") or {},
        "flashLoanLiquidity": observation.get("flashLoanLiquidity") or {},
        "dexSettlement": observation.get("dexSettlement") or {},
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
        ttl_seconds=0,
    )
    return payload


def database_overview_snapshot(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    """Expose a static-snapshot-compatible view backed by SQLite.

    Mainnet V4 pages historically consumed ``overview.json`` for a few
    grouping endpoints.  Keep those response contracts while making SQLite
    the only business-data source for the mainnet V4 scope.
    """
    if not _database_first_v4_robinhood(chain_id=chain_id, protocols=protocols):
        return {}
    overview = dex_overview_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=False,
    )
    summaries: list[dict[str, Any]] = []
    pool_registry: dict[str, dict[str, Any]] = {}
    for route in overview.get("arbitragePaths") or []:
        if not isinstance(route, dict):
            continue
        route_view = route.get("overviewRoute") if isinstance(route.get("overviewRoute"), dict) else {}
        path_symbols = list(route_view.get("pathSymbols") or route.get("routeSymbols") or [])
        path_addresses = list(route_view.get("pathAddresses") or route.get("routeAddresses") or [])
        if len(path_symbols) < 4 or path_symbols[0] != path_symbols[-1]:
            continue
        route_pools = [
            pool
            for pool in (
                route.get("executionPools")
                or route.get("preferredPools")
                or route.get("strictQualityPools")
                or []
            )
            if isinstance(pool, dict)
        ]
        for pool in route_pools:
            pool_id = _pool_address(pool)
            if pool_id:
                pool_registry[pool_id.lower()] = pool
        summaries.append(
            {
                "anchorSymbol": route_view.get("anchorBorrowSymbol") or path_symbols[0],
                "counterSymbol": route_view.get("targetSymbol") or path_symbols[-2],
                "pathSymbols": path_symbols,
                "pathText": route_view.get("pathText") or route.get("symbolPair") or " -> ".join(path_symbols),
                "routeAddresses": path_addresses,
                "poolIds": list(route.get("poolIds") or [_pool_address(pool) for pool in route_pools]),
                "routePools": route_pools,
            }
        )
    liquid_registry = overview.get("liquidPoolRegistry")
    if isinstance(liquid_registry, dict):
        for pool_id, pool in liquid_registry.items():
            if isinstance(pool, dict):
                pool_registry.setdefault(str(pool_id).lower(), pool)
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    borrowable = (
        overview.get("borrowableSymbols")
        or summary.get("closedLoopAnchorSymbols")
        or summary.get("observationAnchorSymbols")
        or []
    )
    return {
        "exists": True,
        "path": "sqlite_db",
        "source": "sqlite_db",
        "network": _chain_network_name(chain_id) or "robinhood",
        "chain_id": chain_id,
        "protocols": protocols or [],
        "borrowableSymbols": list(borrowable),
        "summary": summary,
        "closedLoopRouteSummaries": summaries,
        "poolRegistry": pool_registry,
        "liquidPoolRegistry": liquid_registry if isinstance(liquid_registry, dict) else {},
        "poolIndexVersion": overview.get("poolIndexVersion") or {},
    }


def load_observation_overview_for_scope(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    """Return the overview snapshot for a page request.

    Robinhood mainnet V4 uses the SQLite-backed snapshot; other scopes keep
    reading the persisted static overview.
    """
    database_snapshot = database_overview_snapshot(
        chain_id=chain_id,
        protocols=protocols,
    )
    if database_snapshot:
        return database_snapshot
    return load_market_observation_overview_static(
        chain_id=chain_id,
        protocols=protocols,
    )


def dex_market_observation_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    """Return SQLite-backed Robinhood V4 observation; other scopes are legacy.

    Robinhood V4 (mainnet and testnet) is the DB-first scope and reads
    ``v4_closed_loops`` directly.  Non-V4 scopes keep the legacy surface
    builder, which is intentionally isolated so the DB-first runtime never
    imports the old YC x ZC enumeration path.
    """
    if _database_first_v4_robinhood(chain_id=chain_id, protocols=protocols):
        return _database_first_robinhood_observation_payload(
            chain_id=chain_id,
            protocols=protocols,
        )
    from market.binance_market.dex_observation_surface import build_market_observation_payload

    return build_market_observation_payload(
        sys.modules[__name__],
        chain_id=chain_id,
        protocols=protocols,
    )


def _database_first_market_observation_payload(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    anchor: str | None = None,
) -> dict[str, Any]:
    """Return the SQLite-backed observation payload for Robinhood V4."""
    payload = dex_market_observation_payload(
        chain_id=chain_id,
        protocols=protocols,
    )
    if anchor:
        try:
            database_url = str(_database_path_for_scope(chain_id=chain_id))
        except Exception:
            database_url = ""
        synthetic = _synthetic_db_observation_payload(
            chain_id=chain_id,
            protocols=protocols,
            anchor=anchor,
            database_url=database_url,
        )
        if synthetic.get("anchorGroups") or synthetic.get("closedLoopRoutes"):
            payload = synthetic
    return payload


def _database_path_for_scope(*, chain_id: int | None) -> str:
    from core.db.database_path import absolute_database_path

    return str(absolute_database_path())


def _synthetic_db_observation_payload(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    anchor: str,
    database_url: str = "",
) -> dict[str, Any]:
    from core.db.database_path import absolute_database_path
    from core.db.storage_business_io import query
    from core.db.storage_chain_core import load_chain_tokens
    from core.rs_bridge import load_dex_candidate_pools as _storage_candidate_load
    from core.db.storage_market_observation import load_market_observation as _storage_observation_load

    db = str(absolute_database_path(database_url)) if database_url else str(absolute_database_path())
    network = "robinhood-testnet" if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID else "robinhood"
    symbol = str(anchor or "").strip().upper()
    token_rows = load_chain_tokens(db, network=network, chain_id=int(chain_id or 0))
    tokens_by_address = {
        str(row.get("token_address") or "").lower(): str(row.get("token_symbol") or "")
        for row in token_rows
        if str(row.get("token_address") or "")
    }
    candidate = _storage_candidate_load(
        network=network,
        chain_id=int(chain_id or 0),
        protocol="uniswap_v4",
        anchor_symbol=symbol,
        database_url=db,
    )
    anchor_token_rows = candidate.get("anchorTokens") or []
    if not anchor_token_rows:
        return {
            "mode": "dex_market_observation",
            "chain_id": chain_id,
            "selected_protocols": protocols or [],
            "anchorGroups": [],
            "observationSequences": [],
            "closedLoopSequences": [],
            "closedLoopRoutes": [],
            "chainSpecificObservationPairs": [],
            "chainSpecificObservationPools": [],
            "chainSpecificObservationTokens": [],
            "summary": {"anchorBorrowSymbols": [symbol], "observationAnchorSymbols": [symbol]},
        }
    observation = _storage_observation_load(
        db,
        network=network,
        chain_id=int(chain_id or 0),
        protocol="uniswap_v4",
        anchor_symbol=symbol,
    )
    pools = observation.get("pools") or []
    anchor_tokens = anchor_token_rows
    pools_by_edge: dict[str, list[dict[str, Any]]] = {}
    for pool in pools:
        edge_key = "|".join(
            sorted(
                str(pool.get("token0") or "").lower(),
                str(pool.get("token1") or "").lower(),
            )
        )
        pools_by_edge.setdefault(edge_key, []).append(pool)
    closed_loop_rows = query(
        db,
        """
        SELECT loop_key, anchor_address, anchor_symbol, path_text,
               path_addresses_json, path_symbols_json, pool_ids_json,
               status, recommendation_basis
        FROM v4_closed_loops
        WHERE network = ? AND chain_id = ? AND protocol = ? AND market_id = ?
          AND anchor_symbol = ?
        ORDER BY loop_key
        """,
        (network, int(chain_id or 0), "uniswap_v4", f"dex-arbitrage-{int(chain_id or 0)}-uniswap-v4", symbol),
    )
    sequences: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for row in closed_loop_rows:
        path_symbols = _json_loads(row.get("path_symbols_json") or row.get("pathSymbols") or [])
        path_text = str(row.get("path_text") or row.get("pathText") or "")
        pool_ids = _json_loads(row.get("pool_ids_json") or row.get("poolIds") or [])
        if len(pool_ids) != 3:
            continue
        route_pools = []
        for pool_id in pool_ids:
            for pool in pools:
                if str(pool.get("pool_id") or pool.get("poolId") or "").lower() == str(pool_id).lower():
                    route_pools.append(pool)
                    break
        if len(route_pools) != 3:
            continue
        sequences.append(
            {
                "kind": "robinhood_database_closed_loop",
                "source": "v4_closed_loop_db",
                "anchorSymbol": symbol,
                "anchorAddress": row.get("anchor_address") or "",
                "pathSymbols": path_symbols,
                "pathText": path_text,
                "pool": pool_ids[1] if len(pool_ids) > 1 else "",
                "routePools": route_pools,
                "hopCandidates": [],
                "hopCandidateCounts": {"x-y": 1, "y-z": 1, "z-x": 1},
                "hopCandidateCombinationCount": 1,
                "candidatePoolCount": "3",
                "matchedPoolCount": "3",
                "recommendedFirstPriority": True,
            }
        )
        routes.append(
            {
                "kind": "bridge_closed_loop",
                "closedLoop": True,
                "borrowSymbol": symbol,
                "anchorSymbol": symbol,
                "bridgeSymbol": path_symbols[1] if len(path_symbols) > 1 else "",
                "targetSymbol": path_symbols[-2] if len(path_symbols) > 2 else "",
                "pathSymbols": path_symbols,
                "pathText": path_text,
                "routePools": route_pools,
                "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
                "hopCandidates": [],
                "hopCandidateCounts": {"x-y": 1, "y-z": 1, "z-x": 1},
                "hopCandidateCombinationCount": 1,
                "poolPathKey": "|".join(str(pool_id).lower() for pool_id in pool_ids),
                "recommendedFirstPriority": True,
                "routeRecommendationBasis": "fixed_fee_no_hook_low_fee",
                "source": "v4_closed_loop_db",
            }
        )
    directions = observation.get("pairs") or []
    pairs = [
        {
            "symbolPair": "|".join(
                sorted(
                    tokens_by_address.get(str(row.get("token_x_address") or "").lower(), "")
                    or str(row.get("token_x_address") or "")[:8],
                    tokens_by_address.get(str(row.get("token_y_address") or "").lower(), "")
                    or str(row.get("token_y_address") or "")[:8],
                )
            ),
            "addressPair": row.get("address_pair") or "",
            "tokenX": row.get("token_x_address") or "",
            "tokenY": row.get("token_y_address") or "",
            "tokenXSymbol": tokens_by_address.get(str(row.get("token_x_address") or "").lower(), ""),
            "tokenYSymbol": tokens_by_address.get(str(row.get("token_y_address") or "").lower(), ""),
            "allPools": row.get("allPools") or [],
            "preferredPools": row.get("allPools") or [],
            "strictQualityPools": row.get("allPools") or [],
            "executionPools": (row.get("allPools") or [])[:5],
            "poolCount": len(row.get("allPools") or []),
        }
        for row in directions
    ]
    anchor_group = {
        "anchorSymbol": symbol,
        "anchorAddresses": [str(row.get("anchor_address") or "") for row in anchor_token_rows[:1]],
        "candidateSetMode": "bidirectional_same_pool_key_set",
        "ycZcSameSet": True,
        "sharedTokens": anchor_tokens,
        "sharedPools": candidate.get("pools") or [],
        "sharedPoolCount": str(len(candidate.get("pools") or [])),
        "sharedTokenCount": str(len(anchor_tokens)),
        "abnormalTokens": candidate.get("abnormalAnchorTokens") or [],
        "abnormalPools": candidate.get("abnormalPools") or [],
        "abnormalTokenCount": str(len(candidate.get("abnormalAnchorTokens") or [])),
        "abnormalPoolCount": str(len(candidate.get("abnormalPools") or [])),
        "observationSequences": sequences,
        "closedLoopSequences": sequences,
        "observationSequenceCount": str(len(sequences)),
        "closedLoopRouteCount": str(len(routes)),
        "observationState": {
            "status": "reachable" if sequences else "partial",
            "reachable": bool(sequences),
        },
    }
    return {
        "mode": "dex_market_observation",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "marketObservationSource": "sqlite_db",
        "marketObservationSourceStage": "sqlite_db",
        "tableSource": "ob_table",
        "referencedTable": "ch_table",
        "poolIndexVersion": {"path": db, "network": network, "chainId": chain_id, "source": "sqlite_db"},
        "marketObservationStaticTable": {
            "exists": True,
            "network": network,
            "chain_id": chain_id,
            "protocols": protocols or [],
            "path": db,
            "source": "sqlite_db",
        },
        "borrowableSymbols": [symbol],
        "observationAnchorSymbols": [symbol],
        "anchorGroups": [anchor_group],
        "pairs": pairs,
        "bridgePairs": pairs,
        "observationSequences": sequences,
        "closedLoopSequences": sequences,
        "closedLoopRoutes": routes,
        "chainSpecificObservationPairs": [],
        "chainSpecificObservationPools": pools,
        "chainSpecificObservationTokens": anchor_tokens,
        "summary": {
            "borrowableTokenCount": "1",
            "observationAnchorCount": "1",
            "candidateObservationGroupCount": str(len([anchor_group])),
            "closedLoopSequenceCount": str(len(sequences)),
            "closedLoopRouteCount": str(len(routes)),
            "observationSequenceCount": str(len(sequences)),
            "candidateObservationSequenceCount": str(len(sequences)),
            "directPairCount": str(len(pairs)),
            "fundAnchorPairObservationRouteCount": str(len(sequences)),
            "anchorBorrowSymbols": [symbol],
            "observationAnchorSymbols": [symbol],
            "closedLoopAnchorSymbols": [symbol],
        },
    }


def _database_first_robinhood_observation_payload(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Build a compact DB-first observation payload from the loop tables.

    The full market-observation surface can enumerate hundreds of thousands of
    YC x ZC direction combinations even when SQLite only contains a handful of
    real three-hop loops. Overview and quote routing only need the authoritative
    closed-loop set, so this helper reads ``v4_closed_loops`` directly and
    leaves the full direction surface to the market-observation page.
    """
    from core.db.database_path import absolute_database_path
    from core.db.storage_business_io import query
    from core.db.storage_chain_core import load_chain_tokens
    from core.rs_bridge import load_dex_candidate_pools
    from dex.intent_trade.chains.robinhood_common.market_observation_core import (
        db_closed_loop_routes,
        db_closed_loop_sequences,
    )

    db = str(absolute_database_path(database_url))
    network = _chain_network_name(chain_id) or (
        "robinhood-testnet" if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID else "robinhood"
    )
    chain = int(chain_id or 0)
    market_id = f"dex-arbitrage-{chain}-uniswap-v4"
    sequences = db_closed_loop_sequences(
        network=network,
        chain_id=chain,
        protocol="uniswap_v4",
        market_id=market_id,
        kind=(
            "robinhood_testnet_closed_loop"
            if chain == ROBINHOOD_TESTNET_CHAIN_ID
            else "robinhood_closed_loop"
        ),
        database_url=db,
    )
    chain_token_rows = load_chain_tokens(db, network=network, chain_id=chain)
    tokens_by_address = {
        str(row.get("token_address") or "").lower(): row
        for row in chain_token_rows
        if str(row.get("token_address") or "")
    }
    anchor_symbol_rows = query(
        db,
        """
        SELECT DISTINCT anchor_symbol
        FROM dex_candidate_anchor_tokens
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY anchor_symbol
        """,
        (network, chain, "uniswap_v4"),
    )
    anchor_groups: list[dict[str, Any]] = []
    borrowable_symbols: list[str] = []
    for row in anchor_symbol_rows:
        symbol = _norm_symbol(row.get("anchor_symbol"))
        if not symbol:
            continue
        borrowable_symbols.append(symbol)
        candidate = load_dex_candidate_pools(
            network=network,
            chain_id=chain,
            protocol="uniswap_v4",
            anchor_symbol=symbol,
            database_url=db,
        )
        shared_pools: list[dict[str, Any]] = []
        seen_shared_pool_ids: set[str] = set()
        for pool_row in candidate.get("pools") or []:
            if not isinstance(pool_row, dict):
                continue
            pool = dict(pool_row)
            pool["pool"] = str(pool_row.get("pool_id") or pool_row.get("poolId") or "").strip()
            pool["poolId"] = pool["pool"]
            if pool["pool"] and pool["pool"] in seen_shared_pool_ids:
                continue
            if pool["pool"]:
                seen_shared_pool_ids.add(pool["pool"])
            pool["poolKey"] = pool_row.get("pool_key_json") or pool_row.get("poolKey") or {}
            pool["tickSpacing"] = pool_row.get("tick_spacing") or pool_row.get("tickSpacing")
            pool["hooks"] = pool_row.get("hooks")
            pool["sqrtPriceX96"] = pool_row.get("sqrt_price_x96") or pool_row.get("sqrtPriceX96")
            pool["tick"] = pool_row.get("tick")
            pool["liquidity"] = pool_row.get("liquidity")
            pool["liquidityIndex"] = pool_row.get("liquidity_index") or pool_row.get("liquidityIndex")
            pool["estimatedTvlUsd"] = pool_row.get("estimated_tvl_usd") or pool_row.get("estimatedTvlUsd")
            pool["balances"] = _json_loads(pool_row.get("balances_json") or pool_row.get("balances") or {})
            pool["depthStatus"] = pool_row.get("depth_status") or pool_row.get("depthStatus")
            pool["depthBand"] = _json_loads(pool_row.get("depth_band_json") or pool_row.get("depthBand") or {})
            pool["liquidityReadAt"] = pool_row.get("liquidity_read_at") or pool_row.get("liquidityReadAt")
            pool["liquidityBlockNumber"] = (
                pool_row.get("liquidity_block_number") or pool_row.get("liquidityBlockNumber")
            )
            pool["hasLiquidity"] = pool_row.get("has_liquidity")
            if pool["hasLiquidity"] is None:
                pool["hasLiquidity"] = pool_row.get("hasLiquidity")
            pool["usableForObservation"] = pool_row.get("usable_for_observation")
            if pool["usableForObservation"] is None:
                pool["usableForObservation"] = pool_row.get("usableForObservation")
            pool["addressPair"] = pool_row.get("address_pair") or pool_row.get("addressPair")
            shared_pools.append(pool)
        shared_tokens: list[dict[str, Any]] = []
        token_buckets: dict[str, dict[str, Any]] = {}
        for pool in shared_pools:
            for index in (0, 1):
                address = str(pool.get(f"token{index}") or "").lower()
                if not address:
                    continue
                token_row = tokens_by_address.get(address)
                token_symbol = _norm_symbol(
                    pool.get(f"token{index}Symbol")
                    or (token_row.get("token_symbol") if token_row else "")
                )
                if not token_symbol:
                    continue
                bucket = token_buckets.setdefault(
                    token_symbol,
                    {
                        "symbol": token_symbol,
                        "address": address,
                        "poolCount": "0",
                        "pools": [],
                    },
                )
                if not bucket.get("address"):
                    bucket["address"] = address
                bucket["pools"].append(pool)
        for token in sorted(token_buckets.values(), key=lambda item: str(item.get("symbol") or "")):
            token["poolCount"] = str(len(token.get("pools") or []))
            shared_tokens.append(token)
        anchor_address = ""
        if symbol == "ETH":
            anchor_address = "0x0000000000000000000000000000000000000000"
        else:
            for token in shared_tokens:
                if _norm_symbol(token.get("symbol")) == symbol:
                    anchor_address = str(token.get("address") or "")
                    break
        if not anchor_address:
            for token_row in chain_token_rows:
                if _norm_symbol(token_row.get("token_symbol")) == symbol:
                    anchor_address = str(token_row.get("token_address") or "").lower()
                    break
        anchor_groups.append(
            {
                "anchorSymbol": symbol,
                "anchorAddresses": [anchor_address] if anchor_address else [],
                "candidateSetMode": "bidirectional_same_pool_key_set",
                "ycZcSameSet": True,
                "ycZcSharedPoolSet": True,
                "sharedPools": shared_pools,
                "sharedTokens": shared_tokens,
                "sharedPoolCount": str(len(shared_pools)),
                "sharedTokenCount": str(len(shared_tokens)),
                "ycPools": [],
                "zcPools": [],
                "ycTokens": [],
                "zcTokens": [],
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "ycTokensRef": "sharedTokens",
                "zcTokensRef": "sharedTokens",
                "ycPoolCount": str(len(shared_pools)),
                "zcPoolCount": str(len(shared_pools)),
                "ycTokenCount": str(len(shared_tokens)),
                "zcTokenCount": str(len(shared_tokens)),
                "candidatePoolCount": str(len(shared_pools)),
                "candidateQueueCount": str(len(shared_pools) * 2),
            }
        )

    groups_by_anchor: dict[str, dict[str, Any]] = {}
    for group in anchor_groups:
        if isinstance(group, dict):
            symbol = _norm_symbol(group.get("anchorSymbol"))
            if symbol:
                groups_by_anchor[symbol] = group

    # The loop table already contains X-Y-Z-X and X-Z-Y-X as independent
    # directed rows. Do not synthesize the reverse direction in memory here;
    # each SQLite row is the authoritative ordered hop for quote routing.
    routes = db_closed_loop_routes(sequences)

    sequences_by_anchor: dict[str, list[dict[str, Any]]] = {}
    routes_by_anchor: dict[str, list[dict[str, Any]]] = {}
    for sequence in sequences:
        symbol = _norm_symbol(sequence.get("anchorSymbol"))
        if symbol:
            sequences_by_anchor.setdefault(symbol, []).append(sequence)
    for route in routes:
        symbol = _norm_symbol(route.get("anchorSymbol"))
        if symbol:
            routes_by_anchor.setdefault(symbol, []).append(route)

    observed_anchors = sorted(sequences_by_anchor)
    if anchor_groups:
        observed_anchors = sorted(
            {
                symbol
                for symbol in (
                    observed_anchors
                    + [
                        _norm_symbol(group.get("anchorSymbol"))
                        for group in anchor_groups
                        if isinstance(group, dict)
                    ]
                )
                if symbol
            }
        )

    groups: list[dict[str, Any]] = []
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        symbol = _norm_symbol(group.get("anchorSymbol"))
        if not symbol:
            continue
        copied = dict(group)
        group_sequences = sequences_by_anchor.get(symbol, [])
        group_routes = routes_by_anchor.get(symbol, [])
        copied["observationSequences"] = group_sequences
        copied["closedLoopSequences"] = group_sequences
        copied["observationSequenceCount"] = str(len(group_sequences))
        copied["closedLoopRouteCount"] = str(len(group_routes))
        copied["observationState"] = {
            "status": "reachable" if group_sequences else group.get("observationState", {}).get("status"),
            "reachable": bool(group_sequences),
        }
        groups.append(copied)
    for symbol, group_sequences in sequences_by_anchor.items():
        if any(_norm_symbol(group.get("anchorSymbol")) == symbol for group in groups):
            continue
        group_routes = routes_by_anchor.get(symbol, [])
        groups.append(
            {
                "anchorSymbol": symbol,
                "anchorAddresses": [
                    str(sequence.get("anchorAddress") or "")
                    for sequence in group_sequences
                    if str(sequence.get("anchorAddress") or "")
                ],
                "sharedTokens": [],
                "sharedPools": [],
                "sharedPoolCount": "0",
                "sharedTokenCount": "0",
                "abnormalTokens": [],
                "abnormalPools": [],
                "observationSequences": group_sequences,
                "closedLoopSequences": group_sequences,
                "observationSequenceCount": str(len(group_sequences)),
                "closedLoopRouteCount": str(len(group_routes)),
                "observationState": {
                    "status": "reachable" if group_sequences else "partial",
                    "reachable": bool(group_sequences),
                },
            }
        )
    groups.sort(key=lambda group: str(group.get("anchorSymbol") or ""))

    pool_rows: list[dict[str, Any]] = []
    seen_pools: set[str] = set()
    pair_rows: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    tokens_by_address: dict[str, dict[str, Any]] = {}
    for group in groups:
        for token in group.get("sharedTokens") or []:
            if not isinstance(token, dict):
                continue
            address = _token_address(token.get("address"))
            if address:
                tokens_by_address.setdefault(address.lower(), token)
    for route in routes:
        for step in route.get("routePools") or []:
            if not isinstance(step, dict):
                continue
            pool_id = _pool_address(step)
            if pool_id and pool_id.lower() not in seen_pools:
                seen_pools.add(pool_id.lower())
                pool_rows.append(step)
            pair = _sequence_pair_row(step)
            pair_key = (str(pair.get("symbolPair") or ""), str(pair.get("pool") or "").lower())
            if pair_key[0] and pair_key not in seen_pairs:
                seen_pairs.add(pair_key)
                pair_rows.append(pair)

    total_direction_count = 0
    try:
        total_direction_count = int(
            _query_count(
                db,
                "market_observation_yz_pairs",
                "network = ? AND chain_id = ? AND protocol = ?",
                (network, chain, "uniswap_v4"),
            )
        )
    except Exception:
        total_direction_count = len(pair_rows)

    summary = {
        "anchorBorrowSymbols": observed_anchors,
        "observationAnchorSymbols": observed_anchors,
        "closedLoopAnchorSymbols": observed_anchors,
        "observationAnchorCount": str(len(observed_anchors)),
        "candidateObservationGroupCount": str(len(groups)),
        "directPairCount": str(len(pair_rows)),
        "observationSequenceCount": str(total_direction_count),
        "chainSpecificObservationSequenceCount": str(total_direction_count),
        "candidateObservationSequenceCount": str(total_direction_count),
        "closedLoopSequenceCount": str(len(sequences)),
        "closedLoopRouteCount": str(len(routes)),
        "fundAnchorPairObservationRouteCount": str(len(routes)),
        "fundAnchorPairObservationAssociatedPairCount": str(len(pair_rows)),
        "fundAnchorPairObservationAssociatedObservationPoolCount": str(len(pool_rows)),
        "fundAnchorPairObservationAssociatedObservationTokenCount": str(len(tokens_by_address)),
    }
    return {
        "mode": "dex_market_observation",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "marketObservationSource": "sqlite_db",
        "marketObservationSourceStage": "sqlite_db",
        "tableSource": "ob_table",
        "referencedTable": "ch_table",
        "poolIndexVersion": {
            "path": db,
            "network": network,
            "chainId": chain,
            "source": "sqlite_db",
        },
        "marketObservationStaticTable": {
            "exists": True,
            "network": network,
            "chain_id": chain,
            "protocols": protocols or [],
            "path": db,
            "source": "sqlite_db",
        },
        "borrowableSymbols": borrowable_symbols or observed_anchors,
        "observationAnchorSymbols": observed_anchors,
        "anchorGroups": groups,
        "pairs": pair_rows,
        "bridgePairs": pair_rows,
        "observationSequences": sequences,
        "closedLoopSequences": sequences,
        "closedLoopRoutes": routes,
        "chainSpecificObservationPairs": pair_rows,
        "chainSpecificObservationPools": pool_rows,
        "chainSpecificObservationTokens": list(tokens_by_address.values()),
        "summary": summary,
    }


def _filter_observation_payload_by_anchor(payload: dict[str, Any], anchor: str) -> dict[str, Any]:
    normalized = str(anchor or "").strip().upper()
    anchor_groups = [
        group
        for group in payload.get("anchorGroups") or []
        if str(group.get("anchorSymbol") or "").strip().upper() == normalized
    ]
    if not anchor_groups:
        payload["anchorGroups"] = []
        payload["observationSequences"] = []
        payload["closedLoopSequences"] = []
        payload["chainSpecificObservationPairs"] = []
        payload["chainSpecificObservationPools"] = []
        payload["chainSpecificObservationTokens"] = []
        payload["summary"] = {
            **dict(payload.get("summary") or {}),
            "anchorBorrowSymbols": [normalized],
            "observationAnchorSymbols": [normalized],
            "closedLoopAnchorSymbols": [normalized],
            "candidateObservationGroupCount": "0",
            "observationSequenceCount": "0",
            "closedLoopSequenceCount": "0",
            "closedLoopRouteCount": "0",
        }
        payload["mode"] = "dex_market_observation"
        return payload
    payload["anchorGroups"] = anchor_groups
    allowed_anchors = {normalized}
    payload["observationSequences"] = [
        sequence
        for sequence in payload.get("observationSequences") or []
        if str(sequence.get("anchorSymbol") or "").strip().upper() in allowed_anchors
    ]
    payload["closedLoopSequences"] = [
        sequence
        for sequence in payload.get("closedLoopSequences") or []
        if str(sequence.get("anchorSymbol") or "").strip().upper() in allowed_anchors
    ]
    payload["closedLoopRoutes"] = [
        route
        for route in payload.get("closedLoopRoutes") or []
        if str(route.get("anchorSymbol") or "").strip().upper() in allowed_anchors
    ]
    payload["chainSpecificObservationPairs"] = [
        row
        for row in payload.get("chainSpecificObservationPairs") or []
        if str(row.get("anchorSymbol") or row.get("counterAnchorSymbol") or "").strip().upper() in allowed_anchors
    ]
    payload["summary"] = {
        **dict(payload.get("summary") or {}),
        "anchorBorrowSymbols": [normalized],
        "observationAnchorSymbols": [normalized],
        "closedLoopAnchorSymbols": [normalized],
        "candidateObservationGroupCount": str(len(anchor_groups)),
        "observationSequenceCount": str(len(payload["observationSequences"])),
        "closedLoopSequenceCount": str(len(payload["closedLoopSequences"])),
        "closedLoopRouteCount": str(len(payload["closedLoopRoutes"])),
    }
    payload["mode"] = "dex_market_observation"
    return payload


def dex_quote_routes_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    use_static_observation: bool = False,
    anchor: str | None = None,
) -> dict[str, Any]:
    cached = None
    if not anchor:
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
    if chain_id in _ROBINHOOD_CHAIN_IDS:
        payload = _quote_routes_payload_for_robinhood_family(
            chain_id=chain_id,
            protocols=scope_protocols,
            use_static_observation=use_static_observation,
            price_map=None,
            anchor=anchor,
        )
        payload["selected_protocols"] = protocols or scope_protocols
        if not anchor:
            _cache_surface_payload(
                "dex_quote_routes",
                payload,
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
        return payload

    overview = dex_overview_payload(
        chain_id=chain_id,
        protocols=scope_protocols,
        use_static_observation=use_static_observation,
    )
    observation = overview.get("marketObservation") or {}
    settlement = overview.get("dexSettlement") or {}
    price_map = _price_map_from_pairs(observation.get("pairs") or observation.get("bridgePairs") or [])
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=scope_protocols,
        route_quote_analysis_fn=_route_quote_analysis,
        route_size_analysis_fn=_route_size_analysis,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
        price_map=price_map,
        anchor=anchor,
        best_per_anchor_direction=True,
    )
    payload["marketObservationSource"] = observation.get("marketObservationSource") or "computed"
    payload["marketObservationSourceStage"] = (
        observation.get("marketObservationSourceStage")
        or payload["marketObservationSource"]
    )
    if not anchor:
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
    anchor: str | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    def _quote_analysis_with_mode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if use_static_observation:
            kwargs["use_static_observation"] = True
        return _route_quote_analysis(*args, **kwargs)

    def _size_analysis_with_mode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if use_static_observation:
            kwargs["use_static_observation"] = True
        return _route_size_analysis(*args, **kwargs)

    return build_quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=protocols,
        route_quote_analysis_fn=_quote_analysis_with_mode,
        route_size_analysis_fn=_size_analysis_with_mode,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
        price_map=price_map,
        anchor=anchor,
        best_per_anchor_direction=True,
    )


def _quote_routes_payload_for_robinhood_family(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool,
    price_map: dict[str, float] | None = None,
    anchor: str | None = None,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Quote/routing must consume closed loops formed by the DEX overview. It
    # must not rebuild A-B-C-A paths from observation/anchor sets on its own.
    overview = dex_overview_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if observation is None:
        observation = _cached_framework_surface_payload(
            "dex_market_observation",
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
        )
    if price_map is None and observation is not None:
        price_map = _price_map_from_pairs(observation.get("pairs") or observation.get("bridgePairs") or [])
    if price_map is None:
        price_map = {}
    payload = _quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=protocols,
        price_map=price_map,
        anchor=anchor,
        use_static_observation=use_static_observation,
    )
    source_observation = observation if observation is not None else overview
    payload["marketObservationSource"] = source_observation.get("marketObservationSource") or "computed"
    payload["marketObservationSourceStage"] = (
        source_observation.get("marketObservationSourceStage")
        or payload["marketObservationSource"]
    )
    observed_anchor_symbols = (
        list(observation.get("observationAnchorSymbols") or [])
        if observation is not None
        else list((overview.get("summary") or {}).get("observationAnchorSymbols") or [])
    )
    if observed_anchor_symbols:
        payload_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        payload_summary["observationAnchorSymbols"] = observed_anchor_symbols
        payload_summary.setdefault("anchorBorrowSymbols", observed_anchor_symbols)
        payload_summary.setdefault("closedLoopAnchorSymbols", observed_anchor_symbols)
        payload["summary"] = payload_summary
    if not anchor:
        _cache_surface_payload(
            "dex_quote_routes",
            payload,
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
        )
    return payload


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
    "database_overview_snapshot",
    "dex_overview_payload",
    "load_observation_overview_for_scope",
    "dex_quote_request_payload",
    "dex_quote_routes_payload",
    "dex_submission_review_payload",
    "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
    "robinhood_testnet_v4_fund_anchor_pair_observation",
]
