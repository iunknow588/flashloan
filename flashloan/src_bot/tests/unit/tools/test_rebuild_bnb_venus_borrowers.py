from bot.tools import rebuild_bnb_venus_borrowers as rebuild
from bot.tools.rebuild_bnb_venus_borrowers import _filter_log_capable_rpc_pool, _incremental_start_block, _merge_registries


def test_rebuild_bnb_venus_borrowers_merges_incremental_registry_rows():
    previous = {
        "from_block": 10,
        "to_block": 20,
        "accounts": [
            {
                "account": "0x0000000000000000000000000000000000000001",
                "first_block": 10,
                "last_block": 20,
                "event_count": 1,
                "event_counts": {"Borrow": 1},
                "markets_touched": ["vUSDT"],
                "borrow_markets": ["vUSDT"],
                "collateral_markets": [],
                "tier": "warm",
            }
        ],
    }
    current = {
        "from_block": 21,
        "to_block": 30,
        "accounts": [
            {
                "account": "0x0000000000000000000000000000000000000001",
                "first_block": 25,
                "last_block": 30,
                "event_count": 2,
                "event_counts": {"RepayBorrow": 2},
                "markets_touched": ["vBNB"],
                "borrow_markets": ["vBNB"],
                "collateral_markets": [],
                "tier": "hot",
            }
        ],
    }

    merged = _merge_registries(previous, current)

    assert merged["account_count"] == 1
    assert merged["accounts"][0]["first_block"] == 10
    assert merged["accounts"][0]["last_block"] == 30
    assert merged["accounts"][0]["event_count"] == 3
    assert merged["accounts"][0]["event_counts"] == {"Borrow": 1, "RepayBorrow": 2}
    assert merged["accounts"][0]["borrow_markets"] == ["vBNB", "vUSDT"]


def test_rebuild_bnb_venus_borrowers_audit_helpers_produce_files(tmp_path):
    registry = {
        "storage_mode": "file_only",
        "discovery_mode": "borrow_events_only",
        "events_jsonl": str(tmp_path / "venus_borrower_events_2mo.jsonl"),
        "scan_complete": True,
        "account_count": 1,
        "accounts": [{"account": "0x0000000000000000000000000000000000000001"}],
    }
    (tmp_path / "venus_borrower_registry.json").write_text("{}", encoding="utf-8")
    (tmp_path / "venus_borrower_accounts.txt").write_text("0x0000000000000000000000000000000000000001\n", encoding="utf-8")
    (tmp_path / "venus_borrower_events_2mo.jsonl").write_text("", encoding="utf-8")

    result = rebuild.audit_file_state(tmp_path)
    paths = rebuild.write_audit_report(tmp_path, result)

    assert paths["json"].endswith("venus_file_state_audit.json")
    assert paths["markdown"].endswith("venus_file_state_audit.md")


def test_rebuild_bnb_incremental_start_rewinds_after_incomplete_scan():
    assert _incremental_start_block({"scan_complete": True, "to_block": 500}) == 501
    assert _incremental_start_block(
        {
            "scan_complete": False,
            "to_block": 500,
            "accounts": [{"account": "0x0000000000000000000000000000000000000001", "last_block": 321}],
        }
    ) == 322


def test_rebuild_bnb_filters_rpc_pool_by_get_logs_capability():
    class WorkingEth:
        block_number = 10

        def get_logs(self, _params):
            return []

    class FailingEth:
        block_number = 10

        def get_logs(self, _params):
            raise RuntimeError("413 request entity too large")

    class Client:
        def __init__(self, eth):
            self.eth = eth

    clients, urls, failures = _filter_log_capable_rpc_pool(
        [Client(FailingEth()), Client(WorkingEth())],
        ["https://bad.example", "https://good.example"],
        probe_vtoken="0x0000000000000000000000000000000000000001",
    )

    assert len(clients) == 1
    assert urls == ["https://good.example"]
    assert "413 request entity too large" in failures[0]
