import json

from tools import smoke_bnb_venus_readonly as smoke


class FakeEth:
    chain_id = 56
    gas_price = 3_000_000_000
    block_number = 123


class FakeW3:
    eth = FakeEth()


def test_smoke_bnb_venus_readonly_writes_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        smoke,
        "build_venus_market_static_table",
        lambda *args, **kwargs: {
            "markets": [{"vtoken_address": "0x0000000000000000000000000000000000000001"}],
            "market_count": 1,
            "comptroller_address": "0x0000000000000000000000000000000000000002",
            "oracle_address": "0x0000000000000000000000000000000000000003",
        },
    )
    monkeypatch.setattr(
        smoke,
        "read_bnb_runtime_metrics",
        lambda _w3: {
            "gas_price": {"ok": True, "gas_price_gwei": 3.0, "error": None},
            "native_usd_price": {"ok": True, "bnb_usd": 600.0, "error": None},
        },
    )

    report = smoke.build_smoke_report(FakeW3(), rpc_url="https://bsc.example")
    paths = smoke.write_smoke_report(report, tmp_path)

    saved = json.loads((tmp_path / "venus_readonly_smoke_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert saved["checks"]["bnb_rpc"]["ok"] is True
    assert "venus_readonly_smoke_report.md" in paths["markdown"]


def test_smoke_bnb_venus_readonly_loads_account_limit(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {"account": "0x0000000000000000000000000000000000000001"},
                    {"account": "0x0000000000000000000000000000000000000002"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert smoke._load_accounts(path, limit=1) == ["0x0000000000000000000000000000000000000001"]
