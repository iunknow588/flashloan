from __future__ import annotations

import logging

from flask import jsonify

from core.api_response_contract import error_response
from core.chain_data_registry import chain_data_by_chain_id
from core.chain_selection import ChainSelectionError
from core.db.storage_cow_execution import (
    clear_cow_execution_attempts_jsonl,
    load_recent_cow_execution_attempts_jsonl,
)
from core.market_observation_store import load_market_observation_overview_static
from market.binance_market.dex_flash_loan_route_groups import closed_loop_route_groups_payload
from market.binance_market.dex_strategy_registry import (
    dex_strategy_definition,
    dex_strategy_key_for_path,
    dex_strategy_keys,
    dex_strategy_path_for_key,
    resolve_dex_strategy_key,
)
from market.binance_market.dex_surfaces import (
    dex_market_observation_payload,
    dex_market_observation_surface_payload,
    dex_overview_payload,
    dex_quote_request_payload,
    dex_quote_routes_payload,
    dex_submission_review_payload,
)
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from dex.runtime.dex_opportunity_scanner import (
    dex_opportunity_snapshot,
    get_dex_opportunity_scanner,
    run_dex_opportunity_scan_now,
)
from dex.runtime.dex_opportunity_helpers import (
    _merge_onchain_evidence_into_route as _merge_onchain_evidence_into_route,
)
from dex.runtime.dex_route_test_recorder import record_dex_route_test_attempt
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from web import control_panel_data_routes as shared
from market.binance_market.dex_strategy_config import dex_strategy_state, write_dex_strategy_config
from web.data_routes import dex_surface_orchestration as dex_surface_orchestration
from web.data_routes.dex_surface_orchestration import (
    _attach_chain_flow_processing,
    _finalize_market_observation_payload,
    _market_observation_page_payload,
    _refresh_robinhood_testnet_v4_sources,
    _refresh_state_summary,
    _refresh_v4_sources,
    _submission_review_framework_payload,
)
from web.data_routes.dex_surface_response import (
    attach_dex_strategy as _attach_dex_strategy,
    candidate_pagination_args,
    chain_flow_protocol as _chain_flow_protocol,
    dex_surface_payload_for_chain as _dex_surface_payload_for_chain,
    overview_response_payload as _overview_response_payload,
    paginate_candidate_payload,
    request_dex_strategy_arg,
    request_dex_protocols_arg,
    selected_chain_id_from_request,
    submission_networks_from_request,
)
from core.dex_provenance import framework_label
from web.data_routes.dex_surface_helpers import (
    _live_quote_deferred_route,
    _merge_live_route_estimate,
    _merge_recent_attempt_evidence_into_routes,
    _reference_only_live_quote_context,
    _route_dedup_key,
    _scanner_snapshot_is_stale,
    evict_stale_evidence_from_routes,
)


def _bootstrap_dex_surface_dependencies(
    *,
    chain_id: int | None,
    protocols: list[str],
    flow_name: str,
) -> None:
    return dex_surface_orchestration._bootstrap_dex_surface_dependencies(
        chain_id=chain_id,
        protocols=protocols,
        flow_name=flow_name,
        pool_candidates_payload=dex_pool_candidates_payload,
        observation_surface_payload=dex_market_observation_surface_payload,
        overview_payload=dex_overview_payload,
        quote_routes_payload=dex_quote_routes_payload,
    )


logger = logging.getLogger(__name__)


def _submission_review_framework_payload(*, chain_id: int | None, protocols: list[str]) -> dict:
    return dex_surface_orchestration._submission_review_framework_payload(
        chain_id=chain_id,
        protocols=protocols,
        overview_payload=dex_overview_payload,
        framework_label_fn=framework_label,
    )


def register_dex_surface_routes(app, panel) -> None:
    # PANEL is retained as a compatibility argument for callers that pass the
    # control-panel facade at assembly time; individual route groups are kept
    # in sibling modules so the high-touch facade stays a thin registrar.
    del panel
    from web.data_routes.dex_surface_routes_config import (
        register_dex_overview_routes,
        register_dex_strategy_routes,
    )
    from web.data_routes.dex_surface_routes_quotes import (
        register_dex_quote_request_routes,
        register_dex_quote_route_test_routes,
        register_dex_quote_routes,
        register_dex_submission_onchain_check_routes,
    )
    from web.data_routes.dex_surface_routes_ops import (
        register_dex_pool_routes,
        register_dex_scanner_routes,
        register_dex_submission_routes,
    )

    register_dex_strategy_routes(app)
    register_dex_overview_routes(app)
    register_dex_quote_routes(app)
    register_dex_quote_route_test_routes(app)
    register_dex_submission_onchain_check_routes(app)
    register_dex_quote_request_routes(app)
    register_dex_scanner_routes(app)
    register_dex_pool_routes(app)
    register_dex_submission_routes(app)


__all__ = ["register_dex_surface_routes"]
