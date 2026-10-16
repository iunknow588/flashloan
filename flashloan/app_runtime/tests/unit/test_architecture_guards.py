import re
import ast
from pathlib import Path

from web.control_panel import app as control_panel_app


SRC_ROOT = Path(__file__).resolve().parents[2]
SPLIT_SRC_ROOT = SRC_ROOT.parent / "src"
REPO_ROOT = SRC_ROOT.parents[1]

_PACKAGE_MOVES = {
    "bot": "bot",
    "dex": "dex",
    "core": "core",
    "liquidation": "bot/liquidation",
    "account_pool": "bot/account_pool",
    "debt_pool": "bot/debt_pool",
    "page_state": "bot/page_state",
    "intent_trade": "dex/intent_trade",
    "cow_flashloan": "dex/cow_flashloan",
    "market": "market",
    "market_events": "market/market_events",
    "observer_runtime": "market/observer_runtime_pkg",
    "config": "core/config",
    "validation": "core/validation",
    "db": "core/db",
    "execution": "bot/execution",
}

_FILE_MOVES = {
    "web/api_scope_policy.py": "core/api_scope_policy.py",
    "web/robinhood_testnet_flash_pools.py": "dex/robinhood_testnet_flash_pools.py",
    "web/route_context.py": "core/route_context.py",
    "web/dex_provenance.py": "core/dex_provenance.py",
    "web/workflow_context.py": "core/workflow_context.py",
    "web/data_routes/dex_surface_provider.py": "core/dex_surface_provider.py",
    "web/data_routes/cow_evidence_helpers.py": "core/cow_evidence_helpers.py",
    "web/data_routes/binance_market_cow_service.py": "market/binance_market/route_services/cow_support.py",
    "web/data_routes/binance_market_quote_service.py": "market/binance_market/route_services/quote.py",
    "web/data_routes/binance_market_state_service.py": "market/binance_market/route_services/state.py",
    "web/data_routes/liquidation_response.py": "bot/liquidation/route_response.py",
    "web/control_panel_data_cache.py": "core/control_panel_data_cache.py",
    "web/control_panel_data_compact.py": "core/control_panel_data_compact.py",
    "web/control_panel_data_db.py": "core/control_panel_data_db.py",
    "web/control_panel_config.py": "market/binance_market/control_panel_config.py",
    "web/control_panel_opportunity_health.py": "market/binance_market/opportunity_health.py",
    "web/control_panel_stats.py": "core/control_panel_stats.py",
    "web/control_panel_liquidation_borrow_pool_payload.py": "bot/liquidation/panel_borrow_pool_payload.py",
    "web/control_panel_liquidation_scan_support.py": "bot/liquidation/panel_scan_support.py",
    "web/control_panel_cow_pause.py": "dex/runtime/cow_submission_pause.py",
    "web/control_panel_liquidation_pause.py": "bot/liquidation/pause_guard.py",
    "web/control_panel_robinhood_dex.py": "dex/robinhood_dex_panel.py",
    "runtime/liquidation_daemon.py": "bot/runtime/liquidation_daemon.py",
    "runtime/liquidation_engine.py": "bot/runtime/liquidation_engine.py",
    "runtime/liquidation_market_bridge.py": "bot/runtime/liquidation_market_bridge.py",
    "runtime/liquidation_price_trigger.py": "bot/runtime/liquidation_price_trigger.py",
    "runtime/cow_arbitrage_daemon.py": "dex/runtime/cow_arbitrage_daemon.py",
    "runtime/cow_candidate_queue.py": "dex/runtime/cow_candidate_queue.py",
    "runtime/cow_realtime_quote.py": "dex/runtime/cow_realtime_quote.py",
    "runtime/dex_onchain_quote_evidence.py": "dex/runtime/dex_onchain_quote_evidence.py",
    "runtime/dex_opportunity_scanner.py": "dex/runtime/dex_opportunity_scanner.py",
    "runtime/ui_signal_pool_evidence.py": "dex/runtime/ui_signal_pool_evidence.py",
    "runtime/oracle_price_events.py": "market/runtime/oracle_price_events.py",
    "tools/aave_hit_stats.py": "market/tools/aave_hit_stats.py",
}


def _resolved(relative_path: str | Path) -> Path:
    """Resolve a source path inside app_runtime or the split src/ package tree."""
    relative = Path(relative_path)
    exact_move = _FILE_MOVES.get(relative.as_posix())
    if exact_move:
        candidate = SPLIT_SRC_ROOT / exact_move
        if candidate.exists():
            return candidate
    head = relative.parts[0]
    moved = _PACKAGE_MOVES.get(head)
    if moved:
        candidate = SPLIT_SRC_ROOT / Path(moved)
        if len(relative.parts) > 1:
            candidate = candidate / Path(*relative.parts[1:])
        if candidate.exists():
            return candidate
    return SRC_ROOT / relative


def _python_files() -> list[Path]:
    roots = [SRC_ROOT, SPLIT_SRC_ROOT]
    files = []
    for root in roots:
        files.extend(
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts and ".pytest_cache" not in path.parts
            and "runtime" not in path.parts and "chains" not in path.parts
        )
    return files


def _active_source_files() -> list[Path]:
    roots = (
        SPLIT_SRC_ROOT,
        SRC_ROOT / "web",
        REPO_ROOT / "contract" / "avalanche" / "DEX" / "src",
        REPO_ROOT / "contract" / "avalanche" / "DEX" / "scripts",
    )
    exts = {".py", ".js", ".ts", ".tsx", ".html", ".sol"}
    skip_parts = {
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".venv",
        "runtime",
        "cache",
        "evidence",
        "state",
        "logs",
        "market_observation",
        "chains",
        "templates",
        "tests",
    }
    out = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in exts:
                continue
            if any(part in skip_parts for part in path.parts):
                continue
            out.append(path)
    return out


def test_no_wildcard_imports_are_added():
    offenders = []
    for path in _python_files():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.match(r"^\s*(from\s+\S+\s+import\s+\*|import\s+\*)", line):
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{line_no}")

    assert offenders == []


def test_no_silent_broad_exceptions_are_added():
    offenders = []
    for path in _python_files():
        if "tests" in path.parts or ".pytest_cache" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            handler_type = node.type
            broad_type = None
            if handler_type is None:
                broad_type = "bare"
            elif isinstance(handler_type, ast.Name) and handler_type.id in {"Exception", "BaseException"}:
                broad_type = handler_type.id
            if broad_type is None:
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                try:
                    display = path.relative_to(SRC_ROOT).as_posix()
                except ValueError:
                    display = path.relative_to(SPLIT_SRC_ROOT).as_posix()
                offenders.append(f"{display}:{node.lineno}:{broad_type}")

    assert offenders == []


def test_split_packages_exist_without_cross_imports():
    split_root = SRC_ROOT.parent / "src"
    for package in ("bot", "dex", "market", "core"):
        assert (split_root / package / "__init__.py").exists(), f"split package missing {package}/__init__.py"

    offenders = []
    transitional_cross_imports = {
            "market/observer_runtime_daemon.py",
            "bot/page_state/service.py",
            "bot/tools/watch_bnb_venus_liquidations.py",
        # CLI tools are transitional adapters while their domain services are
        # still being split from the legacy package names.
            "dex/tools/probe_signal_pool_quotes.py",
            "dex/tools/probe_v3_pool_arbitrage.py",
                "dex/tools/quote_dynamic_candidates.py",
                "dex/tools/build_robinhood_mainnet_eth_closed_loop_sets.py",
                "dex/tools/run_unified_data_simulation.py",
                "dex/tools/run_unified_live_shadow_bridge.py",
            # These DEX intent modules now use split package names, but still
            # depend on execution and strategy services pending domain-level
            # extraction.
            "dex/intent_trade/builder.py",
            "dex/intent_trade/direct_impl.py",
            "dex/intent_trade/direct_unified_broadcast.py",
            "market/binance_market/cow_tokens.py",
                # DEX quote page adapters consume the DEX-owned error stack
                # contract to publish route-level API/page results.
                "market/binance_market/dex_quote_route_builder.py",
                "market/binance_market/dex_quote_surface.py",
                    "market/binance_market/dex_surface_quotes.py",
                    "market/binance_market/dex_surface_route_quotes.py",
                    "market/binance_market/dex_surface_v4.py",
                "market/binance_market/dex_surfaces.py",
                "market/binance_market/pool_candidates.py",
                    "market/binance_market/cow_legacy_compat.py",
                    "market/binance_market/cow_legacy_compat_impl.py",
                "market/binance_market/service.py",
                "market/binance_market/market_snapshot.py",
            "market/observer_common.py",
            "market/market_events/volatility.py",
            "market/tools/audit_bnb_venus_file_state.py",
            "market/tools/build_avalanche_l1_pool_static_table.py",
            "market/tools/build_bnb_venus_market_static_table.py",
            "market/tools/watch_bnb_venus_account_scan_status.py",
            "bot/execution/flashloan_arbitrage_payload.py",
            "market/dex_target_common.py",
            "market/dex_target_loaders.py",
            "market/tools/quote_dynamic_candidates.py",
                "bot/execution/bnb_exit_quote.py",
                    "bot/execution/bnb_runtime.py",
                    "bot/tools/import_bnb_venus_borrowers_from_dune.py",
                    "bot/tools/rebuild_bnb_venus_borrowers.py",
                    "bot/tools/resume_bnb_venus_two_month_scan.py",
                    "bot/tools/smoke_bnb_venus_readonly.py",
                    "market/tools/quote_dynamic_candidates.py",
                    "market/tools/audit_static_table_triangular_coverage.py",
                    "market/tools/audit_ui_chain_pair_coverage.py",
                    "dex/tools/refresh_robinhood_dex_candidates.py",
                "market/tools/check_robinhood_testnet_lending_liquidity.py",
                "market/tools/build_bnb_niche_token_static_table.py",
                "market/tools/check_robinhood_candidate_pool_supply.py",
                "market/tools/build_bnb_venus_market_static_table.py",
                "market/tools/probe_robinhood_chain_health.py",
                "market/tools/probe_robinhood_contract_verification.py",
                "market/tools/run_bnb_niche_xy_dynamic_monitor.py",
                "market/tools/probe_robinhood_market_snapshot.py",
                "market/tools/observe_bnb_ui_signals.py",
                    "bot/execution/uniswap_v4_sdk_adapter.py",
                    "market/binance_market/dex_route_test.py",
                    "market/binance_market/dex_route_helpers.py",
                    "market/binance_market/dex_quote_surface_helpers.py",
                        "market/binance_market/dex_surface_observation_chain.py",
                        "market/binance_market/dex_surface_overview.py",
                        "market/binance_market/dex_market_row_helpers.py",
                        "market/binance_market/dex_surface_framework.py",
                        "core/market_observation_store.py",
                            "core/db/builders.py",
                            "core/db/import_existing_json.py",
                            "core/db/import_v4_evidence.py",
                            "core/db/page_payloads.py",
                            "dex/tools/refresh_robinhood_mainnet_v4_evidence.py",
                            "dex/tools/refresh_robinhood_v4_live.py",
                }
    for path in split_root.rglob("*.py"):
        if "runtime" in path.parts or "chains" in path.parts:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.match(r"^\s*(?:from|import)\s+(\w+)", line)
            if not match:
                continue
            imported = match.group(1)
            if imported not in {"bot", "dex", "market"}:
                continue
            current_package = path.relative_to(split_root).parts[0]
            if current_package != imported:
                relative = path.relative_to(split_root).as_posix()
                if relative in transitional_cross_imports:
                    continue
                offenders.append(f"{path.relative_to(split_root)}:{line_no}")

    assert offenders == []


def test_app_runtime_moved_packages_are_shim_only():
    moved_packages = (
        # All moved packages now resolve directly to flashloan/src split paths.
    )

    for package in moved_packages:
        package_root = SRC_ROOT / package
        assert package_root.is_dir(), f"compatibility package missing: {package}"
        files = [
            path.name
            for path in package_root.iterdir()
            if path.is_file() and path.name != "__pycache__"
        ]
        assert files == ["__init__.py"], f"{package} contains duplicate implementation files: {files}"
        assert (package_root / "__init__.py").read_text(encoding="utf-8").strip()

    assert not (SRC_ROOT / "flashloan").exists(), "nested app_runtime/flashloan output directory must stay removed"


def test_split_core_facade_is_importable_without_cross_module_imports():
    from src.core import errors as split_errors
    from src.core import json_io as split_json_io
    from src.core import types as split_types

    assert split_errors.normalize_error_code("made-up") == "validation"
    assert split_json_io.read_json(Path("does-not-exist.json")) is None
    assert "MarketScope" in split_types.__all__
    assert "EvidenceEnvelope" in split_types.__all__


def test_dex_overview_response_compaction_is_core_owned():
    from core.dex_response import compact_dex_overview_response

    payload = {
        "arbitragePaths": [{
            "symbolPair": "A:B",
            "overviewProfit": {"observedNetProfitUsd": 1, "internal": "drop"},
            "executionPools": [{"pool": "p1", "internal": "drop"}],
        }],
        "marketObservation": {"summary": {"count": 1}, "internal": "drop"},
    }
    compact = compact_dex_overview_response(payload)
    assert compact["arbitragePaths"][0]["overviewProfit"] == {"observedNetProfitUsd": 1}
    assert compact["arbitragePaths"][0]["executionPools"] == [{"pool": "p1"}]
    assert compact["marketObservation"] == {"summary": {"count": 1}}


def test_candidate_response_pagination_is_core_owned():
    from core.dex_candidate_response import compact_candidate_payload

    result = compact_candidate_payload(
        {"pairs": [{"symbolPair": "A:B"}, {"symbolPair": "C:D"}], "summary": {}},
        page=2, page_size=1,
    )
    assert result["pairs"] == [{"symbolPair": "C:D"}]
    assert result["pagination"]["candidate_groups"]["page"] == 2


def test_pool_manager_evidence_requires_independent_groups():
    from src.core.types import validate_pool_manager_evidence

    payload = {
        "balance": {
            "token_address": "0x1",
            "decimals": 18,
            "balance_raw": "100",
            "balance_decimal": "0.0000000000000001",
            "provider_address": "0x2",
            "observed_at": "2026-09-04T00:00:00Z",
            "source": "eth_call",
            "evidence_ref": "e1",
        },
        "coverage": {
            "candidate_pool_count": 5,
            "min_candidate_pool_count": 5,
            "scan_scope": {"chain_id": 46630},
            "generation_id": "g1",
            "yc_zc_same_set": True,
            "shared_pools_ref": ["p1"],
        },
        "price": {
            "price_source": "binance",
            "price_observed_at": "2026-09-04T00:00:00Z",
            "price_freshness": "fresh",
            "price_expires_at": "2026-09-04T00:01:00Z",
        },
        "freshness": {
            "observed_at": "2026-09-04T00:00:00Z",
            "freshness": "fresh",
            "expires_at": "2026-09-04T00:01:00Z",
            "stale_at": "2026-09-04T00:02:00Z",
            "status": "valid",
        },
    }
    assert validate_pool_manager_evidence(payload)["coverage"]["generation_id"] == "g1"

    import pytest

    incomplete = dict(payload)
    incomplete["price"] = dict(payload["price"])
    del incomplete["price"]["price_source"]
    with pytest.raises(ValueError, match="price.price_source"):
        validate_pool_manager_evidence(incomplete)

    stale = dict(payload)
    stale["freshness"] = {**payload["freshness"], "status": "stale"}
    with pytest.raises(ValueError, match="not fresh"):
        validate_pool_manager_evidence(stale)


def test_refactored_helper_modules_stay_under_line_limits():
    limits = {
        "account_pool/state.py": 300,
        "debt_pool/workflow.py": 300,
        "liquidation/account_backfill.py": 300,
        "liquidation/account_payload_service.py": 150,
        "liquidation/health_scan_workflow.py": 250,
        "liquidation/scan_presenter.py": 300,
        "liquidation/scan_orchestrator.py": 220,
        "liquidation/scan_payload_service.py": 420,
        "liquidation/borrow_pool_scan_service.py": 350,
        "liquidation/account_backfill_workflow.py": 220,
        "liquidation/refresh_service.py": 150,
        "liquidation/scan_selection.py": 250,
        "liquidation/scan_request.py": 160,
        "liquidation/execution_broadcast.py": 100,
        "liquidation/execution_orchestrator.py": 650,
        "liquidation/execution_receipt.py": 150,
        "liquidation/execution_service.py": 100,
        "liquidation/execution_static_call.py": 100,
        "liquidation/execution_submission_gate.py": 120,
        "liquidation/execution_target.py": 150,
        "liquidation/execution_state.py": 220,
        "liquidation/submission_service.py": 150,
        "liquidation/scan_summary_service.py": 100,
        "execution/static_call.py": 300,
        "execution/receipt_formatter.py": 100,
        "intent_trade/builder.py": 300,
        "intent_trade/direct.py": 480,
        "intent_trade/direct_intent.py": 180,
        "intent_trade/direct_onchain_config.py": 300,
        "intent_trade/direct_symbols.py": 140,
        "intent_trade/direct_submission.py": 650,
        "intent_trade/direct_submission_preflight.py": 240,
        "intent_trade/direct_submission_context.py": 120,
        "intent_trade/direct_unified.py": 500,
        "intent_trade/direct_unified_broadcast.py": 300,
        "intent_trade/direct_unified_cache.py": 400,
        "intent_trade/direct_unified_candidates.py": 700,
        "intent_trade/direct_unified_cost.py": 500,
        "intent_trade/direct_unified_evidence.py": 250,
        "intent_trade/direct_unified_preflight.py": 520,
        "intent_trade/direct_unified_reports.py": 320,
        "intent_trade/direct_unified_reserve.py": 160,
        "config/intent_trade.py": 300,
        "page_state/models.py": 300,
        "page_state/store.py": 300,
        "observer_runtime/service.py": 250,
        "cow_flashloan/order_submission.py": 300,
        "market/binance_market/cow_tokens.py": 350,
        "market/binance_market/cow_math.py": 400,
        "market/binance_market/candidate_service.py": 120,
        "market/binance_market/dex_surface_analysis.py": 260,
            "market/binance_market/dex_quote_surface.py": 500,
        "market/binance_market/quote_service.py": 400,
        "market/binance_market/submission_readiness.py": 120,
        "market/binance_market/submission_service.py": 220,
        "market/binance_market/pool_support.py": 450,
        "market/binance_market/service.py": 2200,
        "market/binance_market/dex_quote_route_common.py": 220,
        "market/binance_market/dex_quote_route_builder.py": 260,
        "intent_trade/chains/_dex_surface_flows.py": 220,
        "intent_trade/chains/robinhood/dex_surfaces.py": 20,
        "intent_trade/chains/robinhood_testnet/dex_surfaces.py": 20,
        "core/workflow.py": 300,
            "core/contracts.py": 300,
            "core/chain_data_registry.py": 920,
            "market/binance_market/dex_surfaces.py": 3400,
            "market/binance_market/service.py": 1950,
            "market/binance_market/pool_candidates.py": 1350,
            "web/control_panel.py": 1320,
            "web/control_panel_liquidation_base.py": 980,
            "web/control_panel_liquidation_routes.py": 900,
            "db/storage_cow_execution.py": 1700,
            "db/storage_liquidation_pool.py": 1400,
            "runtime/dex_onchain_quote_evidence.py": 1500,
            "runtime/dex_opportunity_scanner.py": 1100,
            "dex/runtime/dex_quote_evidence_common.py": 550,
            "dex/runtime/dex_quote_evidence_rpc.py": 250,
            "dex/runtime/dex_quote_evidence_pool.py": 500,
            "dex/runtime/dex_quote_evidence_size.py": 350,
                "dex/runtime/dex_quote_evidence_execution_probe.py": 450,
                "dex/runtime/dex_quote_evidence_helpers.py": 150,
                "dex/runtime/dex_opportunity_review_engine.py": 700,
                "dex/runtime/dex_opportunity_scanner_engine.py": 1000,
                "intent_trade/direct_impl.py": 1620,
                    "intent_trade/chains/robinhood_testnet/v4_pool_liquidity.py": 1300,
                "core/workflow_context.py": 100,
                "core/db/storage_business_schema.py": 1800,
                "core/db/builders.py": 1900,
        "web/control_panel_market.py": 450,
        "web/control_panel_liquidation_borrow_pool.py": 240,
        "bot/liquidation/panel_borrow_pool_payload.py": 180,
        "web/control_panel_liquidation_scan.py": 520,
        "bot/liquidation/panel_scan_support.py": 300,
        "market/binance_market/opportunity_health.py": 200,
        "web/control_panel_data.py": 200,
        "core/control_panel_data_cache.py": 150,
        "core/control_panel_data_db.py": 500,
        "web/control_panel_data_routes.py": 500,
        "core/dex_provenance.py": 220,
        "web/control_panel_liquidation_execute.py": 500,
        "web/data_routes/binance_market.py": 500,
        "web/data_routes/binance_market_admin.py": 300,
        "web/data_routes/binance_market_cow.py": 200,
        "web/data_routes/liquidation_data.py": 250,
        "web/data_routes/market_analysis.py": 500,
        "db/storage_liquidation.py": 250,
        "db/storage_liquidation_accounts.py": 300,
        "db/storage_liquidation_attempts.py": 300,
    }

    for relative_path, limit in limits.items():
        line_count = len(_resolved(relative_path).read_text(encoding="utf-8").splitlines())
        assert line_count < limit, f"{relative_path} has {line_count} lines"


def test_active_source_files_stay_under_1000_lines():
    known_over_limit = {
        "market/binance_market/dex_route_test.py",
        "web/data_routes/dex_surface_routes_ops.py",
        "web/data_routes/dex_surface_routes_quotes.py",
        "web/static/chain_selector.js",
        "web/static/dex_quotes/quotes_app.js",
        "web/static/dex_quotes/quotes_render.js",
        "core/market_observation_compact.py",
        "core/db/builders.py",
        "core/db/storage_business_schema.py",
        "market/binance_market/dex_route_candidate_selection.py",
        "market/binance_market/dex_surfaces.py",
    }
    offenders = []
    for path in _active_source_files():
        count = sum(1 for _ in path.open(encoding="utf-8-sig", errors="ignore"))
        if count > 1000:
            try:
                relative = path.relative_to(SPLIT_SRC_ROOT).as_posix()
            except ValueError:
                relative = path.relative_to(SRC_ROOT).as_posix()
            if relative in known_over_limit:
                continue
            offenders.append(f"{path}: {count} lines")
    assert offenders == []


def test_dex_opportunity_runtime_layer_boundaries():
    snapshot_source = _resolved("dex/runtime/dex_opportunity_snapshot.py").read_text(encoding="utf-8")
    route_runner_source = _resolved("dex/runtime/dex_opportunity_route_runner.py").read_text(encoding="utf-8")
    review_source = _resolved("dex/runtime/dex_opportunity_review_engine.py").read_text(encoding="utf-8")
    engine_source = _resolved("dex/runtime/dex_opportunity_scanner_engine.py").read_text(encoding="utf-8")
    facade_source = _resolved("dex/runtime/dex_opportunity_scanner.py").read_text(encoding="utf-8")

    assert "dex_opportunity_route_runner" not in snapshot_source
    assert "dex_opportunity_review_engine" not in snapshot_source
    assert "dex_opportunity_scanner_engine" not in snapshot_source
    assert "dex_opportunity_scanner_engine" not in route_runner_source
    assert "dex_opportunity_review_engine" not in route_runner_source
    assert "dex_opportunity_scanner_engine" not in review_source
    assert "dex_opportunity_review_engine" not in facade_source
    assert "DexOpportunityReviewEngineMixin" not in facade_source
    assert "DexOpportunityReviewEngineMixin" in engine_source
    assert "class DexOpportunityScanner(DexOpportunityReviewEngineMixin)" in engine_source

    implementation_names = (
        "def _fixed_refresh_group",
        "def _closed_loop_review_one_route",
        "def _run_manual_route_review_and_broadcast",
        "def _fixed_refresh_attempt",
        "def _apply_fixed_refresh_evidence_to_route",
        "def _attach_auto_execute_to_attempt",
        "def _scan_one",
        "def _write_cache",
        "def _record_opportunities",
        "def _auto_execute_ready_attempts",
        "def _merge_fixed_refresh_into_snapshot",
    )
    for name in implementation_names:
        assert name not in facade_source
    for name in (
        "def _fixed_refresh_group",
        "def _closed_loop_review_one_route",
        "def _run_manual_route_review_and_broadcast",
        "def _fixed_refresh_attempt",
        "def _apply_fixed_refresh_evidence_to_route",
        "def _attach_auto_execute_to_attempt",
    ):
        assert name not in engine_source

def test_repo_root_keeps_only_intended_config_files():
    allowed_files = {
        ".gitignore",
        "README.md",
        ".replit",
        "pytest.ini",
        "replit.nix",
    }
    generated_suffixes = (".log", ".json", ".sqlite3", ".tmp")
    generated_prefixes = (
        "dex_runtime_",
        "mainnet_",
        "run-",
        "run_codex_",
        "restart_",
        "step.",
        "watcher_",
        "web-testnet-46630.",
    )
    offenders = [
        path.name
        for path in REPO_ROOT.iterdir()
        if path.is_file()
        and path.name not in allowed_files
        and not path.name.lower().endswith(generated_suffixes)
        and not any(path.name.lower().startswith(prefix) for prefix in generated_prefixes)
    ]
    assert offenders == []


def test_v4_formal_refresh_path_is_rust_owned_for_schema_scan_and_merge():
    source = _resolved("dex/tools/refresh_robinhood_v4_live.py").read_text(encoding="utf-8")
    for needle in (
        "rust_available()",
        "load_v4_scan_state(",
        "update_v4_scan_state(",
        "merge_v4_pool_index(",
        "import_v4_evidence_rows(",
        "prune_v4_evidence_rows(",
        "builders.build_all_anchor_data(",
    ):
        assert needle in source, f"refresh_robinhood_v4_live.py no longer uses {needle}"
    assert "from core.db.import_existing_json import" not in source
    assert "import core.db.import_existing_json" not in source


def test_v4_formal_refresh_path_keeps_python_business_writes_out_of_module_scope():
    source = _resolved("dex/tools/refresh_robinhood_v4_live.py").read_text(encoding="utf-8")
    top_level_write_imports = [
        line.strip()
        for line in source.splitlines()
        if line
        and not line.startswith((" ", "\t"))
        and (
            line.startswith("from core.db.import_v4_evidence import")
            or line.startswith("from core.db.storage_v4_exchange import")
            or line.startswith("import core.db.storage_v4_exchange")
        )
    ]
    assert top_level_write_imports == []


def test_v4_anchor_builder_entry_point_is_rust_owned():
    source = _resolved("core/db/builders.py").read_text(encoding="utf-8")
    assert "rust_available()" in source
    assert "build_v4_anchor_data(" in source
    assert "return _rust_build_v4_anchor_data(" in source


def test_v4_evidence_write_entry_points_are_rust_owned():
    source = _resolved("dex/tools/v4_rust_refresh.py").read_text(encoding="utf-8")
    for needle in (
        "rust_available()",
        "prepare_v4_evidence_rows(",
        "import_v4_evidence(",
        "prune_v4_evidence(",
    ):
        assert needle in source, f"v4_rust_refresh.py no longer uses {needle}"


def test_v4_page_and_quote_paths_never_import_json_importers_or_builders():
    paths = (
        "dex/intent_trade/chains/robinhood/flash_loan_pool_views.py",
        "dex/intent_trade/chains/robinhood_testnet/flash_pool_view.py",
        "dex/intent_trade/chains/robinhood_common/v4_sqlite_view.py",
        "market/binance_market/dex_surfaces.py",
        "market/binance_market/dex_quote_surface.py",
    )
    forbidden = (
        "import_existing_json",
        "import_v4_evidence",
        "build_all_anchor_data",
        "build_closed_loops_anchor",
        "build_market_observation_anchor",
        "build_candidate_anchor",
    )
    for relative in paths:
        source = _resolved(relative).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{relative} contains {needle}"


def test_v4_quote_closed_loop_anchors_derive_from_route_paths_only():
    source = _resolved("market/binance_market/dex_quote_surface.py").read_text(encoding="utf-8")
    assert "_closed_loop_anchor_symbols_from_paths" in source
    assert "closed_loop_anchor_symbols = _closed_loop_anchor_symbols_from_paths(paths)" in source


def test_key_modules_stop_using_the_storage_aggregator():
    forbidden_modules = {
        "web/control_panel.py": ["from db.storage import"],
        "web/control_panel_liquidation_scan.py": ["from db.storage import"],
        "market/observer_common.py": ["from db.storage import"],
        "market/observer_runtime_daemon.py": ["from db.storage import"],
    }

    for relative_path, forbidden_imports in forbidden_modules.items():
        source = _resolved(relative_path).read_text(encoding="utf-8")
        for forbidden_import in forbidden_imports:
            assert forbidden_import not in source, f"{relative_path} still imports the storage aggregator"


def test_core_and_workflow_packages_keep_layer_boundaries():
    core_sources = [
        path for path in _resolved("core").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    liquidation_sources = [
        path for path in _resolved("liquidation").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    intent_sources = [
        path for path in _resolved("intent_trade").rglob("*.py")
        if "__pycache__" not in path.parts
    ]

    def has_module_import(source: str, module_name: str) -> bool:
        pattern = rf"^\s*(from\s+{re.escape(module_name)}(?=[\s.])|import\s+{re.escape(module_name)}(?=[\s.]))"
        return any(re.match(pattern, line) for line in source.splitlines())

    assert all(
        not has_module_import(path.read_text(encoding="utf-8"), "web")
        for path in core_sources
    )
    assert all(
        not has_module_import(path.read_text(encoding="utf-8"), "intent_trade")
        for path in liquidation_sources
    )
    assert all(
        not has_module_import(path.read_text(encoding="utf-8"), "web")
        for path in liquidation_sources
    )
    assert all(
        not has_module_import(path.read_text(encoding="utf-8"), "liquidation")
        for path in intent_sources
    )


def test_cow_intent_construction_stays_out_of_runtime_and_web_layers():
    runtime_source = _resolved("runtime/cow_arbitrage_daemon.py").read_text(encoding="utf-8")
    web_source = _resolved("web/control_panel_data_routes.py").read_text(encoding="utf-8")
    intent_source = _resolved("intent_trade/builder.py").read_text(encoding="utf-8")
    config_source = _resolved("config/intent_trade.py").read_text(encoding="utf-8")

    assert "build_triangular_onchain_intent_trade(" in runtime_source
    assert "_cow_pure_profit_intent(" not in runtime_source
    assert "route_trade_fee_amount" not in web_source
    assert "flashloan_fee_amount" not in web_source
    assert "def build_cow_intent_trade(" in intent_source
    assert "def intent_costs(" in config_source


def test_cross_page_functionality_uses_dedicated_packages():
    package_paths = {
        "page_state": ["models.py", "store.py", "service.py"],
        "market_events": ["volatility.py", "store.py"],
        "observer_runtime": ["service.py"],
        "market/binance_market": ["__init__.py", "service.py"],
    }
    retired_web_modules = (
        "page_state.py",
        "page_state_store.py",
        "page_state_service.py",
        "market_volatility_event_service.py",
        "market_volatility_event_store.py",
        "observer_runtime_service.py",
        "binance_market_service.py",
    )
    web_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SRC_ROOT / "web").glob("*.py")
    )

    for package, files in package_paths.items():
        for filename in files:
            assert _resolved(Path(package) / filename).exists(), f"{package}/{filename} is missing"
    for filename in retired_web_modules:
        assert not (SRC_ROOT / "web" / filename).exists(), f"web/{filename} should be a dedicated package"
    assert "from web.page_state" not in web_sources
    assert "from web.market_volatility_event" not in web_sources
    assert "from web.observer_runtime_service" not in web_sources


def test_feature_packages_have_matching_test_directories():
    expected_test_directories = (
        "tests/unit/account_pool",
        "tests/unit/cow_flashloan",
        "tests/unit/debt_pool",
        "tests/unit/intent_trade",
        "tests/unit/config",
        "tests/unit/market_events",
        "tests/integration/cow_flashloan",
        "tests/integration/liquidation",
        "tests/functional/liquidation",
        "tests/functional/page_state",
        "tests/functional/observer_runtime",
        "tests/functional/binance_market",
    )

    for relative_path in expected_test_directories:
        assert _resolved(relative_path).is_dir(), f"{relative_path} is missing"


def test_cow_flashloan_helpers_live_in_dedicated_package():
    expected_modules = (
        "cow_flashloan/routes.py",
        "cow_flashloan/order_submission.py",
        "cow_flashloan/capabilities.py",
    )
    retired_execution_modules = (
        "cow_routes.py",
        "cow_order_submission.py",
        "cow_flashloan_capabilities.py",
    )
    retired_imports = (
        "execution" + ".cow_routes",
        "execution" + ".cow_order_submission",
        "execution" + ".cow_flashloan_capabilities",
    )
    python_sources = "\n".join(path.read_text(encoding="utf-8") for path in _python_files())

    for relative_path in expected_modules:
        assert _resolved(relative_path).exists(), f"{relative_path} is missing"
    for filename in retired_execution_modules:
        assert not (SRC_ROOT / "execution" / filename).exists(), f"execution/{filename} should be in cow_flashloan"
    for retired_import in retired_imports:
        assert retired_import not in python_sources


def test_robinhood_chain_dex_surface_wrappers_stay_thin():
    wrapper_paths = (
        "intent_trade/chains/robinhood/dex_surfaces.py",
        "intent_trade/chains/robinhood_testnet/dex_surfaces.py",
    )
    for relative_path in wrapper_paths:
        source = _resolved(relative_path).read_text(encoding="utf-8")
        assert "from dex.intent_trade.chains._dex_surface_flows import" in source
        assert "def _request_protocols" not in source
        assert "from market.binance_market.dex_surfaces import" not in source


def test_service_functionality_stays_out_of_web_routes():
    retired_web_modules = (
        "account_pool_state_service.py",
        "debt_pool_workflow.py",
        "liquidation_account_backfill.py",
        "liquidation_discovery_service.py",
        "liquidation_discovery_workflow.py",
        "liquidation_execution_service.py",
        "liquidation_scan_presenter.py",
        "liquidation_scan_summary_service.py",
        "liquidation_submission_service.py",
        "binance_market_service.py",
    )
    web_sources = "\n".join(path.read_text(encoding="utf-8") for path in (SRC_ROOT / "web").glob("*.py"))

    assert _resolved("account_pool/state.py").exists()
    assert _resolved("debt_pool/workflow.py").exists()
    assert _resolved("liquidation/execution_service.py").exists()
    for filename in retired_web_modules:
        assert not (SRC_ROOT / "web" / filename).exists(), f"web/{filename} should be a dedicated package"
    assert "from web.account_pool_state_service" not in web_sources
    assert "from web.debt_pool_workflow" not in web_sources
    assert "from web.liquidation_" not in web_sources


def test_web_route_registration_smoke():
    paths = {rule.rule for rule in control_panel_app.url_map.iter_rules()}

    assert "/api/status" in paths
    assert "/api/debt-pool/state" in paths
    assert "/api/debt-pool/decision" in paths
    assert "/api/account-pool/state" in paths
    assert "/api/account-scan/state" in paths
    assert "/api/market-observation/state" in paths
    assert "/api/execution/state" in paths
    assert "/binance-market" in paths
    assert "/dex-arbitrage" in paths
    assert "/api/binance-market/state" in paths
    assert "/api/binance-market/states" in paths
    assert "/api/binance-market/cow-config" in paths
    assert "/api/binance-market/cow-tokens" in paths
    assert "/api/binance-market/cow-tokens/refresh" in paths
    assert "/api/binance-market/cow-support" in paths
    assert "/api/binance-market/cow-quotes" in paths
    assert "/api/binance-market/cow-execution-attempts" in paths
    assert "/api/binance-market/cow-candidate-queue" in paths
    assert "/api/binance-velocity/candidates" in paths
    assert "/api/liquidation-health" in paths
    assert "/api/liquidation/borrow-pool/scan" in paths
    assert "/api/liquidation/account/<account>/static-call-and-save" in paths


def test_page_status_enums_do_not_include_cross_page_route_nodes():
    from bot.page_state import AccountScanStatus, DebtPoolStatus, ExecutionStatus, MarketObservationStatus

    route_nodes = {"ACCOUNT_SCAN_PAGE", "DEBT_POOL_PAGE", "EXECUTION_PAGE"}
    status_values = {
        *(status.value for status in DebtPoolStatus),
        *(status.value for status in AccountScanStatus),
        *(status.value for status in MarketObservationStatus),
        *(status.value for status in ExecutionStatus),
    }

    assert status_values.isdisjoint(route_nodes)


def test_cow_quotes_route_defines_quote_timeout_before_use():
    source = _resolved("web/data_routes/binance_market.py").read_text(encoding="utf-8")
    function_start = source.index("def binance_market_cow_quotes():")
    function_end = source.index("from web.data_routes.binance_market_admin import", function_start)
    body = source[function_start:function_end]

    assignment_at = body.index("quote_timeout_seconds = request_float_arg")
    use_at = body.index("quote_timeout_seconds=quote_timeout_seconds")

    assert assignment_at < use_at


def test_cow_quotes_route_applies_pause_guard_after_sdk_quote_verification():
    service = _resolved("web/data_routes/binance_market_quote_service.py").read_text(encoding="utf-8")

    verification_at = service.index("def build_cow_quote_verification_payload(")
    pause_at = service.index("if pause_guard.get(\"paused\"):")
    recording_at = service.index("payload[\"history_recording\"] = record_execution_attempts(")

    assert verification_at < pause_at < recording_at


def test_intent_mode_submission_script_does_not_use_profit_gates():
    source = (
        _resolved("cow_flashloan/node_adapter/scripts/submit-cow-flashloan-order.js")
    ).read_text(encoding="utf-8")

    assert "final_amount_below_minimum_bound" not in source
    assert "sell_budget_exceeds_principal" not in source
    assert "if (!profitBudgetMet || !sellBudgetPassed)" not in source
    assert "flashloanTokenScope" not in source
    assert "appDataIndicator" not in source
    assert "flashloanIntent" not in source
    assert "token_scope_only" not in source


def test_cow_intent_node_adapter_is_independent_from_contract_workspace():
    source = (
        _resolved("cow_flashloan/node_adapter/scripts/submit-cow-flashloan-order.js")
    ).read_text(encoding="utf-8")
    package_json = _resolved("cow_flashloan/node_adapter/package.json").read_text(encoding="utf-8")

    assert "contracts-dex" not in source
    assert "hardhat" not in package_json.lower()
    assert "deploy" not in package_json.lower()


def test_active_source_has_no_src_bot_path_references():
    offenders = []
    roots = [SRC_ROOT, SPLIT_SRC_ROOT]
    extensions = {".py", ".js", ".ts", ".html"}
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in extensions:
                continue
            if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            if "runtime" in path.parts or "chains" in path.parts:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
            if "src_bot" in text:
                offenders.append(str(path))
    assert offenders == []


def test_unified_direct_submission_has_no_reachable_legacy_controller_path():
    source = _resolved("intent_trade/direct_impl.py").read_text(encoding="utf-8")
    submit_start = source.index("def submit_direct_onchain_trade(")
    submit_body = source[submit_start:]
    legacy_start = source.index("def _submit_legacy_direct_onchain_trade(")
    legacy_body = source[legacy_start:submit_start]

    assert "_submit_legacy_direct_onchain_trade(" not in submit_body
    assert "UnifiedFlashLoanMevExecutor" in legacy_body
    assert 'status": "legacy_direct_path_disabled"' in legacy_body
