from db import storage_common
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


def test_synthetic_pool_benchmark_improves_connection_latency():
    result = run_benchmark(operations=50, connect_delay_ms=1.0)

    assert result["improvement_percent"] > 50.0
