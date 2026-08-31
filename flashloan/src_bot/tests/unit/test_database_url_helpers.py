import pytest

from core.env_loader import disable_database_alias_enabled, local_json_only_enabled
from db import storage_common


def test_database_url_helpers_respect_local_json_only_and_alias(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.delenv("FLASHLOAN_LOCAL_JSON_ONLY", raising=False)
    monkeypatch.setenv("FLASHLOAN_DISABLE_DATABASE", "true")

    assert storage_common.database_url_or_none() is None
    assert disable_database_alias_enabled() is True
    assert local_json_only_enabled() is True
    with pytest.raises(RuntimeError, match="disabled"):
        storage_common.configured_database_url()


def test_configured_database_url_raises_when_missing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("FLASHLOAN_LOCAL_JSON_ONLY", raising=False)
    monkeypatch.delenv("FLASHLOAN_DISABLE_DATABASE", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL is missing"):
        storage_common.configured_database_url()
