import json
from datetime import datetime, timezone

import pytest

from db.storage_liquidation_pool import (
    _core_opportunity_viable,
    _core_profit_assessment,
    sync_liquidation_borrow_health_pool,
)


@pytest.fixture(autouse=True)
def clear_liquidation_pool_caches():
    import db.storage_liquidation_pool as pool_storage

    pool_storage._POOL_READ_CACHE.clear()
    pool_storage._POOL_WRITE_CACHE.clear()


def _row(net_profit, *, debt=1000, health_factor=0.98, account="0x0000000000000000000000000000000000000001"):
    return {
        "account": account,
        "health_factor": health_factor,
        "total_debt_base": debt,
        "recommended_candidate": {
            "debt_symbol": "USDC",
            "collateral_symbol": "WAVAX",
            "estimated_profit": {
                "operator_net_profit_usd": net_profit,
                "net_profit_base": net_profit,
            },
        },
    }


def test_core_opportunity_keeps_positive_profit_for_manual_review():
    assert _core_opportunity_viable(_row(1.01), min_operator_net_profit_usd=1.0) is True
    assert _core_opportunity_viable(_row(1.0), min_operator_net_profit_usd=1.0) is True
    assert _core_opportunity_viable(_row(0.99), min_operator_net_profit_usd=1.0) is True
    assert _core_opportunity_viable(_row(0.0), min_operator_net_profit_usd=1.0) is False


def test_core_opportunity_requires_candidate_and_positive_debt():
    assert _core_opportunity_viable(_row(10, debt=0), min_operator_net_profit_usd=1.0) is False
    assert _core_opportunity_viable(
        {"health_factor": 0.98, "total_debt_base": 1000, "liquidation_profit": {"net_profit_base": 10}},
        min_operator_net_profit_usd=1.0,
    ) is True


def test_core_profit_assessment_marks_low_profit_as_manual_test_item():
    low = _core_profit_assessment(_row(0.75), min_operator_net_profit_usd=1.0)
    high = _core_profit_assessment(_row(1.25), min_operator_net_profit_usd=1.0)

    assert low["label"] == "low_profit_manual_test"
    assert low["auto_execution_blocked"] is True
    assert low["manual_review_required"] is True
    assert low["blocked_reasons"] == ["profit_below_minimum"]
    assert high["label"] == "over_1u_candidate"
    assert high["auto_execution_blocked"] is False


def test_core_profit_assessment_blocks_non_liquidatable_without_candidate():
    assessment = _core_profit_assessment(
        {
            "health_factor": 1.006,
            "status": "warning",
            "total_debt_base": 1000,
            "liquidation_profit": {"operator_net_profit_usd": 10},
        },
        min_operator_net_profit_usd=1.0,
    )

    assert assessment["label"] == "watch_only_not_liquidatable"
    assert assessment["above_auto_profit_threshold"] is True
    assert assessment["auto_execution_blocked"] is True
    assert assessment["executable_candidate_present"] is False
    assert assessment["blocked_reasons"] == ["account_not_liquidatable", "no_liquidation_candidate"]


def test_sync_core_pool_keeps_low_profit_manual_test_items(monkeypatch):
    import db.storage_liquidation_pool as pool_storage

    captured_core_params = []

    class FakeCursor:
        def __init__(self):
            self.fetchall_result = []

        def execute(self, sql, params=()):
            if "SELECT account FROM liquidation_borrow_health_pool" in sql:
                self.fetchall_result = []
            if "INSERT INTO liquidation_core_opportunity_pool" in sql:
                captured_core_params.append(params)

        def fetchall(self):
            return self.fetchall_result

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @staticmethod
        def cursor():
            class CursorContext:
                def __enter__(self):
                    return FakeCursor()

                def __exit__(self, exc_type, exc, tb):
                    return False

            return CursorContext()

    monkeypatch.setattr(pool_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(pool_storage, "db_connection", lambda *args, **kwargs: FakeConnection())

    result = sync_liquidation_borrow_health_pool(
        "postgresql://example",
        [
            _row(0.75),
            _row(1.25, health_factor=0.97, account="0x0000000000000000000000000000000000000002"),
        ],
        min_operator_net_profit_usd=1.0,
    )

    assert result["core_count"] == 2
    assert len(captured_core_params) == 2
    low = captured_core_params[0]
    high = captured_core_params[1]
    assert low[0:4] == ("avalanche-aave-v3", 43114, "avalanche", "aave_v3")
    assert low[12] == 0.75
    assert low[16] == "low_profit_manual_test"
    assert json.loads(low[17]) == ["profit_below_minimum"]
    assert json.loads(low[18])["profit_assessment"] == {
        "estimated_operator_net_profit_usd": 0.75,
        "min_operator_net_profit_usd": 1.0,
        "above_auto_profit_threshold": False,
        "manual_review_required": True,
        "auto_execution_blocked": True,
        "executable_candidate_present": True,
        "blocked_reasons": ["profit_below_minimum"],
        "label": "low_profit_manual_test",
    }
    assert high[12] == 1.25
    assert high[16] == "over_1u_candidate"
    assert json.loads(high[17]) == []
    assert json.loads(high[18])["profit_assessment"]["above_auto_profit_threshold"] is True


def test_load_core_pool_exposes_profit_assessment(monkeypatch):
    import db.storage_liquidation_pool as pool_storage

    metadata = {
        "profit_assessment": {
            "estimated_operator_net_profit_usd": 0.75,
            "min_operator_net_profit_usd": 1.0,
            "above_auto_profit_threshold": False,
            "manual_review_required": True,
            "auto_execution_blocked": True,
            "label": "low_profit_manual_test",
        }
    }

    class FakeCursor:
        def execute(self, sql, params=()):
            self.sql = sql
            self.params = params

        @staticmethod
        def fetchall():
            return [
                (
                    "0x0000000000000000000000000000000000000001",
                    0.98,
                    500.0,
                    1000,
                    1200,
                    "USDC",
                    "WAVAX",
                    "100",
                    0.75,
                    0.2,
                    True,
                    "pending",
                    "low_profit_manual_test",
                    '["profit_below_minimum"]',
                    datetime(2026, 8, 3, tzinfo=timezone.utc),
                    json.dumps(metadata),
                )
            ]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @staticmethod
        def cursor():
            class CursorContext:
                def __enter__(self):
                    return FakeCursor()

                def __exit__(self, exc_type, exc, tb):
                    return False

            return CursorContext()

    monkeypatch.setattr(pool_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(pool_storage, "db_connection", lambda *args, **kwargs: FakeConnection())

    rows = pool_storage.load_liquidation_core_opportunity_pool("postgresql://example", limit=20)

    assert rows[0]["profit_assessment"]["label"] == "low_profit_manual_test"
    assert rows[0]["profit_assessment_label"] == "low_profit_manual_test"
    assert rows[0]["auto_execution_blocked"] is True
    assert rows[0]["above_auto_profit_threshold"] is False
    assert rows[0]["blocked_reasons"] == ["profit_below_minimum"]


def test_load_borrow_health_pool_uses_deepcopy_cache(monkeypatch):
    import db.storage_liquidation_pool as pool_storage

    execute_calls = []
    report = {"nested": {"value": 1}}

    class FakeCursor:
        def execute(self, sql, params=()):
            execute_calls.append(sql)

        @staticmethod
        def fetchall():
            return [
                (
                    "0x0000000000000000000000000000000000000001",
                    1.2,
                    "watching",
                    "yellow",
                    1000,
                    500,
                    1,
                    None,
                    json.dumps(report),
                )
            ]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @staticmethod
        def cursor():
            class CursorContext:
                def __enter__(self):
                    return FakeCursor()

                def __exit__(self, exc_type, exc, tb):
                    return False

            return CursorContext()

    monkeypatch.setattr(pool_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(pool_storage, "db_connection", lambda *args, **kwargs: FakeConnection())

    first = pool_storage.load_liquidation_borrow_health_pool("postgresql://cache-test", limit=20)
    first[0]["report"]["nested"]["value"] = 999
    second = pool_storage.load_liquidation_borrow_health_pool("postgresql://cache-test", limit=20)

    assert len(execute_calls) == 1
    assert second[0]["report"]["nested"]["value"] == 1


def test_sync_borrow_health_pool_skips_unchanged_account_writes(monkeypatch):
    import db.storage_liquidation_pool as pool_storage

    execute_calls = []

    class FakeCursor:
        def __init__(self):
            self.fetchall_result = []

        def execute(self, sql, params=()):
            execute_calls.append(sql)
            if "SELECT account FROM liquidation_borrow_health_pool" in sql:
                self.fetchall_result = []

        def fetchall(self):
            return self.fetchall_result

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @staticmethod
        def cursor():
            class CursorContext:
                def __enter__(self):
                    return FakeCursor()

                def __exit__(self, exc_type, exc, tb):
                    return False

            return CursorContext()

    monkeypatch.setattr(pool_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(pool_storage, "db_connection", lambda *args, **kwargs: FakeConnection())

    row = _row(0.25, health_factor=1.2)
    sync_liquidation_borrow_health_pool("postgresql://write-cache-test", [row])
    sync_liquidation_borrow_health_pool("postgresql://write-cache-test", [row])

    borrow_upserts = [
        sql for sql in execute_calls
        if "INSERT INTO liquidation_borrow_health_pool" in sql
    ]
    assert len(borrow_upserts) == 1
