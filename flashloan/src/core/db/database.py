"""Process-local handle for the single authoritative SQLite store.

Every component resolves the default chain SQLite file (currently
``local_store_4663.sqlite3``) through
``core.db.database_path.resolve_database_path`` so the web process, CLI tools,
and the Rust read-only bridge agree on the same file.  Requests with a chain
scope resolve to that chain's file, and the registry default chain provides
the implicit fallback.  Connections stay short-lived by design: SQLite/WAL
already supports concurrent processes, and no single Python process should
act as a persistent proxy for the database file.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from core.db.database_path import resolve_database_path

DEFAULT_CONNECT_TIMEOUT_SECONDS = 15.0
DEFAULT_BUSY_TIMEOUT_MS = 15000
_JOURNAL_MODE_INIT_LOCK = threading.Lock()
_JOURNAL_MODE_INITIALIZED_PATHS: set[str] = set()


@dataclass(frozen=True)
class Database:
    """Resolved database path plus a uniform connection factory."""

    path: Path
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS

    @classmethod
    def from_url(
        cls,
        database_url: str | os.PathLike[str] | None = None,
    ) -> Database:
        return cls(resolve_database_path(database_url))

    def ensure(self) -> Path:
        from core.local_kv import ensure_store

        return ensure_store(self.path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=max(1.0, float(self.connect_timeout)),
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        connection.execute("PRAGMA synchronous=NORMAL")
        path_key = str(self.path.resolve())
        if path_key not in _JOURNAL_MODE_INITIALIZED_PATHS:
            # The SQLite write itself carries its own busy timeout.  Holding
            # the Python lock around it would queue every other connection on
            # one path while SQLite waits for a writer.
            connection.execute("PRAGMA journal_mode=WAL")
            with _JOURNAL_MODE_INIT_LOCK:
                _JOURNAL_MODE_INITIALIZED_PATHS.add(path_key)
        return connection

    def connect_read_only(self) -> sqlite3.Connection:
        """Open a strict read-only connection that never sets WAL.

        Dry-run planning and online read-only checks must not create WAL files
        or touch the database header.  The ``mode=ro`` URI rejects every write
        at the SQLite layer before it can reach the file.
        """
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=ro",
            uri=True,
            timeout=max(1.0, float(self.connect_timeout)),
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def database(
    database_url: str | os.PathLike[str] | None = None,
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Database:
    """Return a ``Database`` handle for the authoritative store."""

    return Database(
        resolve_database_path(database_url),
        connect_timeout=connect_timeout,
        busy_timeout_ms=busy_timeout_ms,
    )


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "Database",
    "database",
    "resolve_database_path",
]
