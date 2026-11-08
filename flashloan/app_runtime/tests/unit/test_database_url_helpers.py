from core.db import storage_common
from core.db.database_path import default_chain_database_path


def test_database_url_helpers_resolve_local_kv_path(monkeypatch, tmp_path):
    kv_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("FLASHLOAN_KV_PATH", str(kv_path))

    assert storage_common.database_url() == str(kv_path.resolve())
    assert storage_common.configured_database_url() == str(kv_path.resolve())


def test_database_url_defaults_to_default_chain_database(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)

    assert storage_common.database_url() == str(default_chain_database_path().resolve())
