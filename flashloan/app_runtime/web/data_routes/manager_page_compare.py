"""Compatibility facade for the split Manager page adapters."""

from core.manager_api_client import ManagerApiClient, ManagerApiClientError, manager_ui_data_source
from web.data_routes.manager_broadcast_failure_payload import (
    broadcast_failure_manager_payload,
)
from web.data_routes.manager_candidate_payload import (
    candidate_edge_refresh_manager_payload,
    candidate_exchange_refresh_manager_payload,
    candidate_manager_page_payload,
    candidate_manager_shadow_payload,
)
from web.data_routes.manager_closed_loop_payload import (
    closed_loop_manager_page_payload,
    closed_loop_manager_shadow_payload,
)
from web.data_routes.manager_market_observation_payload import (
    market_observation_manager_page_payload,
    market_observation_manager_shadow_payload,
)
from web.data_routes.manager_overview_payload import (
    overview_manager_page_payload,
    runtime_core_manager_overview_envelope,
)
from web.data_routes.manager_page_compare_common import (
    SUBMISSION_FAILURE_CATEGORY_ORDER,
    SUBMISSION_SNAPSHOT_LIMIT,
    manager_compare_requested,
    runtime_core_manager_view_envelope,
)
from web.data_routes.manager_quote_payload import (
    quote_request_manager_page_payload,
    quote_routes_manager_page_payload,
)
from web.data_routes.manager_submission_payload import (
    review_manager_page_payload,
    submission_clear_manager_payload,
    submission_detail_manager_payload,
    submission_execution_manager_payload,
    submission_manager_shadow_payload,
    submission_overview_manager_payload,
)

__all__ = [
    "broadcast_failure_manager_payload",
    "candidate_edge_refresh_manager_payload",
    "candidate_exchange_refresh_manager_payload",
    "candidate_manager_page_payload",
    "candidate_manager_shadow_payload",
    "closed_loop_manager_page_payload",
    "closed_loop_manager_shadow_payload",
    "ManagerApiClient",
    "ManagerApiClientError",
    "manager_compare_requested",
    "manager_ui_data_source",
    "market_observation_manager_page_payload",
    "market_observation_manager_shadow_payload",
    "overview_manager_page_payload",
    "quote_request_manager_page_payload",
    "quote_routes_manager_page_payload",
    "review_manager_page_payload",
    "runtime_core_manager_overview_envelope",
    "runtime_core_manager_view_envelope",
    "SUBMISSION_FAILURE_CATEGORY_ORDER",
    "SUBMISSION_SNAPSHOT_LIMIT",
    "submission_detail_manager_payload",
    "submission_execution_manager_payload",
    "submission_overview_manager_payload",
    "submission_clear_manager_payload",
    "submission_manager_shadow_payload",
]
