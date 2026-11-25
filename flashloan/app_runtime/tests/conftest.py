import sys
import os
import sqlite3
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1]
FLASHLOAN_ROOT = Path(__file__).resolve().parents[2]
SPLIT_SRC_ROOT = FLASHLOAN_ROOT / "src"
if str(FLASHLOAN_ROOT) not in sys.path:
    sys.path.insert(0, str(FLASHLOAN_ROOT))
if str(SPLIT_SRC_ROOT) not in sys.path:
    sys.path.insert(1, str(SPLIT_SRC_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.append(str(SRC_ROOT))


from app_runtime.tests.live_chain_store_guard import (  # noqa: E402
    is_live_chain_store,
    install_rust_write_guard,
    install_store_path_redirect,
    live_chain_store_root,
    open_live_chain_store_read_only,
)


@pytest.fixture(autouse=True)
def _isolate_working_directory(tmp_path_factory):
    """Keep every test out of the repository working directory.

    A relative ``database_url`` (for example ``"local_store.sqlite3"``)
    resolves against the current working directory.  Without this fixture a
    test that used a relative path dropped an orphan SQLite file into the
    repository root, which then failed the repository-root architecture guard
    and could be picked up as a spurious store by a later run.  Pinning the
    working directory to a per-session temporary directory keeps such files
    inside the test sandbox.

    Tests that need a specific working directory override this by calling
    ``monkeypatch.chdir`` themselves; ``monkeypatch.chdir`` restores the
    original directory when the test finishes.
    """

    sandbox = tmp_path_factory.mktemp("cwd")
    original = Path.cwd()
    os.chdir(sandbox)
    try:
        yield sandbox
    finally:
        os.chdir(original)


@pytest.fixture(autouse=True)
def _reset_dynamic_rpc_offline_state():
    """Keep the dynamic RPC offline state out of the next test.

    ``rpc_gate.mark_v4_rpc_offline`` records a chain as offline after a real
    RPC probe failed.  That state is a module global, so a test that reaches
    the network once (and fails, because the test environment has no network)
    would silently mark the chain offline for every later test in the same
    session.  The effect is a whole group of unrelated tests failing with an
    empty result instead of their own assertion.

    The reset runs before and after each test, so a test may still exercise
    the offline transition itself and observes exactly its own state.
    """

    from dex.intent_trade.chains.robinhood_common import rpc_gate

    def _reset() -> None:
        rpc_gate._OFFLINE_REASONS.clear()

    _reset()
    try:
        yield
    finally:
        _reset()


@pytest.fixture(autouse=True)
def _isolate_flashloan_kv_path():
    """Keep a chain-scoped database override out of the next test.

    ``flashloan/app_runtime/.env`` ships a ``FLASHLOAN_KV_PATH`` that points at
    one concrete chain database, and ``core.env_loader.load_env_files`` copies
    that value into ``os.environ``.  Because the value names a single file, a
    test that resolves the *other* chain would read the wrong database and see
    an empty result instead of its own assertion.

    The per-chain database registry is the only source of truth for tests, so
    this fixture removes the environment override for the duration of every
    test.  A test that needs a specific database sets the variable itself with
    ``monkeypatch``, which restores the previous state afterwards.
    """

    key = "FLASHLOAN_KV_PATH"
    previous = os.environ.pop(key, None)
    try:
        yield
    finally:
        os.environ.pop(key, None)
        if previous is not None:
            os.environ[key] = previous


@pytest.fixture(scope="session", autouse=True)
def _sandbox_implicit_default_store(tmp_path_factory):
    """Point the process default store at a sandbox file.

    Several production read paths resolve "the" store without naming it
    (``bot/page_state/store.py`` asks ``storage_common.database_url()``).
    The registry default chain is the live mainnet store, so a test that
    writes a page state would otherwise overwrite the production control
    panel state - the two production pause guards included.

    The redirect sits at the single root of the path resolution
    (``core.db.database_path.resolve_database_path``) so it covers every
    consumer: the KV store, the business tables, the page payload helpers
    and the Rust bridge, which resolves its database path the same way.

    Three targets stay untouched:

    * a path the test passes **explicitly** - the caller named a store;
    * a **bound chain scope** - the pages that read a real chain (the flash
      loan pool page reads the borrow asset universe of a live chain) must
      still see the production rows;
    * a store that is not a live chain store.

    The sandbox carries the same schema as the live store, so a read of a
    KV table returns an empty result instead of failing on a missing table.
    """

    from core.db.storage_schema import ensure_database_schema

    sandbox = tmp_path_factory.mktemp("default_store") / "local_store_sandbox.sqlite3"
    ensure_database_schema(str(sandbox))

    with pytest.MonkeyPatch.context() as patch:
        install_store_path_redirect(sandbox, patch)
        install_rust_write_guard(sandbox, patch)
        yield sandbox


@pytest.fixture(autouse=True)
def _guard_real_chain_databases(monkeypatch: pytest.MonkeyPatch):
    """Block writes into the live mainnet and testnet databases.

    ``flashloan/db`` holds the live mainnet and testnet stores.  Several
    code paths resolve their target database from the process default chain
    when no explicit scope is bound, and the process default chain is the
    mainnet chain.  A test that reaches such a path therefore writes its
    fixture rows straight into a production database, which silently
    corrupts the closed-loop evidence that the runtime later reads back.

    The guard is installed on the **connections** instead of on the write
    helpers.  Modules that import the write helpers by name
    (``core/db/builders.py`` imports ``delete_rows`` and ``query`` directly)
    keep their own binding, so a guard that only wrapped the functions in
    ``storage_business_io`` could be bypassed by a single import statement.

    A live chain store is opened with the read-only mode.  Every write then
    fails inside SQLite, while the read paths that legitimately inspect the
    live chain metadata keep working.
    """
    from core import local_kv
    from core.db.database import Database

    original_connect = Database.connect
    original_local_connect = local_kv._connect
    original_sqlite3_connect = sqlite3.connect

    def _guarded_connect(self):
        if is_live_chain_store(self.path):
            return open_live_chain_store_read_only(self.path)
        return original_connect(self)

    def _guarded_local_connect(path):
        if is_live_chain_store(path):
            # The schema initialiser issues DDL, so a live chain store is
            # opened without it.
            return open_live_chain_store_read_only(path)
        return original_local_connect(path)

    def _guarded_sqlite3_connect(database, *args, **kwargs):
        # The backstop for the modules that open a store directly instead of
        # going through one of the two connection factories.
        if is_live_chain_store(database):
            timeout = kwargs.get("timeout", 15.0)
            return open_live_chain_store_read_only(
                database,
                timeout=float(timeout or 15.0),
            )
        return original_sqlite3_connect(database, *args, **kwargs)

    monkeypatch.setattr(Database, "connect", _guarded_connect)
    monkeypatch.setattr(local_kv, "_connect", _guarded_local_connect)
    monkeypatch.setattr(sqlite3, "connect", _guarded_sqlite3_connect)


@pytest.fixture(autouse=True)
def _reset_chain_runtime_registry():
    """Keep a chain-to-database binding out of the next test.

    ``core.runtime.chain_runtime.chain_runtime_registry`` is a process-global
    singleton that maps a chain id to one concrete database.  The first test
    that registers a chain therefore pins that chain to its own temporary
    database for the rest of the session, and any later test that expects a
    different database for the same chain silently reads the stale one.

    Resetting the registry between tests keeps the chain-to-database binding
    scoped to the test that created it.  A test that needs a specific chain
    binding registers it itself, and the next test starts from a clean
    registry again.
    """

    from core.runtime.chain_runtime import reset_chain_runtime_registry

    reset_chain_runtime_registry()
    try:
        yield
    finally:
        reset_chain_runtime_registry()


@pytest.fixture(autouse=True)
def _clear_dex_auto_execute_env():
    key = "DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES"
    previous = os.environ.get(key)
    os.environ.pop(key, None)
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@pytest.fixture(autouse=True)
def _clear_rust_closed_loop_flag():
    key = "DEX_CLOSED_LOOP_NATIVE_RUST"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@pytest.fixture(autouse=True)
def _disable_background_dex_opportunity_threads():
    key = "DEX_OPPORTUNITY_SCANNER_BACKGROUND_ENABLED"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@pytest.fixture(autouse=True)
def _disable_dex_rpc_rate_limit():
    key = "DEX_RPC_MIN_INTERVAL_SECONDS"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    try:
        from dex.intent_trade.chains.robinhood_common.rpc_rate_limit import (
            reset_dex_rpc_rate_limit_for_tests,
        )

        reset_dex_rpc_rate_limit_for_tests()
        yield
    finally:
        reset_dex_rpc_rate_limit_for_tests()
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


@pytest.fixture(autouse=True)
def _stop_global_dex_opportunity_scanner():
    yield
    try:
        from dex.runtime.dex_opportunity_scanner import (
            _reset_dex_opportunity_scanner_for_tests,
        )

        _reset_dex_opportunity_scanner_for_tests()
    except Exception:
        pass
