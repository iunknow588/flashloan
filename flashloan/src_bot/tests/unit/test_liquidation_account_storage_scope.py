import pytest

import core.db.storage_liquidation_accounts as storage


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        self.params = params

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _Cursor(self.rows)


def _row(scope):
    return (
        scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"],
        "0xabc", "scan", True, None, None, None,
        1.2, "healthy", "green", 1, 10.0, 2.0, "normal", "{}", "{}",
    )


def test_latest_liquidation_reports_validates_database_row_scope(monkeypatch):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    monkeypatch.setattr(storage, "_scope_values", lambda **kwargs: scope)
    monkeypatch.setattr(storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(storage, "db_connection", lambda *args, **kwargs: _Connection([_row(scope)]))

    reports = storage.load_latest_liquidation_account_reports("postgresql://example")

    assert reports[0]["market_id"] == "m"
    assert reports[0]["chain_id"] == 56


def test_latest_liquidation_reports_rejects_wrong_database_row_scope(monkeypatch):
    scope = {"market_id": "m", "chain_id": 56, "network": "bnb", "protocol": "venus"}
    invalid = {**scope, "chain_id": 1}
    monkeypatch.setattr(storage, "_scope_values", lambda **kwargs: scope)
    monkeypatch.setattr(storage, "require_psycopg", lambda: object())
    monkeypatch.setattr(storage, "db_connection", lambda *args, **kwargs: _Connection([_row(invalid)]))

    with pytest.raises(ValueError, match=r"invalid scoped DB row at index 0.*chain_id"):
        storage.load_latest_liquidation_account_reports("postgresql://example")
