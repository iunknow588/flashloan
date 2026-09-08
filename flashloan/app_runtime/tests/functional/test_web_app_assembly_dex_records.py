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
CANONICAL_ROBINHOOD_DEX = "dex-arbitrage-4663-uniswap-v3"
CANONICAL_ROBINHOOD_DEX_V2 = "dex-arbitrage-4663-uniswap-v2"
CANONICAL_ROBINHOOD_DEX_V4 = "dex-arbitrage-4663-uniswap-v4"
CANONICAL_ROBINHOOD_DEX_MORPHO = "dex-arbitrage-4663-morpho-blue"
CANONICAL_ROBINHOOD_TESTNET_DEX = "dex-arbitrage-46630-uniswap-v4"
CANONICAL_HYPERLIQUID_DEX = "dex-arbitrage-hyperliquid-hyperliquid-native"


def test_dex_pool_candidates_full_refresh_reports_refresh_state(monkeypatch):
    from web.data_routes import dex_surfaces as dex_surface_routes

    monkeypatch.setattr(
        dex_surface_routes.shared,
        "_optional_request_chain_selection",
        lambda: {"network": "robinhood-testnet", "chain_id": 46630},
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
    monkeypatch.setattr(
        dex_surface_routes,
        "dex_pool_candidates_payload",
        lambda chain_id=None, protocols=None, refresh=False, force_full=False: {
            "mode": "dex_pool_candidates",
            "poolStaticTable": {"path": "cached.json", "exists": True, "network": "robinhood-testnet", "chainId": 46630},
            "summary": {"pairTableCount": "2", "anchorGroupCount": "1"},
            "pairs": [{"symbolPair": "USDC:WETH"}],
            "anchorGroups": [{"anchorSymbol": "USDC", "ycPoolCount": "1", "zcPoolCount": "0", "ycTokenCount": "1", "zcTokenCount": "0", "candidatePoolCount": "1"}],
        },
    )

    response = app.test_client().get(
        "/api/dex-pools/candidates/refresh?market_id=robinhood-testnet-dex-paused&chain_id=46630&full=1"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["refresh_state"]["requested"] == "full"
    assert data["refresh_state"]["running"] is False
    assert data["refresh_state"]["ok"] is True
    assert data["refresh_state"]["source"] == "robinhood_testnet_v4_rebuild"
    assert "全量刷新完成" in data["refresh_state"]["message"]
    assert data["summary"]["pairTableCount"] == "2"


def test_dex_submission_clear_api_removes_current_chain_records(tmp_path, monkeypatch):
    from core.db.storage_cow_execution import append_cow_execution_attempts_jsonl
    from web import control_panel
    from web.data_routes import dex_surfaces as dex_surface_routes

    path = tmp_path / "cow_execution_attempts.jsonl"
    append_cow_execution_attempts_jsonl(
        path,
        [
            {
                "observed_at": "2026-08-24T00:00:00+00:00",
                "network": "robinhood",
                "pair": "USDG -> WETH -> USDG",
                "state": "quote_required",
                "execution_phase": "market_candidate",
                "checks_passed": False,
                "can_submit_order": False,
            }
        ],
    )
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: None)
    monkeypatch.setattr(dex_surface_routes.shared, "COW_EXECUTION_ATTEMPT_LOG_ROOT", tmp_path)
    client = app.test_client()

    before = client.get("/api/dex/submissions?market_id=robinhood-dex-shadow&chain_id=4663&limit=20").get_json()
    cleared = client.post("/api/dex/submissions/clear?market_id=robinhood-dex-shadow&chain_id=4663").get_json()
    after = client.get("/api/dex/submissions?market_id=robinhood-dex-shadow&chain_id=4663&limit=20").get_json()

    assert before["summary"]["attemptCount"] == 1
    assert cleared["ok"] is True
    assert cleared["cleared"] == 1
    assert cleared["networks"] == ["robinhood"]
    assert after["summary"]["attemptCount"] == 0


def test_dex_submission_review_prefers_verified_route_record_over_deferred(tmp_path, monkeypatch):
    from core.db.storage_cow_execution import append_cow_execution_attempts_jsonl
    from web import control_panel
    from web.data_routes import dex_surfaces as dex_surface_routes

    path = tmp_path / "cow_execution_attempts.jsonl"
    pair = "USDG -> WETH -> HOBBS -> WETH -> USDG"
    append_cow_execution_attempts_jsonl(
        path,
        [
            {
                "observed_at": "2026-08-24T12:54:19+00:00",
                "network": "robinhood",
                "pair": pair,
                "state": "quote_required",
                "execution_phase": "market_candidate",
                "checks_passed": False,
                "can_submit_order": False,
                "quote": {
                    "live_onchain_evidence": {
                        "status": "not_live_verified",
                        "executionTrust": "static_reference_only",
                        "trustedForExecution": False,
                        "blockers": ["绗?4 璺充负 sushiswap V3 姹狅紝涓嶈兘鐢?Uniswap Quoter 璇佹槑璇ユ睜杈撳嚭銆?"],
                    }
                },
            },
            {
                "observed_at": "2026-08-24T12:54:30+00:00",
                "network": "robinhood",
                "pair": pair,
                "state": "quote_required",
                "execution_phase": "market_candidate",
                "checks_passed": False,
                "can_submit_order": False,
                "quote": {
                    "live_onchain_evidence": {
                        "status": "live_evidence_deferred",
                        "executionTrust": "static_reference_only",
                        "trustedForExecution": False,
                        "blockers": ["鏈疆鎵弿杈惧埌閾句笂澶嶆牳涓婇檺锛屽凡寤跺悗鍒颁笅涓€杞┖闂叉壂鎻忋€?"],
                    }
                },
            },
        ],
    )
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: None)
    monkeypatch.setattr(dex_surface_routes.shared, "COW_EXECUTION_ATTEMPT_LOG_ROOT", tmp_path)
    client = app.test_client()

    payload = client.get("/api/dex/submissions?market_id=robinhood-dex-shadow&chain_id=4663&limit=20").get_json()
    attempts = [row for row in payload["attempts"] if row.get("pair") == pair]

    assert len(attempts) == 1
    evidence = attempts[0]["review_summary"]["onchain_evidence"]
    assert evidence["status"] == "not_live_verified"
    assert "涓嶈兘鐢?Uniswap Quoter 璇佹槑璇ユ睜杈撳嚭" in evidence["blockers"][0]


def test_dex_page_mounts_shared_market_selector():
    body = app.test_client().get("/dex-arbitrage").get_data(as_text=True)

    assert 'data-dex-surface-template="quotes"' in body
    assert 'data-chain-selector-component="true"' in body
    assert "data-surface-link" in body


def test_dex_submissions_page_exposes_route_framework_panel():
    client = app.test_client()

    body = client.get("/dex/submissions?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4").get_data(as_text=True)
    payload = client.get(
        "/api/dex/submissions?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4&limit=5"
    ).get_json()

    assert 'id="routeFrameworkPanel"' in body
    assert payload["routeFramework"]["routeFrameworkSource"] in {"market_observation", "candidate_pool_fallback"}
    assert payload["routeFramework"]["topRouteText"]


