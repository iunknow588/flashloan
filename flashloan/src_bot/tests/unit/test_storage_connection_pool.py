from contextlib import contextmanager

import db.storage_cow_execution as cow_storage
import db.storage_chain_markets as chain_market_storage
import db.storage_liquidation_accounts as liquidation_accounts_storage
import db.storage_liquidation_attempts as liquidation_attempts_storage
import db.storage_liquidation_legacy as liquidation_legacy_storage
import db.storage_market as market_storage
import db.storage_liquidation_pool as pool_storage
from db import storage_common
from db.storage_liquidation_pool import load_liquidation_accounts_for_assets
from core.market_config import liquidation_market_config
from tools.benchmark_db_pool import run_benchmark


class FakeConnection:
    def __init__(self, label="pooled"):
        self.label = label

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakePoolConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeTransaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("transaction_enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append("transaction_exit")
        return False


class FakeCursor:
    def __init__(self, events, fetchone_result=None, fetchall_result=None):
        self.events = events
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        self.events.append(("execute", query, params))

    def executemany(self, query, params_list):
        self.events.append(("executemany", query, list(params_list)))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return list(self.fetchall_result)


class FakeTransactionConnection:
    def __init__(self, events, *, fetchone_result=None, fetchall_result=None):
        self.events = events
        self._cursor = FakeCursor(events, fetchone_result=fetchone_result, fetchall_result=fetchall_result)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        self.events.append("transaction_requested")
        return FakeTransaction(self.events)

    def cursor(self):
        return self._cursor


def test_db_connection_reuses_connection_pool(monkeypatch):
    created = []

    class FakeConnectionPool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.connection_count = 0
            self.closed = False
            created.append(self)

        def connection(self):
            self.connection_count += 1
            return FakePoolConnection(FakeConnection())

        def close(self):
            self.closed = True

    storage_common.close_connection_pools()
    monkeypatch.setenv("DATABASE_POOL_ENABLED", "true")
    monkeypatch.setenv("DATABASE_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("DATABASE_POOL_MAX_SIZE", "10")
    monkeypatch.setattr(storage_common, "require_psycopg_pool", lambda: FakeConnectionPool)

    with storage_common.db_connection("postgresql://example") as first:
        assert first.label == "pooled"
    with storage_common.db_connection("postgresql://example") as second:
        assert second.label == "pooled"

    assert len(created) == 1
    assert created[0].kwargs["min_size"] == 2
    assert created[0].kwargs["max_size"] == 10
    assert created[0].connection_count == 2
    storage_common.close_connection_pools()
    assert created[0].closed is True


def test_db_connection_falls_back_to_psycopg_connect(monkeypatch):
    calls = []

    class FakePsycopg:
        @staticmethod
        def connect(database_url, connect_timeout=8):
            calls.append((database_url, connect_timeout))
            return FakeConnection("direct")

    storage_common.close_connection_pools()
    monkeypatch.setenv("DATABASE_POOL_ENABLED", "false")
    monkeypatch.setattr(storage_common, "require_psycopg", lambda: FakePsycopg)
    monkeypatch.setattr(storage_common, "require_psycopg_pool", lambda: None)

    with storage_common.db_connection("postgresql://example", connect_timeout=3) as connection:
        assert connection.label == "direct"

    assert calls == [("postgresql://example", 3)]


def test_db_connection_short_probe_skips_pool_replenishment(monkeypatch):
    pool_calls = []
    direct_calls = []

    class FakePsycopg:
        @staticmethod
        def connect(database_url, connect_timeout=8):
            direct_calls.append((database_url, connect_timeout))
            return FakeConnection("direct-short-probe")

    class UnexpectedPool:
        def __init__(self, **_kwargs):
            pool_calls.append(True)

    storage_common.close_connection_pools()
    monkeypatch.setenv("DATABASE_POOL_ENABLED", "true")
    monkeypatch.setattr(storage_common, "require_psycopg_pool", lambda: UnexpectedPool)
    monkeypatch.setattr(storage_common, "require_psycopg", lambda: FakePsycopg)

    with storage_common.db_connection("postgresql://example", connect_timeout=1) as connection:
        assert connection.label == "direct-short-probe"

    assert pool_calls == []
    assert direct_calls == [("postgresql://example", 1)]


def test_db_connection_falls_back_when_pool_cannot_provide_connection(monkeypatch):
    created = []
    direct_calls = []

    class BrokenConnectionPool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            created.append(self)

        def connection(self, timeout=None):
            self.timeout = timeout
            raise TimeoutError("pool unavailable")

        def close(self):
            self.closed = True

    class FakePsycopg:
        @staticmethod
        def connect(database_url, connect_timeout=8):
            direct_calls.append((database_url, connect_timeout))
            return FakeConnection("direct-after-pool-failure")

    storage_common.close_connection_pools()
    monkeypatch.setenv("DATABASE_POOL_ENABLED", "true")
    monkeypatch.setattr(storage_common, "require_psycopg_pool", lambda: BrokenConnectionPool)
    monkeypatch.setattr(storage_common, "require_psycopg", lambda: FakePsycopg)

    with storage_common.db_connection("postgresql://example", connect_timeout=4) as connection:
        assert connection.label == "direct-after-pool-failure"

    assert len(created) == 1
    assert created[0].timeout == 4
    assert created[0].closed is True
    assert direct_calls == [("postgresql://example", 4)]


def test_db_transaction_wraps_connections_that_support_transactions():
    events = []
    connection = FakeTransactionConnection(events)

    with storage_common.db_transaction(connection):
        events.append("inside")

    assert events == ["transaction_requested", "transaction_enter", "inside", "transaction_exit"]


def test_db_transaction_records_commit_and_applies_optional_limits(monkeypatch):
    events = []

    class ConfigurableConnection(FakeTransactionConnection):
        def execute(self, query):
            events.append(("connection_execute", query))

    storage_common.clear_database_transaction_events()
    monkeypatch.setenv("DATABASE_LOCK_TIMEOUT_MS", "250")
    monkeypatch.setenv("DATABASE_STATEMENT_TIMEOUT_MS", "1000")
    connection = ConfigurableConnection(events)

    with storage_common.db_transaction(connection, operation="unit.transaction"):
        events.append("inside")

    recorded = storage_common.database_transaction_events()
    assert events[:2] == ["transaction_requested", "transaction_enter"]
    assert ("connection_execute", "SET LOCAL lock_timeout = '250ms'") in events
    assert ("connection_execute", "SET LOCAL statement_timeout = '1000ms'") in events
    assert recorded[-1]["operation"] == "unit.transaction"
    assert recorded[-1]["outcome"] == "commit"
    assert recorded[-1]["error_kind"] is None


def test_db_transaction_records_deadlock_as_retryable_rollback():
    storage_common.clear_database_transaction_events()
    connection = FakeTransactionConnection([])

    try:
        with storage_common.db_transaction(connection, operation="unit.deadlock"):
            raise RuntimeError("deadlock detected")
    except RuntimeError:
        pass

    recorded = storage_common.database_transaction_events()
    assert recorded[-1]["operation"] == "unit.deadlock"
    assert recorded[-1]["outcome"] == "rollback"
    assert recorded[-1]["error_kind"] == "deadlock"
    assert recorded[-1]["retryable"] is True


def test_run_db_transaction_retries_deadlock_with_a_fresh_connection(monkeypatch):
    connections = [FakeTransactionConnection([]), FakeTransactionConnection([])]
    calls = []

    @contextmanager
    def fake_connection(*_args, **_kwargs):
        connection = connections.pop(0)
        calls.append(connection)
        yield connection

    monkeypatch.setattr(storage_common, "db_connection", fake_connection)
    storage_common.clear_database_transaction_events()

    def callback(connection):
        if len(calls) == 1:
            raise RuntimeError("deadlock detected")
        return "ok"

    result = storage_common.run_db_transaction(
        "postgresql://example",
        "unit.retry",
        callback,
        max_retries=1,
        retry_backoff_ms=0,
    )

    assert result == "ok"
    assert len(calls) == 2
    events = storage_common.database_transaction_events()
    assert any(event["outcome"] == "rollback" and event["error_kind"] == "deadlock" for event in events)
    assert any(event["outcome"] == "retry" and event["attempt"] == 1 for event in events)


def test_run_db_transaction_does_not_retry_non_retryable_errors(monkeypatch):
    connections = [FakeTransactionConnection([])]
    @contextmanager
    def fake_connection(*_args, **_kwargs):
        yield connections.pop(0)

    monkeypatch.setattr(storage_common, "db_connection", fake_connection)

    def callback(_connection):
        raise RuntimeError("validation error")

    try:
        storage_common.run_db_transaction(
            "postgresql://example",
            "unit.no_retry",
            callback,
            max_retries=3,
            retry_backoff_ms=0,
        )
    except RuntimeError as exc:
        assert str(exc) == "validation error"
    else:
        raise AssertionError("non-retryable transaction errors must propagate")

    assert connections == []


def test_record_cow_execution_attempts_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events, fetchone_result=(17,))

    monkeypatch.setattr(cow_storage, "ensure_cow_execution_attempts_table", lambda _database_url: None)
    monkeypatch.setattr(cow_storage, "prune_cow_execution_attempts", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cow_storage, "db_connection", lambda *args, **kwargs: connection)

    ids = cow_storage.record_cow_execution_attempts(
        "postgresql://example",
        [{"state": "quoted", "execution_phase": "quote_precheck"}],
        retention_days=0,
        dedupe_market_candidates=False,
    )

    assert ids == [17]
    assert events[0] == "transaction_requested"
    assert "transaction_enter" in events
    assert "transaction_exit" in events


def test_record_liquidation_borrow_health_scan_batch_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events, fetchone_result=(9,))

    monkeypatch.setattr(pool_storage, "db_connection", lambda *args, **kwargs: connection)
    monkeypatch.setattr(pool_storage, "_upsert_scan_config_snapshot", lambda *args, **kwargs: None)

    batch = pool_storage.record_liquidation_borrow_health_scan_batch(
        "postgresql://example",
        started_at="2026-08-19T00:00:00+00:00",
        finished_at="2026-08-19T00:05:00+00:00",
        status="success",
        account_count=1,
        scanned_count=1,
        risk_count=0,
        error_count=0,
        entered_count=1,
        exited_count=0,
        rpc_url="https://rpc.example",
        block_number=123,
        watch_health_factor=1.5,
        metadata={"source": "unit-test"},
        market_id="avalanche-aave-v3",
        chain_id=43114,
    )

    assert batch["id"] == 9
    assert events[0] == "transaction_requested"
    assert "transaction_enter" in events
    assert "transaction_exit" in events


def test_append_binance_price_history_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events)

    monkeypatch.setattr(market_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(market_storage, "db_connection", lambda *args, **kwargs: connection)

    market_storage.append_binance_price_history(
        "postgresql://example",
        [
            {
                "observed_at": "2026-08-19T00:00:00+00:00",
                "symbol": "BTC",
                "price": 10,
                "event_time": 1,
                "source": "unit-test",
            }
        ],
    )

    assert events[0] == "transaction_requested"
    assert any(entry[0] == "executemany" for entry in events if isinstance(entry, tuple))
    assert "transaction_exit" in events


def test_record_liquidation_account_scans_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events)

    monkeypatch.setattr(liquidation_accounts_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(liquidation_accounts_storage, "db_connection", lambda *args, **kwargs: connection)
    monkeypatch.setattr(liquidation_accounts_storage, "write_liquidation_account_scan_reports", lambda _cursor, reports: len(reports))

    count = liquidation_accounts_storage.record_liquidation_account_scans(
        "postgresql://example",
        [
            {
                "account": "0xabc",
                "summary": {"health_factor": 1.2, "status": "risk", "candidate_count": 1},
                "market_id": "avalanche-aave-v3",
                "chain_id": 43114,
            }
        ],
    )

    assert count == 1
    assert events[0] == "transaction_requested"
    assert "transaction_enter" in events
    assert "transaction_exit" in events


def test_record_liquidation_execution_attempt_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events, fetchone_result=(41,))

    monkeypatch.setattr(liquidation_attempts_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(liquidation_attempts_storage, "db_connection", lambda *args, **kwargs: connection)

    attempt_id = liquidation_attempts_storage.record_liquidation_execution_attempt(
        "postgresql://example",
        account="0xabc",
        mode="flashloan",
        state="submission_blocked",
        blocked_reasons=["static_call_required"],
        request_payload={"account": "0xabc"},
        quote={"state": "quote"},
        preflight={"ready": False},
        tx_hash="0x1",
        receipt={"status": 0},
        error="blocked",
    )

    assert attempt_id == 41
    assert events[0] == "transaction_requested"
    assert "transaction_enter" in events
    assert "transaction_exit" in events


def test_record_liquidation_discovery_scan_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events)

    monkeypatch.setattr(liquidation_legacy_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(liquidation_legacy_storage, "db_connection", lambda *args, **kwargs: connection)

    liquidation_legacy_storage.record_liquidation_discovery_scan(
        "postgresql://example",
        mode="recent",
        status="success",
        rpc_url="https://rpc.example",
        pool_address="0xpool",
        from_block=1,
        to_block=2,
        scan_start_at="2026-08-19T00:00:00+00:00",
        scan_end_at="2026-08-19T00:05:00+00:00",
        discovered_count=3,
        error=None,
        market_id="avalanche-aave-v3",
        chain_id=43114,
    )

    assert events[0] == "transaction_requested"
    assert "transaction_enter" in events
    assert "transaction_exit" in events


def test_upsert_liquidation_accounts_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events)

    monkeypatch.setattr(liquidation_legacy_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(liquidation_legacy_storage, "db_connection", lambda *args, **kwargs: connection)

    accounts = liquidation_legacy_storage.upsert_liquidation_accounts(
        "postgresql://example",
        ["0xabc", "0xdef"],
        source="unit-test",
        market_id="avalanche-aave-v3",
        chain_id=43114,
    )

    assert accounts == ["0xabc", "0xdef"]
    assert events[0] == "transaction_requested"
    assert "transaction_enter" in events
    assert "transaction_exit" in events


def test_replace_current_dex_arbitrage_candidates_runs_inside_explicit_transaction(monkeypatch):
    events = []
    connection = FakeTransactionConnection(events)

    monkeypatch.setattr(chain_market_storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(chain_market_storage, "db_connection", lambda *args, **kwargs: connection)

    count = chain_market_storage.replace_current_dex_arbitrage_candidates(
        "postgresql://example",
        market_id="avalanche-aave-v3",
        chain_id=43114,
        network="avalanche",
        candidates=[
            {
                "route_key": "route-1",
                "route_symbols": ["USDC", "WAVAX"],
                "pool_addresses": ["0x1"],
                "status": "observed",
                "gross_profit_usd": 1.2,
                "estimated_cost_usd": 0.3,
                "net_profit_usd": 0.9,
                "evidence": {"source": "unit-test"},
                "observed_block": 123,
            }
        ],
        expires_at="2026-08-20T00:00:00+00:00",
    )

    assert count == 1
    assert events[0] == "transaction_requested"
    assert "transaction_enter" in events
    assert "transaction_exit" in events


def test_db_connection_does_not_fallback_after_sql_error(monkeypatch):
    created = []
    direct_calls = []
    close_calls = []

    class FakeCursorErrorConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnectionPool:
        def __init__(self, **kwargs):
            created.append(self)

        def connection(self):
            return FakeCursorErrorConnection()

        def close(self):
            close_calls.append(True)

    class FakePsycopg:
        @staticmethod
        def connect(database_url, connect_timeout=8):
            direct_calls.append((database_url, connect_timeout))
            raise AssertionError("SQL errors must not trigger a fallback connection")

    storage_common.close_connection_pools()
    monkeypatch.setenv("DATABASE_POOL_ENABLED", "true")
    monkeypatch.setattr(storage_common, "require_psycopg_pool", lambda: FakeConnectionPool)
    monkeypatch.setattr(storage_common, "require_psycopg", lambda: FakePsycopg)

    try:
        with storage_common.db_connection("postgresql://example") as connection:
            assert connection is not None
            raise RuntimeError("query failed")
    except RuntimeError as exc:
        assert str(exc) == "query failed"
    else:
        raise AssertionError("query error should propagate")

    assert len(created) == 1
    assert direct_calls == []
    storage_common.close_connection_pools()
    assert close_calls == [True]


def test_db_connection_drops_pool_after_admin_termination_error(monkeypatch):
    close_calls = []

    class FakeCursorErrorConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnectionPool:
        def __init__(self, **_kwargs):
            pass

        def connection(self):
            return FakeCursorErrorConnection()

        def close(self):
            close_calls.append(True)

    storage_common.close_connection_pools()
    monkeypatch.setenv("DATABASE_POOL_ENABLED", "true")
    monkeypatch.setattr(storage_common, "require_psycopg_pool", lambda: FakeConnectionPool)

    try:
        with storage_common.db_connection("postgresql://example") as connection:
            assert connection is not None
            raise RuntimeError("terminating connection due to administrator command")
    except RuntimeError:
        pass
    else:
        raise AssertionError("query error should propagate")

    assert close_calls == [True]


def test_synthetic_pool_benchmark_improves_connection_latency():
    result = run_benchmark(operations=50, connect_delay_ms=1.0)

    assert result["improvement_percent"] > 50.0


def test_load_liquidation_accounts_for_assets_queries_core_pool(monkeypatch):
    captured = {}

    class FakeCursor:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        @staticmethod
        def fetchall():
            return [("0x1",), ("0x2",)]

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

    monkeypatch.setattr(storage_common, "db_connection", lambda *args, **kwargs: FakeConnection())
    import db.storage_liquidation_pool as pool_storage

    monkeypatch.setattr(pool_storage, "db_connection", lambda *args, **kwargs: FakeConnection())

    accounts = load_liquidation_accounts_for_assets("postgresql://example", ["WAVAX", "USDC"], limit=10)

    assert accounts == ["0x1", "0x2"]
    assert "liquidation_core_opportunity_pool" in captured["sql"]
    market = liquidation_market_config()
    assert captured["params"] == (market.market_id, market.chain_id, ["WAVAX", "USDC"], ["WAVAX", "USDC"], 10)
