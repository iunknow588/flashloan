from core.db import storage_common


def test_database_url_helpers_resolve_local_kv_path(monkeypatch, tmp_path):
    kv_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("FLASHLOAN_KV_PATH", str(kv_path))

    assert storage_common.database_url_or_none() == str(kv_path.resolve())
    assert storage_common.configured_database_url() == str(kv_path.resolve())


def test_configured_database_url_uses_default_local_store(monkeypatch, tmp_path):
    monkeypatch.delenv("FLASHLOAN_KV_PATH", raising=False)
    monkeypatch.setattr(storage_common, "store_path", lambda path=None: tmp_path / "default.sqlite3")

    assert storage_common.configured_database_url() == str(tmp_path / "default.sqlite3")
