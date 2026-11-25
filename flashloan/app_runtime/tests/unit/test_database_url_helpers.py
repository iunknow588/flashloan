from core.db import storage_common
from core.db.database_path import default_chain_database_path
from app_runtime.tests.live_chain_store_guard import unguarded_default_store


def test_database_url_helpers_resolve_local_kv_path(monkeypatch, tmp_path):
    kv_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("FLASHLOAN_KV_PATH", str(kv_path))

    assert storage_common.database_url() == str(kv_path.resolve())
    assert storage_common.configured_database_url() == str(kv_path.resolve())


def test_database_url_defaults_to_default_chain_database(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)

    # The test session redirects the unbound process default to a sandbox
    # store (the live chain store guard).  This test asserts the *product*
    # rule, so it observes the production resolution through the guard's
    # explicit escape hatch.  Writes stay blocked.
    with unguarded_default_store():
        resolved = storage_common.database_url()

    assert resolved == str(default_chain_database_path().resolve())
