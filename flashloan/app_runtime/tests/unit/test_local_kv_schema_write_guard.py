"""Guards the KV schema initialiser against writing to an unchanged store.

The core.local_kv module doubles as the single local SQLite database path, so
ensure_store runs against the live chain database as well.  Its schema
initialiser used to end with an unconditional INSERT OR REPLACE on kv_meta,
which turned every read-only access into a write transaction: the test suite
appended WAL frames to the production mainnet store simply by importing a
module that reads chain tokens.

The guard keeps the write out of the unchanged path while still creating the
schema on a fresh store and still refreshing a stale version.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from core import local_kv
from core.db import storage_business_io


REPO_ROOT = Path(__file__).resolve().parents[3]
SPLIT_SRC_ROOT = REPO_ROOT / "src"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> tuple[str, str]:
    """Digest the store and its write ahead log.

    Comparing the database file alone is not enough: a write that is still
    sitting in the write ahead log leaves the database file untouched.  On a
    live chain store that is exactly the failure mode this guard exists for,
    because the appended WAL frames are the write.
    """

    wal = path.with_name(path.name + "-wal")
    wal_digest = _digest(wal) if wal.exists() else "<absent>"
    return _digest(path), wal_digest


def _writes(statements: list[str]) -> list[str]:
    return [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def test_an_up_to_date_store_is_not_rewritten(tmp_path):
    store = tmp_path / "kv.sqlite3"
    connection = local_kv._connect(store)
    local_kv._ensure_schema(connection)
    connection.close()

    reopened = local_kv._connect(store)
    captured: list[str] = []
    reopened.set_trace_callback(captured.append)
    local_kv._ensure_schema(reopened)
    reopened.close()

    assert _writes(captured) == []


def test_a_fresh_store_still_receives_the_schema(tmp_path):
    store = tmp_path / "kv.sqlite3"
    connection = local_kv._connect(store)
    local_kv._ensure_schema(connection)
    connection.close()

    reopened = local_kv._connect(store)
    stored = reopened.execute(
        "SELECT value_json FROM kv_meta WHERE meta_key = 'schema_version'"
    ).fetchone()
    reopened.close()

    assert stored is not None
    assert stored[0] == json.dumps(local_kv._SCHEMA_VERSION)


def test_a_stale_schema_version_is_still_refreshed(tmp_path):
    store = tmp_path / "kv.sqlite3"
    connection = local_kv._connect(store)
    local_kv._ensure_schema(connection)
    connection.execute(
        "UPDATE kv_meta SET value_json = ? WHERE meta_key = 'schema_version'",
        ('"0"',),
    )
    connection.commit()
    local_kv._ensure_schema(connection)
    stored = connection.execute(
        "SELECT value_json FROM kv_meta WHERE meta_key = 'schema_version'"
    ).fetchone()
    connection.close()

    assert stored is not None
    assert stored[0] == json.dumps(local_kv._SCHEMA_VERSION)


def test_a_store_keeping_the_raw_number_is_not_rewritten(tmp_path):
    """Older stores hold the raw number instead of the JSON encoded string.

    The live mainnet store carries the raw form, so a comparison against the
    encoded payload alone would rewrite it on every access.
    """

    store = tmp_path / "kv.sqlite3"
    connection = local_kv._connect(store)
    local_kv._ensure_schema(connection)
    connection.execute(
        "UPDATE kv_meta SET value_json = ? WHERE meta_key = 'schema_version'",
        (str(local_kv._SCHEMA_VERSION),),
    )
    connection.commit()

    reopened = local_kv._connect(store)
    captured: list[str] = []
    reopened.set_trace_callback(captured.append)
    local_kv._ensure_schema(reopened)
    reopened.close()

    assert _writes(captured) == []


def test_a_fresh_process_reading_a_store_leaves_it_byte_identical(tmp_path):
    """Reproduces the pytest situation: a new process, an initialised store.

    The test suite runs in a new process, so the per-process schema memo is
    cold and the initialiser runs again.  It must not touch the file.
    """

    store = tmp_path / "kv.sqlite3"
    connection = local_kv._connect(store)
    local_kv._ensure_schema(connection)
    connection.close()
    before = _fingerprint(store)

    script = (
        "import sys\n"
        "from core.db import storage_business_io\n"
        "storage_business_io.query(sys.argv[1], 'SELECT count(*) FROM kv_meta')\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(SPLIT_SRC_ROOT)]
        + [entry for entry in [environment.get("PYTHONPATH")] if entry]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(store)],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(tmp_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert _fingerprint(store) == before
