import pytest

import core.db.storage_liquidation_failures as storage


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        pass

    def fetchall(self):
        return list(self.rows)


class Connection:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return Cursor(self.rows)


def row(scope):
    return (*[scope[key] for key in ("market_id", "chain_id", "network", "protocol")],
            1, "0xabc", 1, "USDC", "USDT", "rpc", "reverted", "{}", "scan", None)


def test_failure_sample_reader_rejects_wrong_scope(monkeypatch):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    monkeypatch.setattr(storage, "_market_scope", lambda *args, **kwargs: scope)
    monkeypatch.setattr(storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(storage, "db_connection", lambda *args, **kwargs: Connection([row({**scope, "protocol": "aave"})]))

    with pytest.raises(ValueError, match=r"invalid scoped DB row at index 0.*protocol"):
        storage.load_recent_liquidation_failure_samples("postgresql://example")
