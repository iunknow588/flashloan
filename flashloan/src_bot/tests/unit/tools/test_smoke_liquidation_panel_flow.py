import json

from tools import smoke_liquidation_panel_flow as smoke


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content_type="application/json"):
        self.status_code = status_code
        self._payload = payload or {}
        self.is_json = content_type == "application/json"
        self.headers = {"content-type": content_type}

    def get_json(self):
        return self._payload


class FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, path):
        if path == "/liquidation":
            return FakeResponse(200, {}, "text/html")
        if path.startswith("/api/liquidation/borrow-pool"):
            return FakeResponse(
                200,
                {
                    "summary": {
                        "scan_response_source": "file_observation",
                        "current_pool_account_count": 42,
                        "registry_account_count": 42,
                        "scanned_account_count": 3,
                        "pending_account_count": 39,
                    }
                },
            )
        if path.startswith("/api/liquidation/accounts"):
            return FakeResponse(
                200,
                {
                    "accounts": [{"account": "0x0000000000000000000000000000000000000001"}],
                    "registry_window": {
                        "total_count": 42,
                        "health_report_count": 3,
                        "pending_health_count": 39,
                    },
                },
            )
        if path.startswith("/api/liquidation/account/cached"):
            return FakeResponse(200, {"found": True})
        if path.startswith("/api/liquidation/account/payload"):
            return FakeResponse(
                400,
                {
                    "state": "submission_blocked",
                    "blocked_reasons": ["venus_core_observation_only"],
                },
            )
        return FakeResponse(404, {})


class FakeApp:
    def test_client(self):
        return FakeClient()


def test_smoke_liquidation_panel_flow_builds_readonly_bnb_report(monkeypatch):
    monkeypatch.setattr(smoke, "app", FakeApp())

    report = smoke.build_smoke_report(market_id="bnb-venus-core", chain_id=56)

    assert report["ok"] is True
    assert report["selected_account"] == "0x0000000000000000000000000000000000000001"
    assert report["checks"]["current_pool_progress"]["ok"] is True
    assert report["checks"]["bnb_payload_observation_block"]["ok"] is True


def test_smoke_liquidation_panel_flow_writes_json_and_markdown(tmp_path):
    report = {
        "generated_at": "2026-08-17T00:00:00+00:00",
        "market_id": "bnb-venus-core",
        "chain_id": 56,
        "selected_account": "0x1",
        "ok": True,
        "checks": {"page_load": {"ok": True, "detail": "status=200"}},
    }

    paths = smoke.write_report(report, tmp_path)

    saved = json.loads((tmp_path / "liquidation_panel_flow_smoke.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True
    assert paths["markdown"].endswith("liquidation_panel_flow_smoke.md")
