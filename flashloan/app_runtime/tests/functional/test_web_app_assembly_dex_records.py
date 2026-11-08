from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from web.control_panel import app
from web.data_routes.dex_surfaces import _overview_response_payload


TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "static"


EXPECTED_LIQUIDATION_CHAIN_ORDER = [
    "AVALANCHE Chain",
    "BNB Chain",
    "POLYGON Chain",
    "ETHEREUM Chain",
    "Robinhood Chain",
    "Robinhood Testnet",
    "CoW Rail",
]
EXPECTED_DEX_CHAIN_ORDER = [
    "AVALANCHE Chain",
    "BNB Chain",
    "POLYGON Chain",
    "ETHEREUM Chain",
    "Robinhood Chain",
    "Robinhood Testnet",
    "HYPERLIQUID Chain",
    "CoW Rail",
]
CANONICAL_BNB_AAVE = "liquidation-56-aave-v3"
CANONICAL_BNB_VENUS = "liquidation-56-venus-core"
CANONICAL_BNB_DEX = "dex-arbitrage-56-pancakeswap-v3"
CANONICAL_AVALANCHE_AAVE = "liquidation-43114-aave-v3"
CANONICAL_ROBINHOOD_BOT = "liquidation-4663-aave-v3"
CANONICAL_ROBINHOOD_MORPHO = "liquidation-4663-morpho-blue"
CANONICAL_ROBINHOOD_TESTNET_BOT = "liquidation-46630-morpho-blue"
CANONICAL_ROBINHOOD_DEX = "dex-arbitrage-4663-uniswap-v4"
CANONICAL_ROBINHOOD_DEX_V2 = "dex-arbitrage-4663-uniswap-v2"
CANONICAL_ROBINHOOD_DEX_V4 = "dex-arbitrage-4663-uniswap-v4"
CANONICAL_ROBINHOOD_DEX_MORPHO = "dex-arbitrage-4663-morpho-blue"
CANONICAL_ROBINHOOD_TESTNET_DEX = "dex-arbitrage-46630-uniswap-v4"
CANONICAL_HYPERLIQUID_DEX = "dex-arbitrage-hyperliquid-hyperliquid-native"


def test_dex_pool_candidates_full_refresh_reports_refresh_state(monkeypatch):
    from web.data_routes import dex_surfaces as dex_surface_routes
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes.shared,
        "_optional_request_chain_selection",
        lambda feature=None: {"network": "robinhood-testnet", "chain_id": 46630},
    )
    monkeypatch.setattr(
        dex_surface_routes,
        "_refresh_robinhood_testnet_v4_sources",
        lambda: {
            "ok": True,
            "source": "robinhood_testnet_v4_rebuild",
            "steps": [{"source": "v4_pool_index", "ok": True}],
            "errors": [],
            "message": "Robinhood Testnet V4 source files rebuilt",
        },
    )
    command_calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "candidate_edge_refresh_manager_payload",
        lambda **kwargs: command_calls.append(kwargs)
        or {
            "ok": True,
            "dataSource": "manager",
            "dryRun": False,
            "idempotentReplay": False,
            "candidateVersion": 9,
            "materializedChecksum": "sha256:candidate",
            "changedRows": 3,
            "consumersNeedRebuild": True,
        },
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "candidate_manager_page_payload",
        lambda **kwargs: {
            "ok": True,
            "mode": "dex_pool_candidates",
            "dataSource": "manager",
            "chain_id": kwargs["chain_id"],
            "chain_selection": kwargs["chain_selection"],
            "poolStaticTable": {
                "path": "manager://candidate-edge-pools",
                "exists": True,
                "network": "robinhood-testnet",
                "chainId": 46630,
            },
            "summary": {"pairTableCount": "2", "anchorGroupCount": "1"},
            "pairs": [{"symbolPair": "USDC:WETH"}],
            "anchorGroups": [],
        },
    )
    monkeypatch.setattr(
        dex_surface_routes,
        "dex_pool_candidates_payload",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("candidate refresh must not use the legacy builder")
        ),
    )

    response = app.test_client().post(
        "/api/dex-pools/candidates/refresh?market_id=robinhood-testnet-dex-paused&chain_id=46630&full=1",
        json={"full": True, "dryRun": False},
        headers={
            "X-Request-Id": "candidate-refresh-request",
            "Idempotency-Key": "candidate-refresh-idempotency",
        },
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["refresh_state"]["requested"] == "full"
    assert data["refresh_state"]["running"] is False
    assert data["refresh_state"]["ok"] is True
    assert data["refresh_state"]["source"] == "robinhood_testnet_v4_rebuild"
    assert "全量刷新完成" in data["refresh_state"]["message"]
    assert data["refresh_state"]["dryRun"] is False
    assert data["refresh_state"]["candidateVersion"] == 9
    assert data["summary"]["pairTableCount"] == "2"
    assert command_calls == [
        {
            "chain_id": 46630,
            "dry_run": False,
            "request_id": "candidate-refresh-request",
            "idempotency_key": "candidate-refresh-idempotency",
        }
    ]


def test_dex_pool_candidates_refresh_requires_explicit_write_headers(monkeypatch):
    from web.data_routes import dex_surfaces as dex_surface_routes
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes,
        "selected_chain_id_from_request",
        lambda: ({"network": "robinhood", "chain_id": 4663}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "candidate_edge_refresh_manager_payload",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid write headers must fail before RuntimeCore")
        ),
    )

    client = app.test_client()
    missing_request_id = client.post(
        "/api/dex-pools/candidates/refresh?chain_id=4663",
        json={"dryRun": False},
    )
    assert missing_request_id.status_code == 400
    assert missing_request_id.get_json()["error"] == "request_id_required"

    missing_idempotency = client.post(
        "/api/dex-pools/candidates/refresh?chain_id=4663",
        json={"dryRun": False},
        headers={"X-Request-Id": "candidate-request"},
    )
    assert missing_idempotency.status_code == 400
    assert missing_idempotency.get_json()["error"] == "idempotency_key_required"


def test_dex_pool_candidates_dry_run_skips_source_refresh(monkeypatch):
    from web.data_routes import dex_surfaces as dex_surface_routes
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes.shared,
        "_optional_request_chain_selection",
        lambda feature=None: {"network": "robinhood-testnet", "chain_id": 46630},
    )
    monkeypatch.setattr(
        dex_surface_routes,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes,
        "_refresh_robinhood_testnet_v4_sources",
        lambda: (_ for _ in ()).throw(
            AssertionError("dry-run must not refresh source files")
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "candidate_edge_refresh_manager_payload",
        lambda **kwargs: {
            "ok": True,
            "dataSource": "manager",
            "dryRun": True,
            "idempotentReplay": False,
            "candidateVersion": 4,
            "materializedChecksum": "sha256:dry",
            "changedRows": 2,
            "consumersNeedRebuild": True,
        },
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "candidate_manager_page_payload",
        lambda **kwargs: {
            "ok": True,
            "mode": "dex_pool_candidates",
            "dataSource": "manager",
            "chain_id": kwargs["chain_id"],
            "chain_selection": kwargs["chain_selection"],
            "summary": {"pairTableCount": "0"},
            "anchorGroups": [],
            "pairs": [],
        },
    )
    monkeypatch.setattr(
        dex_surface_routes,
        "_attach_dex_strategy",
        lambda payload: payload,
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "mask_dex_surface_placeholder_symbols",
        lambda payload: payload,
    )

    response = app.test_client().post(
        "/api/dex-pools/candidates/refresh?chain_id=46630&full=1",
        json={"full": True, "dryRun": True},
        headers={
            "X-Request-Id": "candidate-dry-request",
            "Idempotency-Key": "candidate-dry-idempotency",
        },
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["refresh_state"]["requested"] == "full"
    assert data["refresh_state"]["dryRun"] is True
    assert data["refresh_state"]["candidateVersion"] == 4
    assert data["refresh_state"]["message"] == "候选池管理器预演完成"


def test_dex_submission_clear_api_delegates_to_manager(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": CANONICAL_ROBINHOOD_DEX}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be written")),
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_clear_manager_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "dataSource": "manager",
            "cleared": 1,
            "remaining": 0,
            "clearedDiagnostics": 0,
            "remainingDiagnostics": 0,
            "dryRun": False,
            "idempotentReplay": False,
            "managerResult": {"clearedAttempts": 1},
        },
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission", "dryRun": False},
        headers={
            "X-Request-Id": "functional-request-1",
            "Idempotency-Key": "functional-clear-1",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["cleared"] == 1
    assert payload["remaining"] == 0
    assert calls[0]["chain_id"] == 4663
    assert calls[0]["dry_run"] is False
    assert calls[0]["idempotency_key"] == "functional-clear-1"


def test_dex_submissions_overview_page_has_clear_button():
    template = (TEMPLATE_DIR / "dex_submissions.html").read_text(encoding="utf-8")

    assert 'id="clearBtn"' in template
    assert '"/api/dex/submissions/clear"' in template
    assert '{recordKind: "submission", dryRun: false}' in template
    assert '"X-Request-Id": requestId' in template
    assert "机会复盘记录不会被删除" in template


def test_dex_submission_clear_api_reports_manager_terminal_counts(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": CANONICAL_ROBINHOOD_DEX}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_clear_manager_payload",
        lambda **kwargs: {
            "ok": True,
            "dataSource": "manager",
            "cleared": 2,
            "remaining": 0,
            "clearedDiagnostics": 3,
            "remainingDiagnostics": 0,
            "dryRun": False,
            "idempotentReplay": False,
            "managerResult": {"clearedAttempts": 2, "clearedDiagnostics": 3},
        },
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission", "dryRun": False},
        headers={
            "X-Request-Id": "functional-request-2",
            "Idempotency-Key": "functional-clear-2",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["cleared"] == 2
    assert payload["remaining"] == 0
    assert payload["clearedDiagnostics"] == 3


def test_dex_submission_clear_passes_selected_chain_to_manager(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": CANONICAL_ROBINHOOD_DEX}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_clear_manager_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "dataSource": "manager",
            "cleared": 1,
            "remaining": 0,
            "clearedDiagnostics": 0,
            "remainingDiagnostics": 0,
            "dryRun": False,
            "idempotentReplay": False,
            "managerResult": {},
        },
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission", "dryRun": False},
        headers={
            "X-Request-Id": "functional-request-3",
            "Idempotency-Key": "functional-clear-3",
        },
    )

    assert response.status_code == 200
    assert calls[0]["chain_id"] == 4663
    assert calls[0]["dry_run"] is False


def test_dex_submission_clear_requires_record_kind():
    response = app.test_client().post(
        "/api/dex/submissions/clear?market_id=robinhood-dex-shadow&chain_id=4663"
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "record_kind_required"


def test_dex_submission_clear_requires_explicit_request_id(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": CANONICAL_ROBINHOOD_DEX}, 4663),
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission", "dryRun": False},
        headers={"Idempotency-Key": "functional-clear-missing-request"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "request_id_required"


def test_dex_submission_clear_requires_explicit_idempotency_key(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": CANONICAL_ROBINHOOD_DEX}, 4663),
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission", "dryRun": False},
        headers={"X-Request-Id": "functional-clear-missing-idempotency"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "idempotency_key_required"


def test_dex_submission_clear_reports_runtime_core_unavailable_without_fallback(
    monkeypatch,
):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": CANONICAL_ROBINHOOD_DEX}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_clear_manager_payload",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "code": "RUNTIME_CORE_UNAVAILABLE",
            "upstreamStatus": 503,
        },
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission", "dryRun": False},
        headers={
            "X-Request-Id": "functional-request-unavailable",
            "Idempotency-Key": "functional-clear-unavailable",
        },
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"] == "manager_write_failed"
    assert payload["dataSource"] == "manager"
    assert payload["managerResult"]["code"] == "RUNTIME_CORE_UNAVAILABLE"


def test_binance_cow_execution_attempt_history_keeps_multiple_submission_txs(tmp_path, monkeypatch):
    from core.db.storage_cow_execution import record_cow_execution_attempts
    from web.data_routes import binance_market_admin

    database_path = str(tmp_path / "local_store.sqlite3")
    observed_at = datetime.now(timezone.utc).isoformat()
    record_cow_execution_attempts(
        database_path,
        [
            {
                "observed_at": observed_at,
                "network": "robinhood",
                "chain_id": 4663,
                "pair": "TZ -> TA -> TB -> TZ",
                "state": "submitted_success",
                "execution_phase": "order_submission",
                "checks_passed": True,
                "can_submit_order": True,
                "tx_hash": "0x" + "a" * 64,
            },
            {
                "observed_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
                "network": "robinhood",
                "chain_id": 4663,
                "pair": "TZ -> TA -> TB -> TZ",
                "state": "submitted_success",
                "execution_phase": "order_submission",
                "checks_passed": True,
                "can_submit_order": True,
                "tx_hash": "0x" + "b" * 64,
            },
        ],
        dedupe_market_candidates=False,
    )
    monkeypatch.setattr(binance_market_admin, "database_url", lambda: database_path)

    data = app.test_client().get("/api/binance-market/cow-execution-attempts?limit=10").get_json()
    attempts = data["attempts"]

    assert {row["tx_hash"] for row in attempts} == {"0x" + "a" * 64, "0x" + "b" * 64}
    assert data["groups"]["execution_success"]["count"] == 2


def test_dex_submission_review_uses_manager_verified_row_and_diagnostics(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    pair = "USDG -> WETH -> HOBBS -> WETH -> USDG"
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "review_manager_page_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": "dex_submission_review_records",
            "recordKind": "review",
            "summary": {"attemptCount": 1, "pageAttemptCount": 1},
            "categoryCounts": {"review_failed": 1},
            "attempts": [
                {
                    "pair": pair,
                    "state": "review_failed",
                    "review_category": "review_failed",
                    "diagnostics": [
                        {
                            "message": "不能使用 Uniswap Quoter 证明该池输出",
                            "diagnosticCode": "QUOTE_GAP",
                        }
                    ],
                    "review_summary": {
                        "onchain_evidence": {
                            "status": "not_live_verified",
                            "blockers": ["不能使用 Uniswap Quoter 证明该池输出"],
                        }
                    },
                }
            ],
            "pagination": {"page": 1, "total_pages": 1},
            "chain_selection": kwargs["chain_selection"],
            "managerStatus": {},
        },
    )
    client = app.test_client()

    payload = client.get("/api/dex/submissions/review?market_id=robinhood-dex-shadow&chain_id=4663&limit=20").get_json()
    attempts = [row for row in payload["attempts"] if row.get("pair") == pair]

    assert len(attempts) == 1
    assert payload["recordKind"] == "review"
    assert payload["dataSource"] == "manager"
    evidence = attempts[0]["review_summary"]["onchain_evidence"]
    assert evidence["status"] == "not_live_verified"
    assert evidence["blockers"] == ["不能使用 Uniswap Quoter 证明该池输出"]
    assert calls[0]["chain_id"] == 4663


def test_dex_page_mounts_shared_market_selector():
    body = app.test_client().get("/dex-arbitrage").get_data(as_text=True)

    assert 'data-dex-surface-template="quotes"' in body
    assert 'data-chain-selector-component="true"' in body
    assert "data-surface-link" in body


def test_dex_submissions_page_exposes_route_framework_panel(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_overview_manager_payload",
        lambda **kwargs: {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": kwargs["mode"],
            "summary": {"attemptCount": 0, "successCount": 0, "failedCount": 0},
            "categoryCounts": {},
            "attempts": [],
            "pagination": {"page": 1, "page_size": 20, "total": 0, "total_pages": 1},
            "chain_selection": kwargs["chain_selection"],
            "routeFramework": {
                "routeFrameworkSource": "manager_broadcast",
                "topRouteText": "USDG -> WETH -> USDG",
            },
            "loopStateSummary": {"source": "manager"},
            "managerStatus": {},
        },
    )
    client = app.test_client()

    body = client.get("/dex/submissions?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4").get_data(as_text=True)
    payload = client.get(
        "/api/dex/submissions?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4&limit=5"
    ).get_json()
    template = (TEMPLATE_DIR / "dex_submissions.html").read_text(encoding="utf-8")

    assert 'id="routeFrameworkPanel"' in body
    assert payload["dataSource"] == "manager"
    assert payload["routeFramework"]["routeFrameworkSource"] == "manager_broadcast"
    assert payload["routeFramework"]["topRouteText"]
    assert 'const submissionEndpoint = "/api/dex/submissions";' in template
    assert 'href="/dex/submissions/review-success"' in template
    assert 'href="/dex/submissions/execution-failed"' in template
    assert 'href="/dex/submissions/success"' not in template
    assert 'href="/dex/submissions/failed"' not in template
    assert 'href="/dex/submissions/review"' not in template


def test_legacy_submission_pages_are_disabled():
    client = app.test_client()
    for path in (
        "/dex/submissions/success",
        "/dex/submissions/failed",
        "/dex/submissions/failed/broadcast",
        "/dex/submissions/review",
        "/dex/submissions/review/confirmed_no_profit",
    ):
        assert client.get(path).status_code == 404


def test_dex_submission_success_api_returns_only_manager_tx_records(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_execution_manager_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": kwargs["mode"],
            "recordKind": "submission",
            "summary": {
                "attemptCount": 1,
                "successCount": 1,
                "failedCount": 0,
            },
            "attempts": [
                {
                    "id": "attempt-success-1",
                    "tx_hash": "0x" + "b" * 64,
                    "status": "success",
                }
            ],
            "pagination": {"page": 1, "total_pages": 1},
            "chain_selection": kwargs["chain_selection"],
            "managerStatus": {},
        },
    )

    payload = app.test_client().get(
        "/api/dex/submissions/success?market_id=robinhood-dex-shadow&chain_id=4663&limit=20"
    ).get_json()

    assert payload["summary"]["attemptCount"] == 1
    assert payload["summary"]["successCount"] == 1
    attempts = payload["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["tx_hash"] == "0x" + "b" * 64
    assert calls[0]["status"] == "success"


def test_dex_submission_review_includes_manager_ready_not_submitted_rows(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "review_manager_page_payload",
        lambda **kwargs: {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": kwargs["mode"],
            "recordKind": "review",
            "summary": {
                "attemptCount": 1,
                "pageAttemptCount": 1,
                "readyNotSubmittedCount": 1,
            },
            "categoryCounts": {"ready_not_submitted": 1},
            "attempts": [
                {
                    "pair": "USDG -> WETH -> USDG",
                    "state": "ready_not_submitted",
                    "review_category": "ready_not_submitted",
                    "signedPnl": {
                        "amount": 2.5,
                        "currency": "USDG",
                        "source": "runArbitrage",
                    },
                }
            ],
            "pagination": {"page": 1, "total_pages": 1},
            "chain_selection": kwargs["chain_selection"],
            "managerStatus": {},
        },
    )
    client = app.test_client()

    review = client.get(
        "/api/dex/submissions/review?market_id=robinhood-dex-shadow&chain_id=4663&limit=20"
    ).get_json()

    assert review["recordKind"] == "review"
    assert review["dataSource"] == "manager"
    assert review["summary"]["attemptCount"] == 1
    assert review["attempts"][0]["state"] == "ready_not_submitted"
    assert review["categoryCounts"]["ready_not_submitted"] == 1


def test_four_independent_submission_record_apis_are_separate(monkeypatch):
    from web.data_routes import dex_surface_routes_ops

    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    review_calls = []

    def review_payload(**kwargs):
        review_calls.append(kwargs)
        if kwargs["outcome"] == "success":
            return {
                "ok": True,
                "source": "manager",
                "dataSource": "manager",
                "mode": kwargs["mode"],
                "recordKind": "review",
                "summary": {"attemptCount": 1},
                "categoryCounts": {"ready_not_submitted": 1},
                "attempts": [{"state": "ready_not_submitted"}],
                "pagination": {"page": 1, "total_pages": 1},
                "chain_selection": kwargs["chain_selection"],
                "managerStatus": {},
            }
        return {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": kwargs["mode"],
            "recordKind": "review",
            "summary": {"attemptCount": 1},
            "categoryCounts": {"review_failed": 1},
            "attempts": [{"state": "quote_failed"}],
            "pagination": {"page": 1, "total_pages": 1},
            "chain_selection": kwargs["chain_selection"],
            "managerStatus": {},
        }

    execution_calls = []

    def execution_payload(**kwargs):
        execution_calls.append(kwargs)
        if kwargs["status"] == "success":
            attempts = [{"tx_hash": "0x" + "e" * 64}]
        else:
            attempts = [{"state": "submission_failed"}]
        return {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": kwargs["mode"],
            "recordKind": "submission",
            "summary": {"attemptCount": 1},
            "attempts": attempts,
            "pagination": {"page": 1, "total_pages": 1},
            "chain_selection": kwargs["chain_selection"],
            "managerStatus": {},
        }

    monkeypatch.setattr(
        dex_surface_routes_ops,
        "review_manager_page_payload",
        review_payload,
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_execution_manager_payload",
        execution_payload,
    )
    client = app.test_client()
    base_query = "?market_id=robinhood-dex-shadow&chain_id=4663&limit=20"

    review_success = client.get(f"/api/dex/submissions/review-success{base_query}").get_json()
    review_failed = client.get(f"/api/dex/submissions/review-failed{base_query}").get_json()
    execution_success = client.get(f"/api/dex/submissions/execution-success{base_query}").get_json()
    execution_failed = client.get(f"/api/dex/submissions/execution-failed{base_query}").get_json()

    assert review_success["mode"] == "dex_submission_review_success_records"
    assert review_success["recordKind"] == "review"
    assert review_success["dataSource"] == "manager"
    assert review_success["summary"]["attemptCount"] == 1
    assert review_success["attempts"][0]["state"] == "ready_not_submitted"

    assert review_failed["mode"] == "dex_submission_review_failed_records"
    assert review_failed["recordKind"] == "review"
    assert review_failed["dataSource"] == "manager"
    assert review_failed["summary"]["attemptCount"] == 1
    assert review_failed["attempts"][0]["state"] == "quote_failed"

    assert execution_success["mode"] == "dex_submission_execution_success_records"
    assert execution_success["recordKind"] == "submission"
    assert execution_success["dataSource"] == "manager"
    assert execution_success["summary"]["attemptCount"] == 1
    assert execution_success["attempts"][0]["tx_hash"] == "0x" + "e" * 64

    assert execution_failed["mode"] == "dex_submission_execution_failed_records"
    assert execution_failed["recordKind"] == "submission"
    assert execution_failed["dataSource"] == "manager"
    assert execution_failed["summary"]["attemptCount"] == 1
    assert execution_failed["attempts"][0]["state"] == "submission_failed"
    assert [call["outcome"] for call in review_calls] == ["success", "failed"]
    assert [call["status"] for call in execution_calls] == ["success", "failed"]


def test_four_independent_submission_record_pages_use_own_endpoints():
    client = app.test_client()
    cases = [
        ("/dex/submissions/review-success", "submissions_review_success", "dex_review_success.html", "/api/dex/submissions/review-success"),
        ("/dex/submissions/review-failed", "submissions_review_failed", "dex_review_failed.html", "/api/dex/submissions/review-failed"),
        ("/dex/submissions/execution-success", "submissions_execution_success", "dex_execution_success.html", "/api/dex/submissions/execution-success"),
        ("/dex/submissions/execution-failed", "submissions_execution_failed", "dex_execution_failed.html", "/api/dex/submissions/execution-failed"),
    ]
    for path, surface, filename, endpoint in cases:
        body = client.get(path).get_data(as_text=True)
        template = (TEMPLATE_DIR / filename).read_text(encoding="utf-8")
        shared_js = "\n".join(
            (STATIC_DIR / name).read_text(encoding="utf-8")
            for name in (
                "dex_submission_record_model.js",
                "dex_submission_record_render.js",
                "dex_submission_records.js",
            )
        )
        config_js = (STATIC_DIR / "dex_submission_records_config.js").read_text(encoding="utf-8")
        assert f'data-dex-surface-template="{surface}"' in body
        assert "data-surface-link" in body
        assert "提交记录总览" in template
        assert "复核成功记录" in template
        assert "复核失败记录" in template
        assert "链上提交执行成功记录" in template
        assert "链上提交执行失败记录" in template
        assert "链上执行成功" not in template
        assert "链上执行失败" not in template
        assert 'href="/dex/submissions/success"' not in template
        assert 'href="/dex/submissions/failed"' not in template
        assert 'href="/dex/submissions/review"' not in template
        assert "/static/dex_submission_records.js" in template
        assert "/static/dex_submission_records_config.js" in template
        assert "/static/dex_submission_record_model.js" in template
        assert "/static/dex_submission_record_render.js" in template
        assert template.index("/static/dex_submission_records_config.js") < template.index("/static/dex_submission_records.js")
        assert "复制记录" in shared_js
        assert "data-copy-record" in shared_js
        assert "copyRecordText" in shared_js
        assert "selectedProfitReviewText" in shared_js
        assert "管理器参考（非 eth_call）" in shared_js
        assert "原生合约预览（链上复核）" in shared_js
        assert endpoint in config_js
        assert "id=\"rows\"" in body
        assert "/api/dex/opportunity-scanner/snapshot" not in template
        assert "/api/dex/quote-routes" not in template


def test_dex_submissions_overview_displays_selected_profit_comparison():
    template = (TEMPLATE_DIR / "dex_submissions.html").read_text(encoding="utf-8")
    shared_js = "\n".join(
        (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in (
            "dex_submission_record_model.js",
            "dex_submission_record_render.js",
            "dex_submission_records.js",
        )
    )

    assert "链上复核值" in template
    assert "参考执行估算" in template
    assert "链上复核对比" in template
    assert "selectedProfitReviewText" in template
    assert "原生合约预览（链上复核）" in template
    assert "管理器参考（非 eth_call）" in template
    assert "无链上 signed 净值；参考执行估算=" in shared_js


def test_dex_closed_loop_broadcast_failure_audit_api_is_manager_only(
    monkeypatch,
):
    from web.data_routes import dex_surface_routes_ops

    calls = []
    state = {"fail": False}
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    def fake_manager_payload(**kwargs):
        calls.append(kwargs)
        if state["fail"]:
            return {
                "ok": False,
                "code": "RUNTIME_CORE_UNAVAILABLE",
                "detail": "runtime core unavailable",
                "upstreamStatus": 503,
            }
        return {
                "ok": True,
                "mode": "dex_closed_loop_broadcast_failures",
                "dataSource": "manager",
                "available": True,
                "recordCount": 1,
                "records": [
                    {
                        "groupKey": "aaa|bbb|ccc",
                        "routePath": "AAA -> BBB -> CCC -> AAA",
                        "broadcastError": "eth_sendRawTransaction RPC request failed",
                        "selectedProfitRaw": "0x0493e0",
                    }
                ],
            }

    monkeypatch.setattr(
        dex_surface_routes_ops,
        "broadcast_failure_manager_payload",
        fake_manager_payload,
    )

    response = app.test_client().get("/api/dex/closed-loop-broadcast-failures?limit=10")
    data = response.get_json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["dataSource"] == "manager"
    assert data["mode"] == "dex_closed_loop_broadcast_failures"
    assert data["available"] is True
    assert data["recordCount"] == 1
    assert data["records"][0]["broadcastError"] == "eth_sendRawTransaction RPC request failed"
    assert data["records"][0]["selectedProfitRaw"] == "0x0493e0"
    assert calls[0]["chain_id"] == 4663
    assert calls[0]["limit"] == 10

    zero = app.test_client().get("/api/dex/closed-loop-broadcast-failures?limit=0")
    assert zero.status_code == 200
    assert calls[-1]["limit"] == 0

    state["fail"] = True
    failed = app.test_client().get("/api/dex/closed-loop-broadcast-failures?limit=10")
    assert failed.status_code == 503
    failed_payload = failed.get_json()
    assert failed_payload["error"] == "manager_read_failed"
    assert failed_payload["dataSource"] == "manager"
    assert failed_payload["managerDetail"]["code"] == "RUNTIME_CORE_UNAVAILABLE"
