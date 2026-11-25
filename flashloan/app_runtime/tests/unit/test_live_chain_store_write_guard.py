"""Guards the live chain store against writes from the test suite.

§flashloan/db§ holds the live mainnet and testnet stores.  Several
production read paths resolve their target database from the process default
chain when no explicit scope is bound, and the process default chain is the
mainnet chain.  A test that reaches such a path used to write its fixture
rows straight into the production store, which reset the two production
pause guards to their default values.

The guard now sits on the **connections**: a live chain store is opened with
the read-only mode, so every write fails inside SQLite.  These tests pin
that behaviour, prove that the guard is not vacuous, and prove that the
legitimate read paths keep working.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app_runtime.tests import live_chain_store_guard
from core import local_kv
from core.db import builders, storage_business_io


LIVE_ROOT = live_chain_store_guard.live_chain_store_root()


def _seed_live_store(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS kv_entries ("
            "collection TEXT NOT NULL, "
            "key TEXT NOT NULL, "
            "value_json TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "PRIMARY KEY (collection, key))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS kv_meta ("
            "meta_key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS dex_candidate_anchor_tokens ("
            "id INTEGER PRIMARY KEY)"
        )
        connection.commit()
    finally:
        connection.close()


def _fake_live_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a store that the guard has to treat as a live chain store."""

    live_root = tmp_path / "live"
    live_root.mkdir()
    store = live_root / "local_store_4663.sqlite3"
    _seed_live_store(store)
    monkeypatch.setattr(
        live_chain_store_guard,
        "live_chain_store_root",
        lambda: live_root,
    )
    return store


def test_the_guard_blocks_a_write_into_a_live_chain_store(tmp_path, monkeypatch):
    store = _fake_live_store(tmp_path, monkeypatch)

    with pytest.raises(sqlite3.OperationalError):
        local_kv.put(store, "parameters", "page_state|execution", '{"status":"ok"}')

    assert local_kv.count(store, "parameters") == 0


def test_the_guard_is_what_blocks_the_write(tmp_path, monkeypatch):
    """The same write succeeds once the guard no longer covers the store.

    Without this counter construction the assertion above would also pass
    when the write failed for an unrelated reason, and the guard could be
    removed without any test going red.
    """

    live_root = tmp_path / "live"
    live_root.mkdir()
    store = live_root / "local_store_4663.sqlite3"
    _seed_live_store(store)

    monkeypatch.setattr(
        live_chain_store_guard,
        "live_chain_store_root",
        lambda: tmp_path / "not-the-live-root",
    )

    local_kv.put(store, "parameters", "page_state|execution", '{"status":"ok"}')

    assert local_kv.count(store, "parameters") == 1


def test_the_guard_covers_a_by_name_imported_write_entry(tmp_path, monkeypatch):
    """§core/db/builders.py§ imports the write helpers by name.

    A guard that only wrapped the functions in §storage_business_io§ would
    be bypassed by that import, so the connection level guard has to cover
    the entry point as well.
    """

    store = _fake_live_store(tmp_path, monkeypatch)

    with pytest.raises(sqlite3.OperationalError):
        builders.delete_rows(str(store), "dex_candidate_anchor_tokens", "1 = 1", ())

    with pytest.raises(sqlite3.OperationalError):
        storage_business_io.execute(
            str(store),
            "INSERT INTO dex_candidate_anchor_tokens (id) VALUES (1)",
        )


def test_the_guard_leaves_the_read_paths_usable(tmp_path, monkeypatch):
    store = _fake_live_store(tmp_path, monkeypatch)

    connection = local_kv._connect(store)
    try:
        rows = connection.execute(
            "SELECT COUNT(*) FROM kv_entries"
        ).fetchall()
    finally:
        connection.close()

    assert rows[0][0] == 0
    assert local_kv.scan(store, "parameters") == []


def test_the_implicit_default_store_never_resolves_to_a_live_chain_store():
    """The default store of the test process is a sandbox, not production."""

    from core.db import storage_common

    assert not live_chain_store_guard.is_live_chain_store(storage_common.database_url())
    assert not live_chain_store_guard.is_live_chain_store(local_kv.store_path(None))


def test_the_real_live_chain_store_stays_read_only():
    """The production store must refuse a write through the guarded path.

    The statement runs inside a transaction that is never committed, so a
    regression in the guard cannot leave a row behind in production.
    """

    live_store = LIVE_ROOT / "local_store_4663.sqlite3"
    if not live_store.exists():
        pytest.skip("the live mainnet chain store is not present")

    connection = local_kv._connect(live_store)
    try:
        before = int(
            connection.execute(
                "SELECT COUNT(*) FROM kv_entries "
                "WHERE collection = ? AND key = ?",
                ("parameters", "guard_probe"),
            ).fetchone()[0]
        )
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "INSERT INTO kv_entries (collection, key, value_json, updated_at) "
                "VALUES ('parameters', 'guard_probe', '{}', 'probe')"
            )
        after = int(
            connection.execute(
                "SELECT COUNT(*) FROM kv_entries "
                "WHERE collection = ? AND key = ?",
                ("parameters", "guard_probe"),
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert after == before


def test_the_real_live_chain_store_stays_readable():
    """The guard must not take the legitimate read paths down with it."""

    live_store = LIVE_ROOT / "local_store_4663.sqlite3"
    if not live_store.exists():
        pytest.skip("the live mainnet chain store is not present")

    rows = local_kv.scan(live_store, "parameters", limit=1)

    assert isinstance(rows, list)


def test_the_path_redirect_reaches_the_rust_bridge():
    """The Rust bridge resolves its store the same way Python does.

    The Rust extension opens the file itself, so the Python connection guard
    cannot see it.  The redirect has to sit at the path resolution root for
    the Rust bridge to stay off the production store as well.
    """

    from core import rs_bridge

    resolved = Path(rs_bridge._database_path(None))

    assert not live_chain_store_guard.is_live_chain_store(resolved)


def test_the_path_redirect_keeps_an_explicit_path(monkeypatch):
    """A store the test names explicitly is never rewritten."""

    from core.db import database_path as database_path_module

    explicit = LIVE_ROOT / "local_store_4663.sqlite3"
    redirect = live_chain_store_guard.make_store_path_redirect(
        database_path_module.resolve_database_path,
        LIVE_ROOT / "sandbox.sqlite3",
    )

    assert Path(redirect(str(explicit))) == Path(explicit).resolve()


def test_the_path_redirect_keeps_a_bound_chain_scope():
    """A bound chain scope keeps pointing at that chain's own store.

    The pages that read a real chain must still see the production rows.
    """

    from core.db.chain_database_context import chain_database_scope
    from core.db import storage_common

    with chain_database_scope(4663):
        scoped = storage_common.database_url()

    assert live_chain_store_guard.is_live_chain_store(scoped)


def test_the_rust_write_guard_refuses_a_live_chain_store(tmp_path, monkeypatch):
    """A Rust write into a live chain store fails loudly.

    The Rust extension opens the store read-write on the Rust side, so the
    Python connection guard never sees it.  The write guard has to refuse it
    instead of letting it land in production.
    """

    from core import rs_bridge

    store = _fake_live_store(tmp_path, monkeypatch)

    with pytest.raises(live_chain_store_guard.LiveChainStoreWriteError):
        rs_bridge.ensure_database_schema(str(store))

    with pytest.raises(live_chain_store_guard.LiveChainStoreWriteError):
        rs_bridge.migrate_direction_schema(str(store))


def test_the_rust_write_guard_lets_a_sandbox_write_through(tmp_path, monkeypatch):
    """The write guard only refuses live stores, not temporary ones."""

    from core import rs_bridge

    store = tmp_path / "sandbox.sqlite3"

    payload = rs_bridge.ensure_database_schema(str(store))

    assert payload["ok"] is True


def test_no_module_keeps_the_unredirected_path_resolver():
    """The redirect has no missed binding point.

    A module that imported §resolve_database_path§ by name holds the
    function object itself, so patching the source module would not reach
    it.  The redirect rebinds those aliases; this test proves that no
    module is left holding the original object.
    """

    import sys

    original = live_chain_store_guard.ORIGINAL_RESOLVE[0]
    if original is None:
        pytest.skip("the path redirect is not installed in this session")

    stale = []
    for module_name, module in list(sys.modules.items()):
        if module is None:
            continue
        try:
            members = list(vars(module).items())
        except Exception:
            continue
        for attribute, value in members:
            if attribute.startswith("__"):
                continue
            if value is original:
                stale.append("%s.%s" % (module_name, attribute))

    assert not stale


def test_the_escape_hatch_restores_the_resolution_but_not_the_write_protection():
    """The escape hatch restores the path, never the write permission.

    Two product rule tests have to observe the production default
    resolution.  If the escape hatch also lifted the write protection, a
    test could use it to write production - that is exactly the defect this
    task removes.
    """

    from core.db import storage_common

    with live_chain_store_guard.unguarded_default_store():
        resolved = storage_common.database_url()
        assert live_chain_store_guard.is_live_chain_store(resolved)
        with pytest.raises(sqlite3.OperationalError):
            local_kv.put(
                Path(resolved),
                "parameters",
                "page_state|escape_hatch_probe",
                '{"status":"probe"}',
            )

    assert not live_chain_store_guard.is_live_chain_store(storage_common.database_url())
