import json

from web import control_panel_bnb_venus as bnb
from web.control_panel import app


CANONICAL_BNB_VENUS = "liquidation-56-venus-core"
LEGACY_BNB_VENUS = "bnb-venus-core"


def _write_latest(tmp_path):
    payload = {
        "generated_at": "2026-08-16T11:05:43Z",
        "block_number": 123,
        "mode": "observation_only",
        "reports": [
            {
                "account": "0x0000000000000000000000000000000000000001",
                "summary": {
                    "status": "healthy",
                    "total_debt_value_usd": 100,
                    "total_collateral_value_usd": 120,
                },
                "risk": {"liquidity_raw": 20 * 10**18, "shortfall_raw": 0, "positions": []},
                "liquidation_candidates": [],
            },
            {
                "account": "0x0000000000000000000000000000000000000002",
                "summary": {
                    "status": "liquidatable",
                    "total_debt_value_usd": 100,
                    "total_collateral_value_usd": 90,
                },
                "risk": {"liquidity_raw": 0, "shortfall_raw": 10 * 10**18, "positions": []},
                "liquidation_candidates": [],
            },
            {
                "account": "0x0000000000000000000000000000000000000003",
                "summary": {"status": "warning", "health_factor": 1.05, "total_debt_value_usd": 0},
                "risk": {"liquidity_raw": 0, "shortfall_raw": 0, "positions": []},
                "liquidation_candidates": [],
            },
        ],
    }
    (tmp_path / "venus_liquidation_latest.json").write_text(json.dumps(payload), encoding="utf-8")
    current = {
        "generated_at": payload["generated_at"],
        "last_block": payload["block_number"],
        "summary": {"account_count": 42, "scanned_account_count": 3, "last_round_scanned_count": 3},
        "reports": payload["reports"],
    }
    (tmp_path / "venus_liquidation_current.json").write_text(json.dumps(current), encoding="utf-8")
    registry = {
        "account_count": 42,
        "accounts": [f"0x{i:040x}" for i in range(42)],
        "scan_complete": True,
        "discovery_mode": "borrow_events_account_only",
        "event_count": 19,
    }
    (tmp_path / "venus_borrower_registry.json").write_text(json.dumps(registry), encoding="utf-8")


def _write_latest_with_core_alert(tmp_path):
    _write_latest(tmp_path)
    payload = json.loads((tmp_path / "venus_liquidation_latest.json").read_text(encoding="utf-8"))
    payload["reports"].append(
        {
            "account": "0x0000000000000000000000000000000000000004",
            "summary": {"status": "healthy", "health_factor": 1.006, "total_debt_value_usd": 10},
            "risk": {"liquidity_raw": 0, "shortfall_raw": 0, "positions": []},
            "liquidation_candidates": [],
        }
    )
    current = json.loads((tmp_path / "venus_liquidation_current.json").read_text(encoding="utf-8"))
    current["reports"] = payload["reports"]
    current["summary"]["scanned_account_count"] = 4
    (tmp_path / "venus_liquidation_latest.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "venus_liquidation_current.json").write_text(json.dumps(current), encoding="utf-8")


def test_bnb_venus_payload_exposes_health_and_separate_account_groups(monkeypatch, tmp_path):
    _write_latest(tmp_path)
    monkeypatch.setattr(bnb, "bnb_venus_output_dir", lambda: tmp_path)

    payload = bnb.bnb_venus_borrow_pool_payload()

    assert payload["summary"]["market_id"] == CANONICAL_BNB_VENUS
    assert payload["summary"]["legacy_market_id"] == LEGACY_BNB_VENUS
    assert payload["summary"]["chain_id"] == 56
    assert payload["summary"]["ordinary_account_count"] == 40
    assert payload["summary"]["high_frequency_account_count"] == 1
    assert payload["summary"]["core_account_count"] == 1
    assert payload["summary"]["registry_account_count"] == 42
    assert payload["summary"]["health_report_account_count"] == 3
    assert payload["summary"]["current_pool_account_count"] == 42
    assert payload["summary"]["scanned_account_count"] == 3
    assert payload["summary"]["pending_account_count"] == 39
    assert payload["summary"]["scan_coverage_rate"] == 3 / 42
    assert payload["summary"]["pool_tier_counts"]["core"] == 1
    assert payload["summary"]["pool_tier_counts"]["high_frequency"] == 1
    assert payload["summary"]["pool_tier_counts"]["borrow_health"] == 40
    assert payload["rows"][0]["health_factor"] == 1.2
    assert payload["rows"][0]["pool_tier"] == "borrow_health"
    assert payload["tiers"]["high_frequency_rows"][0]["health_display"] == "1.050"
    assert payload["tiers"]["high_frequency_rows"][0]["pool_tier"] == "high_frequency"
    assert payload["tiers"]["core_opportunity_rows"][0]["health_factor"] == 0.9
    assert payload["tiers"]["core_opportunity_rows"][0]["pool_tier"] == "core"
    assert payload["rows"][-1]["health_display"] == "待扫描"


def test_bnb_venus_core_bucket_uses_avalanche_threshold(monkeypatch, tmp_path):
    _write_latest_with_core_alert(tmp_path)
    monkeypatch.setattr(bnb, "bnb_venus_output_dir", lambda: tmp_path)

    payload = bnb.bnb_venus_borrow_pool_payload()

    assert payload["summary"]["core_account_count"] == 2
    assert payload["summary"]["high_frequency_account_count"] == 1
    assert any(row["health_factor"] == 1.006 for row in payload["tiers"]["core_opportunity_rows"])


def test_bnb_venus_api_uses_file_observation_scope(monkeypatch, tmp_path):
    _write_latest(tmp_path)
    monkeypatch.setattr(bnb, "bnb_venus_output_dir", lambda: tmp_path)

    response = app.test_client().get(
        "/api/liquidation/borrow-pool?market_id=bnb-venus-core&chain_id=56&network=bnb&protocol=venus_core&page_size=20"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["summary"]["scan_response_source"] == "file_observation"
    assert data["summary"]["execution_enabled"] is False
    assert data["summary"]["registry_account_count"] == 42
    assert data["summary"]["current_pool_account_count"] == 42
    assert data["summary"]["pending_account_count"] == 39
    assert data["tiers"]["core_opportunity_count"] == 1


def test_liquidation_market_api_keeps_explicit_bnb_scope(monkeypatch):
    monkeypatch.delenv("LIQUIDATION_MARKET_ID", raising=False)
    monkeypatch.delenv("LIQUIDATION_CHAIN_ID", raising=False)
    monkeypatch.delenv("CHAIN_ID", raising=False)

    response = app.test_client().get("/api/liquidation/market?market_id=bnb-venus-core&chain_id=56&network=bnb&protocol=venus_core")
    data = response.get_json()

    assert response.status_code == 200
    assert data["active"]["market_id"] == CANONICAL_BNB_VENUS
    assert data["active"]["legacy_market_id"] == LEGACY_BNB_VENUS
    assert data["active"]["chain_id"] == 56
    assert data["active"]["protocol"] == "venus_core"


def test_liquidation_config_health_api_uses_request_market_scope(monkeypatch):
    monkeypatch.delenv("LIQUIDATION_MARKET_ID", raising=False)
    monkeypatch.delenv("LIQUIDATION_CHAIN_ID", raising=False)
    monkeypatch.delenv("CHAIN_ID", raising=False)

    response = app.test_client().get("/api/liquidation/config-health?market_id=bnb-venus-core&chain_id=56&network=bnb&protocol=venus_core")
    data = response.get_json()

    assert response.status_code == 200
    assert data["market"]["market_id"] == CANONICAL_BNB_VENUS
    assert data["market"]["legacy_market_id"] == LEGACY_BNB_VENUS
    assert data["expected_chain_id"] == 56
    assert data["chain_id"] == 56
    assert not any("chain id" in error for error in data["errors"])


def test_bnb_venus_accounts_api_exposes_current_pool_scan_progress(monkeypatch, tmp_path):
    _write_latest(tmp_path)
    monkeypatch.setattr(bnb, "bnb_venus_output_dir", lambda: tmp_path)

    response = app.test_client().get(
        "/api/liquidation/accounts?market_id=bnb-venus-core&chain_id=56&network=bnb&protocol=venus_core&page_size=20"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["registry_window"]["total_count"] == 42
    assert data["registry_window"]["health_report_count"] == 3
    assert data["registry_window"]["pending_health_count"] == 39
    assert data["registry_window"]["scan_coverage_rate"] == 3 / 42
    assert data["registry_window"]["pool_tier_counts"]["core"] == 1
    assert data["registry_window"]["pool_tier_counts"]["borrow_health"] == 40
    assert any(row["pool_tier"] == "high_frequency" for row in data["accounts"])


def test_bnb_venus_payload_generation_is_blocked_as_observation_only(monkeypatch, tmp_path):
    _write_latest(tmp_path)
    monkeypatch.setattr(bnb, "bnb_venus_output_dir", lambda: tmp_path)

    response = app.test_client().get(
        "/api/liquidation/account/payload"
        "?account=0x0000000000000000000000000000000000000002"
        "&market_id=bnb-venus-core&chain_id=56&network=bnb&protocol=venus_core"
    )
    data = response.get_json()

    assert response.status_code == 400
    assert data["state"] == "submission_blocked"
    assert data["execution_phase"] == "observation_only"
    assert data["blocked_reasons"] == ["venus_core_observation_only"]
    assert data["cached_snapshot"]["found"] is True
