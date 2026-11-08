from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def top_level_functions(path: Path) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    result: dict[str, str] = {}
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for index, node in enumerate(nodes):
        end = node.end_lineno or node.lineno
        if index + 1 < len(nodes):
            end = min(end, nodes[index + 1].lineno - 1)
        result[node.name] = "\n".join(lines[node.lineno - 1 : end]).rstrip()
    return result


def write_python(path: Path, header: str, functions: dict[str, str], names: list[str]) -> None:
    body = "\n\n\n".join(functions[name] for name in names)
    path.write_text(header.rstrip() + "\n\n\n" + body + "\n", encoding="utf-8")


def split_storage() -> None:
    source_path = ROOT / "flashloan/src/core/db/storage_cow_execution.py"
    functions = top_level_functions(source_path)
    base = """from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from core.kv_rows import collection_key
from core.local_kv import ensure_store
from core.db.storage_common import db_connection
from core.db.cow_execution_mapping import (
    SELECTED_PROFIT_CAMEL_KEYS,
    SELECTED_PROFIT_SNAKE_KEYS,
    selected_profit_mapping,
)
from core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row
from core.db.storage_cow_policy import (
    COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
    _category_within_retention,
    _decorate_attempt_review,
    _parse_datetime,
    cow_execution_attempt_category,
)

DEFAULT_COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_RETENTION_LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE = "not_executable"
COW_ATTEMPT_CATEGORY_EXECUTION_FAILED = "execution_failed"
COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS = "execution_success"
_COW_ATTEMPT_TABLE = "cow_execution_attempts"
_COW_REVIEW_TABLE = "cow_execution_reviews"
_COW_TABLES = (_COW_ATTEMPT_TABLE, _COW_REVIEW_TABLE)
COW_EXECUTION_SUBMISSION_TABLE = _COW_ATTEMPT_TABLE
COW_EXECUTION_REVIEW_TABLE = _COW_REVIEW_TABLE
_SELECTED_PROFIT_COLUMNS = (
    ("selected_profit_raw", "TEXT"),
    ("selected_profit_amount", "REAL"),
    ("selected_profit_amount_symbol", "TEXT"),
    ("selected_profit_source", "TEXT"),
    ("selected_profit_missing_reason", "TEXT"),
)
_COW_TABLE_ENSURED: set[str] = set()
_COW_TABLE_ENSURED_LOCK = threading.Lock()
"""
    schema_names = [
        "ensure_cow_execution_attempts_table",
        "_ensure_cow_execution_attempts_table_unlocked",
        "_migrate_review_rows_to_review_table",
        "_cow_table_for_category",
        "_cow_tables_for_categories",
    ]
    query_names = ["_row_scope_values", "_scope_sql", "_payload_rows_from_sql", "_summary_rows_from_sql"]
    reader_names = [
        "count_cow_execution_attempts_sqlite_grouped",
        "load_latest_cow_execution_rows_by_pair",
        "load_recent_cow_execution_attempts",
        "load_recent_cow_execution_attempts_source",
        "load_recent_cow_execution_attempt_summaries",
        "load_auto_execute_log_stats",
        "load_recent_cow_execution_attempt_validation_rows",
        "load_recent_cow_execution_attempts_sqlite_grouped",
        "load_cow_execution_attempt_detail",
    ]
    writer_names = [
        "_sqlite_attempt_key",
        "prune_cow_execution_attempts",
        "record_cow_execution_attempts",
        "backfill_cow_execution_attempt_summaries",
        "clear_cow_execution_attempts_sqlite",
    ]
    common = base + "\nfrom core.db.storage_cow_execution_schema import (\n    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,\n    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,\n    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,\n    COW_EXECUTION_REVIEW_TABLE,\n    COW_EXECUTION_SUBMISSION_TABLE,\n    DEFAULT_COW_EXECUTION_RETENTION_DAYS,\n    _COW_ATTEMPT_TABLE,\n    _COW_REVIEW_TABLE,\n    _COW_TABLES,\n    _cow_table_for_category,\n    _cow_tables_for_categories,\n    ensure_cow_execution_attempts_table,\n)\n"
    schema_header = base
    query_header = common + "\nfrom core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row\nfrom core.db.storage_cow_execution_schema import (\n    _COW_ATTEMPT_TABLE,\n    _COW_REVIEW_TABLE,\n    _COW_TABLES,\n    _cow_table_for_category,\n    _cow_tables_for_categories,\n)\n"
    reader_header = common + "\nfrom core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row\nfrom core.db.storage_cow_execution_query import (\n    _payload_rows_from_sql,\n    _scope_sql,\n    _summary_rows_from_sql,\n)\nfrom core.db.storage_cow_execution_schema import (\n    _COW_ATTEMPT_TABLE,\n    _COW_REVIEW_TABLE,\n    _COW_TABLES,\n    _cow_table_for_category,\n    ensure_cow_execution_attempts_table,\n)\n"
    writer_header = common + "\nfrom core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row\nfrom core.db.storage_cow_execution_query import _row_scope_values, _scope_sql\nfrom core.db.storage_cow_execution_schema import (\n    _COW_ATTEMPT_TABLE,\n    _COW_REVIEW_TABLE,\n    _COW_TABLES,\n    _cow_table_for_category,\n    ensure_cow_execution_attempts_table,\n)\n"
    db_dir = ROOT / "flashloan/src/core/db"
    write_python(db_dir / "storage_cow_execution_schema.py", schema_header, functions, schema_names)
    write_python(db_dir / "storage_cow_execution_query.py", query_header, functions, query_names)
    write_python(db_dir / "storage_cow_execution_reader.py", reader_header, functions, reader_names)
    write_python(db_dir / "storage_cow_execution_writer.py", writer_header, functions, writer_names)
    facade = """from __future__ import annotations

from core.db.storage_cow_execution_schema import *
from core.db.storage_cow_execution_schema import (
    _COW_ATTEMPT_TABLE,
    _COW_REVIEW_TABLE,
    _COW_TABLES,
    _cow_table_for_category,
    _cow_tables_for_categories,
)
from core.db.storage_cow_execution_query import *
from core.db.storage_cow_execution_query import (
    _payload_rows_from_sql,
    _row_scope_values,
    _scope_sql,
    _summary_rows_from_sql,
)
from core.db.storage_cow_execution_reader import *
from core.db.storage_cow_execution_writer import *
from core.db.storage_cow_candidate_builders import (
    build_cow_execution_attempts,
    build_cow_market_claim_candidate_attempts,
    build_cow_market_candidate_attempts,
    build_cow_market_claim_pairs,
)
from core.db.storage_cow_policy import (
    COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
    _category_within_retention,
)

__all__ = [
    "backfill_cow_execution_attempt_summaries",
    "build_cow_execution_attempts",
    "build_cow_market_claim_candidate_attempts",
    "build_cow_market_candidate_attempts",
    "build_cow_market_claim_pairs",
    "COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS",
    "COW_ATTEMPT_CATEGORY_RETENTION_DAYS",
    "COW_ATTEMPT_CATEGORY_RETENTION_POLICY",
    "COW_EXECUTION_REVIEW_TABLE",
    "COW_EXECUTION_SUBMISSION_TABLE",
    "count_cow_execution_attempts_sqlite_grouped",
    "_category_within_retention",
    "clear_cow_execution_attempts_sqlite",
    "ensure_cow_execution_attempts_table",
    "load_cow_execution_attempt_detail",
    "load_latest_cow_execution_rows_by_pair",
    "load_auto_execute_log_stats",
    "load_recent_cow_execution_attempts",
    "load_recent_cow_execution_attempts_source",
    "load_recent_cow_execution_attempt_summaries",
    "load_recent_cow_execution_attempts_sqlite_grouped",
    "prune_cow_execution_attempts",
    "record_cow_execution_attempts",
]
"""
    (db_dir / "storage_cow_execution.py").write_text(facade, encoding="utf-8")


def split_scanner() -> None:
    source_path = ROOT / "flashloan/src/dex/runtime/dex_opportunity_scanner_engine.py"
    functions = top_level_functions(source_path)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    scanner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DexOpportunityScanner")
    lines = source.splitlines()
    methods: dict[str, str] = {}
    for node in scanner.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[node.name] = "\n".join(lines[node.lineno - 1 : node.end_lineno]).rstrip()
    imports = """from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import logging
import os
import threading
import time
from typing import Any

from core.db.chain_database_context import chain_database_scope
from dex.runtime.dex_closed_loop_review_queue import FixedRefreshPipeline
from dex.runtime.closed_loop_supervisor import ReviewSupervisor
from dex.intent_trade.chains.robinhood_common.rpc_gate import v4_live_quote_enabled as _rpc_gate_v4_live_quote_enabled
from dex.runtime.dex_opportunity_auto_execute import (
    auto_execute_callback_from_env as default_auto_execute_callback,
    auto_execute_cooldown_seconds as default_auto_execute_cooldown_seconds,
    auto_execute_env_enabled as default_auto_execute_env_enabled,
)
from dex.runtime.dex_opportunity_helpers import (
    _chain_key,
    _network_for_chain_id,
    _now_iso,
    _refresh_payload_summary,
    _snapshot_summary,
    _structured_dex_error,
    _trigger_block_number,
    merge_recent_manual_review_evidence_into_routes,
)
from dex.runtime.dex_onchain_quote_evidence import capture_onchain_trigger_context
from dex.runtime.dex_opportunity_route_runner import (
    _attempt_route_identity,
    _broadcast_profit_value,
    build_dex_opportunity_attempts,
    default_quote_payload_provider,
    default_record_attempts,
    _scanner_onchain_evidence_provider,
)
from dex.runtime.dex_opportunity_snapshot import (
    _auto_execute_log_stats,
    _chain_scope_key,
    _dedupe_payload_routes,
    _route_is_temporary,
    _route_simple_key,
    _scanner_snapshot_ttl_seconds,
)
from dex.runtime.rust_closed_loop import get_native_pool, native_closed_loop_enabled, stop_native_pool
"""
    lifecycle = [
        "_broadcast_supervisor_status", "start", "stop", "_run_supervisor_watchdog", "watch",
        "run_forever", "_watch_closed_loop_supervisors", "_watch_closed_loop_supervisors_inner",
        "scan_now", "scan_async", "_pop_queued_scan", "_watched_items", "_scan_one",
    ]
    state = [
        "_merge_fixed_refresh_into_snapshot", "_default_chain_stats", "_get_chain_stats",
        "reset_dex_opportunity_stats", "_load_cache", "_latest_chain_snapshot_for_status",
        "status", "snapshot", "_write_cache",
    ]
    recording = ["set_auto_execute_enabled", "_record_session_executed", "_record_opportunities", "_auto_execute_ready_attempts"]
    flow = ["_scan_one_inner"]
    runtime_dir = ROOT / "flashloan/src/dex/runtime"
    mixin_base = "\n\nclass {class_name}:\n"
    for file_name, class_name, names in [
        ("dex_opportunity_scanner_lifecycle.py", "DexOpportunityScannerLifecycleMixin", lifecycle),
        ("dex_opportunity_scanner_state.py", "DexOpportunityScannerStateMixin", state),
        ("dex_opportunity_scanner_recording.py", "DexOpportunityScannerRecordingMixin", recording),
        ("dex_opportunity_scanner_flow.py", "DexOpportunityScannerFlowMixin", flow),
    ]:
        body = "\n\n\n".join("    " + line.replace("\n", "\n    ") for name in names for line in [methods[name]])
        (runtime_dir / file_name).write_text(imports + mixin_base.format(class_name=class_name) + body + "\n", encoding="utf-8")
    init_source = methods["__init__"]
    type_aliases = """QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]
AutoExecuteCallback = Callable[..., dict[str, Any]]

CACHE_PATH = Path(__file__).resolve().parent / "cache" / "dex_opportunity_snapshot.json"
logger = logging.getLogger(__name__)
"""
    engine_header = """from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from dex.runtime.dex_closed_loop_review_queue import ClosedLoopReviewQueue, FixedRefreshPipeline
from dex.runtime.dex_opportunity_auto_execute import DexAutoExecuteRunner
from dex.runtime.dex_opportunity_review_engine import DexOpportunityReviewEngineMixin
from dex.runtime.dex_opportunity_scanner_flow import DexOpportunityScannerFlowMixin
from dex.runtime.dex_opportunity_scanner_lifecycle import DexOpportunityScannerLifecycleMixin
from dex.runtime.dex_opportunity_scanner_recording import DexOpportunityScannerRecordingMixin
from dex.runtime.dex_opportunity_scanner_state import DexOpportunityScannerStateMixin
""" + type_aliases
    engine = engine_header + "\n\nclass DexOpportunityScanner(\n    DexOpportunityScannerLifecycleMixin,\n    DexOpportunityScannerStateMixin,\n    DexOpportunityScannerRecordingMixin,\n    DexOpportunityScannerFlowMixin,\n    DexOpportunityReviewEngineMixin,\n):\n" + "\n".join("    " + line.replace("\n", "\n    ") for line in init_source.splitlines()) + "\n"
    source_path.write_text(engine, encoding="utf-8")


def split_manager() -> None:
    source_path = ROOT / "flashloan/app_runtime/web/data_routes/manager_page_compare.py"
    functions = top_level_functions(source_path)
    common = [
        "_manager_read_envelope", "manager_compare_requested", "_shadow_gate",
        "runtime_core_manager_view_envelope", "_manager_snapshot", "_comparison_summary",
        "_shadow_error", "_manager_read_error", "_int_or_none", "_float_or_none",
        "_mapping_value", "_list_length", "_delta",
    ]
    candidate = [
        "candidate_manager_shadow_payload", "candidate_manager_page_payload",
        "candidate_edge_refresh_manager_payload", "candidate_exchange_refresh_manager_payload",
        "_candidate_token_metadata_map", "_ordered_candidate_anchor_ids", "_candidate_key_address",
        "_candidate_manager_pairs", "_candidate_manager_group", "_candidate_recommended_pool_ids",
        "_candidate_manager_pool_dto", "_candidate_manager_summary", "_candidate_row_comparison",
    ]
    closed_loop = ["closed_loop_manager_page_payload", "_closed_loop_manager_route", "closed_loop_manager_shadow_payload", "_closed_loop_row_comparison"]
    market = [
        "market_observation_manager_page_payload", "_exchange_token_meta", "_exchange_token_dto",
        "_exchange_pool_dto", "_exchange_pair_dto", "_exchange_observation_groups",
        "market_observation_manager_shadow_payload", "_candidate_exchange_row_comparison",
    ]
    submission = [
        "submission_manager_shadow_payload", "_manager_review_row_dto", "review_manager_page_payload",
        "_manager_attempt_detail_dto", "_manager_failure_category", "_manager_attempt_overview_dto",
        "_manager_unique_tx_stats", "_manager_route_framework", "submission_overview_manager_payload",
        "submission_detail_manager_payload", "submission_execution_manager_payload", "submission_clear_manager_payload",
        "_review_manager_shadow", "_broadcast_manager_shadow",
    ]
    overview = [
        "overview_manager_page_payload", "runtime_core_manager_overview_envelope", "_overview_manager_contract_error",
        "_closed_loop_group_key_for_symbols", "_closed_loop_control_snapshot", "_attach_closed_loop_control",
    ]
    quote = [
        "quote_routes_manager_page_payload", "quote_request_manager_page_payload", "_match_quote_request_route",
        "_quote_request_pair_not_found", "_quote_request_route_manager_dto", "_manager_quote_verification_payload",
        "_quote_route_anchor", "_quote_route_manager_dto", "_quote_route_group_manager_dto",
        "_quote_route_pool_dto", "_quote_route_pool_has_identity", "_quote_route_pool_ids",
    ]
    package = "web.data_routes"
    common_header = f'''"""Shared RuntimeCore adapter and comparison helpers."""\n\nfrom __future__ import annotations\n\nimport sys\nfrom typing import Any\n\nfrom flask import has_app_context\nfrom core.manager_api_client import ManagerApiClient, ManagerApiClientError, manager_ui_data_source\n\nCANDIDATE_SNAPSHOT_LIMIT = 1000\nCANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT = 200\nCLOSED_LOOP_SNAPSHOT_LIMIT = 1000\nCLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT = 200\nCANDIDATE_EXCHANGE_SNAPSHOT_LIMIT = 1000\nCANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT = 200\nRUNTIME_CORE_OVERVIEW_VIEW = "overview"\nRUNTIME_CORE_OVERVIEW_LIMIT = CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT\nSUBMISSION_SNAPSHOT_LIMIT = 1000\nRUNTIME_CORE_MANAGER_VIEWS = {{\n    "/v1/candidate-edge-pools": "candidate_edge",\n    "/v1/candidate-exchange-pools": "candidate_exchange",\n    "/v1/closed-loops": "closed_loops",\n    "/v1/reviews": "reviews",\n    "/v1/broadcasts": "broadcasts",\n}}\nSUBMISSION_FAILURE_CATEGORY_ORDER = ("all", "broadcast", "submission", "code", "rpc", "execution", "quote", "blocked", "deferred", "other")\n\ndef _compat_manager_client():\n    facade = sys.modules.get("{package}.manager_page_compare")\n    return getattr(facade, "ManagerApiClient", ManagerApiClient) if facade is not None else ManagerApiClient\n'''
    common_header = common_header.replace("def _compat_manager_client():", "def _compat_manager_client():")
    domain_headers = {
        "manager_candidate_payload.py": common_header + "\nfrom core.dex_candidate_response import compact_candidate_payload\nfrom .manager_page_compare_common import *\n",
        "manager_closed_loop_payload.py": common_header + "\nfrom .manager_page_compare_common import *\n",
        "manager_market_observation_payload.py": common_header + "\nfrom .manager_page_compare_common import *\nfrom .manager_candidate_payload import _candidate_key_address, _candidate_token_metadata_map\n",
        "manager_submission_payload.py": common_header + "\nfrom .manager_page_compare_common import *\n",
        "manager_overview_payload.py": common_header + "\nfrom .manager_page_compare_common import *\n",
        "manager_quote_payload.py": common_header + "\nfrom urllib.parse import quote\nfrom .manager_page_compare_common import *\nfrom .manager_overview_payload import _attach_closed_loop_control, _overview_manager_contract_error, runtime_core_manager_overview_envelope\n",
    }
    web_dir = ROOT / "flashloan/app_runtime/web/data_routes"
    for file_name, names in [
        ("manager_page_compare_common.py", common),
        ("manager_candidate_payload.py", candidate),
        ("manager_closed_loop_payload.py", closed_loop),
        ("manager_market_observation_payload.py", market),
        ("manager_submission_payload.py", submission),
        ("manager_overview_payload.py", overview),
        ("manager_quote_payload.py", quote),
    ]:
        header = common_header if file_name == "manager_page_compare_common.py" else domain_headers[file_name]
        body = "\n\n\n".join(functions[name] for name in names)
        (web_dir / file_name).write_text(header.rstrip() + "\n\n\n" + body + "\n", encoding="utf-8")
    facade = '''"""Compatibility facade for the split Manager page adapters."""

from core.manager_api_client import ManagerApiClient, ManagerApiClientError, manager_ui_data_source
from core.dex_candidate_response import compact_candidate_payload

from web.data_routes.manager_page_compare_common import *
from web.data_routes.manager_candidate_payload import *
from web.data_routes.manager_closed_loop_payload import *
from web.data_routes.manager_market_observation_payload import *
from web.data_routes.manager_submission_payload import *
from web.data_routes.manager_overview_payload import *
from web.data_routes.manager_quote_payload import *

__all__ = [
    "candidate_edge_refresh_manager_payload",
    "candidate_exchange_refresh_manager_payload",
    "candidate_manager_page_payload",
    "candidate_manager_shadow_payload",
    "closed_loop_manager_page_payload",
    "closed_loop_manager_shadow_payload",
    "manager_compare_requested",
    "market_observation_manager_page_payload",
    "market_observation_manager_shadow_payload",
    "overview_manager_page_payload",
    "quote_request_manager_page_payload",
    "quote_routes_manager_page_payload",
    "review_manager_page_payload",
    "runtime_core_manager_overview_envelope",
    "runtime_core_manager_view_envelope",
    "SUBMISSION_FAILURE_CATEGORY_ORDER",
    "submission_detail_manager_payload",
    "submission_execution_manager_payload",
    "submission_overview_manager_payload",
    "submission_clear_manager_payload",
    "submission_manager_shadow_payload",
]
'''
    source_path.write_text(facade, encoding="utf-8")


if __name__ == "__main__":
    split_storage()
    split_scanner()
    split_manager()
