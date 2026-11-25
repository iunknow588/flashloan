"""Shared helpers for the live chain store write guard.

The guard belongs to the test tree because it protects the test suite from
the production chain stores.  The helpers live in their own module so that
§conftest.py§ and the guard's own test agree on one definition of "a live
chain store" and share one module instance, which is what makes the root
override in the test reach the guard itself.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path


FLASHLOAN_ROOT = Path(__file__).resolve().parents[2]
LIVE_CHAIN_STORE_SUFFIX = ".sqlite3"

#: The pre-redirect §resolve_database_path§ function object, kept in a tuple
#: so that the alias sweep cannot rebind it: the sweep matches by object
#: identity, and a bare attribute would hold the very function it looks for.
#: §install_store_path_redirect§ fills it in, and the guard's own test uses
#: it to prove that **no** module still holds the un-redirected resolver.
ORIGINAL_RESOLVE = (None,)

#: The redirect that §install_store_path_redirect§ installed.  It is kept so
#: that §unguarded_default_store§ can put the production resolution back.
REDIRECT_RESOLVE = (None,)


def live_chain_store_root() -> Path:
    """Return the directory that holds the live chain stores."""

    return (FLASHLOAN_ROOT / "db").resolve()


def is_live_chain_store(database_url) -> bool:
    """Tell whether a path points at a live chain store.

    The live stores are the per-chain SQLite files that sit directly inside
    §flashloan/db§.  A file inside a sub-directory of that root is not a
    chain store: the review JSONL shards and the broadcast transaction
    databases live there as well.
    """

    if database_url is None:
        return False
    if not isinstance(database_url, (str, Path, os.PathLike)):
        return False
    try:
        resolved = Path(str(database_url)).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return (
        resolved.parent == live_chain_store_root()
        and resolved.suffix == LIVE_CHAIN_STORE_SUFFIX
    )


def open_live_chain_store_read_only(
    database_url,
    *,
    timeout: float = 15.0,
) -> sqlite3.Connection:
    """Open a live chain store without any write capability.

    The read-only mode makes SQLite reject every write at the statement
    level, so a fixture row can never land in production even when the write
    reaches a path that no Python level guard covers.  The read paths that
    legitimately inspect the live chain metadata keep working.
    """

    resolved = Path(str(database_url)).expanduser().resolve()
    connection = sqlite3.connect(
        resolved.as_uri() + "?mode=ro",
        uri=True,
        timeout=timeout,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


#: Rust bridge entry points that open the store **read-write** on the Rust
#: side.  The Python level connection guard cannot see them: the Rust
#: extension opens the file itself with ``open_rw`` and never goes through
#: ``sqlite3.connect``.
#:
#: The value names the path resolver the entry point uses: ``chain`` for the
#: per-chain stores, ``bnb`` for the shared BNB store (which lives in the
#: same directory and is production data as well).
RUST_WRITE_FUNCTIONS = {
    "ensure_database_schema": "chain",
    "ensure_database_indexes": "chain",
    "migrate_direction_schema": "chain",
    "update_v4_scan_state": "chain",
    "merge_v4_pool_index": "chain",
    "build_v4_anchor_data": "chain",
    "import_v4_evidence": "chain",
    "prune_v4_evidence": "chain",
    "append_bnb_market_observation": "bnb",
    "append_bnb_market_snapshot": "bnb",
}


class LiveChainStoreWriteError(RuntimeError):
    """Raised when a test tries to write into a live chain store."""


def make_store_path_redirect(original_resolve, sandbox):
    """Build the ``resolve_database_path`` replacement used by the guard.

    The rule is deliberately narrow:

    * an **explicit** path keeps pointing where the caller asked - the
      caller named a store, and the connection guard still refuses to write
      into a live one;
    * a **bound chain scope** keeps pointing at that chain's store, because a
      test that means to read production rows (the flash loan pool page reads
      the borrow asset universe of a live chain) must still see them;
    * the **unbound default** that would land on a live chain store is the
      defect this task removes - the process default is the mainnet store,
      so a test that writes "the current store" writes production.  That
      target is redirected to the sandbox.
    """

    def _resolve(database_url=None):
        resolved = original_resolve(database_url)
        if database_url is not None:
            return resolved
        from core.db.chain_database_context import current_chain_scope

        if current_chain_scope() is not None:
            return resolved
        if is_live_chain_store(resolved):
            return Path(sandbox)
        return resolved

    return _resolve


def install_store_path_redirect(sandbox, patch):
    """Redirect the unbound default store to the sandbox.

    ``core.db.database_path.resolve_database_path`` is the single root of the
    path resolution.  Every consumer reaches it either directly or through
    ``absolute_database_path``, which delegates to it by module global
    lookup, so patching the root covers all of them - including the Rust
    bridge, which resolves its database path exactly the same way.

    Modules that imported the function by name keep their own binding, so
    every alias of the original function object is rebound as well.
    """

    from core.db import database_path as database_path_module

    original_resolve = database_path_module.resolve_database_path
    # Keep a reference so the guard's own test can prove that no module
    # still holds the pre-redirect function object.
    global ORIGINAL_RESOLVE
    ORIGINAL_RESOLVE = (original_resolve,)
    redirect = make_store_path_redirect(original_resolve, sandbox)
    global REDIRECT_RESOLVE
    REDIRECT_RESOLVE = (redirect,)
    patch.setattr(database_path_module, "resolve_database_path", redirect)

    replacements = {id(original_resolve): redirect}
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            members = list(vars(module).items())
        except Exception:
            continue
        for name, value in members:
            if name.startswith("__"):
                continue
            replacement = replacements.get(id(value))
            if replacement is not None:
                patch.setattr(module, name, replacement)
    return redirect


@contextmanager
def unguarded_default_store():
    """Put the **production** default resolution back for one block.

    A test that asserts the *product* rule for the default store - "the
    process default is the default chain's SQLite file" - has to observe the
    production resolution, because the test session deliberately redirects
    it to a sandbox.

    Only the **resolution** is restored.  The write protection does not look
    at the resolution: the connection guard still opens a live chain store
    read-only and the Rust write guard still refuses a live target, so a test
    that uses this block still cannot write production.
    """

    from core.db import database_path as database_path_module

    original = ORIGINAL_RESOLVE[0]
    redirect = REDIRECT_RESOLVE[0]
    if original is None or redirect is None:
        yield
        return

    restored = []
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            members = list(vars(module).items())
        except Exception:
            continue
        for name, value in members:
            if name.startswith("__"):
                continue
            if value is redirect:
                restored.append((module, name))
                setattr(module, name, original)
    database_path_module.resolve_database_path = original
    try:
        yield
    finally:
        database_path_module.resolve_database_path = redirect
        for module, name in restored:
            setattr(module, name, redirect)


def install_rust_write_guard(sandbox, patch):
    """Refuse Rust bridge writes that target a live chain store.

    The Python connection guard cannot protect the Rust side: the extension
    opens the file itself with ``open_rw``.  Every Rust write entry point
    therefore checks its target and fails loudly when that target is a live
    chain store, instead of writing production rows in silence.

    A write that targets the sandbox - the normal case after the path
    redirect - is untouched.
    """

    from core import rs_bridge

    def _target(kind, database_url):
        try:
            if kind == "bnb":
                return Path(rs_bridge._bnb_database_path(database_url))
            return Path(rs_bridge._database_path(database_url))
        except Exception:
            return None

    def _guard(name, kind, original):
        def _wrapped(*args, **kwargs):
            database_url = kwargs.get("database_url")
            if database_url is None and name in {
                "ensure_database_schema",
                "ensure_database_indexes",
                "migrate_direction_schema",
            }:
                database_url = args[0] if args else None
            target = _target(kind, database_url)
            if target is not None and is_live_chain_store(target):
                raise LiveChainStoreWriteError(
                    "refusing to write the live chain store %s through %s; "
                    "a test must use a temporary store"
                    % (target, name)
                )
            return original(*args, **kwargs)

        _wrapped.__name__ = getattr(original, "__name__", name)
        _wrapped.__doc__ = getattr(original, "__doc__", None)
        return _wrapped

    installed = []
    replacements = {}
    for name, kind in RUST_WRITE_FUNCTIONS.items():
        original = getattr(rs_bridge, name, None)
        if original is None:
            continue
        guarded = _guard(name, kind, original)
        patch.setattr(rs_bridge, name, guarded)
        replacements[id(original)] = guarded
        installed.append(name)

    # A module that imported a writer by name keeps its own binding, so the
    # guard has to reach those aliases as well.
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            members = list(vars(module).items())
        except Exception:
            continue
        for name, value in members:
            if name.startswith("__"):
                continue
            replacement = replacements.get(id(value))
            if replacement is not None and value is not replacement:
                patch.setattr(module, name, replacement)
    return installed


__all__ = [
    "FLASHLOAN_ROOT",
    "LiveChainStoreWriteError",
    "LIVE_CHAIN_STORE_SUFFIX",
    "RUST_WRITE_FUNCTIONS",
    "is_live_chain_store",
    "install_rust_write_guard",
    "install_store_path_redirect",
    "live_chain_store_root",
    "make_store_path_redirect",
    "open_live_chain_store_read_only",
    "unguarded_default_store",
]
