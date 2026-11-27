"""The business write helpers must wait for the write lock, not lose it.

The store is a multi-gigabyte WAL database whose checkpoint can hold the
write lock for a while.  A writer that gives up early loses a closed-loop
state transition, so the helpers take the write lock up front and wait for
their turn.
"""

import sqlite3
import threading
import time

from core.db import storage_business_io


def _store(tmp_path):
    path = tmp_path / "write_lock.sqlite3"
    connection = sqlite3.connect(str(path))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT)")
    connection.commit()
    connection.close()
    return str(path)


def test_a_writer_waits_for_the_lock_instead_of_failing_at_once(tmp_path):
    path = _store(tmp_path)

    holder = sqlite3.connect(path, isolation_level=None, timeout=1)
    holder.execute("PRAGMA busy_timeout=100")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO notes (body) VALUES ('holder')")

    failures: list[BaseException] = []

    def _write() -> None:
        try:
            storage_business_io.execute(path, "INSERT INTO notes (body) VALUES ('waiter')")
        except BaseException as error:  # noqa: BLE001 - the test reports it
            failures.append(error)

    worker = threading.Thread(target=_write)
    worker.start()
    time.sleep(1.5)
    assert worker.is_alive(), "the writer gave up while the lock was still held"

    holder.execute("COMMIT")
    holder.close()
    worker.join(timeout=60)

    assert not failures, failures
    reader = sqlite3.connect(path)
    try:
        rows = [row[0] for row in reader.execute("SELECT body FROM notes ORDER BY id")]
    finally:
        reader.close()
    assert rows == ["holder", "waiter"]


def test_a_business_write_commits(tmp_path):
    path = _store(tmp_path)

    changed = storage_business_io.execute(path, "INSERT INTO notes (body) VALUES ('committed')")

    assert changed == 1
    reader = sqlite3.connect(path)
    try:
        rows = [row[0] for row in reader.execute("SELECT body FROM notes")]
    finally:
        reader.close()
    assert rows == ["committed"]


def test_a_business_transaction_commits_every_statement(tmp_path):
    path = _store(tmp_path)

    count = storage_business_io.transaction(
        path,
        [
            ("INSERT INTO notes (body) VALUES ('one')", ()),
            ("INSERT INTO notes (body) VALUES ('two')", ()),
        ],
    )

    assert count == 2
    reader = sqlite3.connect(path)
    try:
        rows = [row[0] for row in reader.execute("SELECT body FROM notes ORDER BY id")]
    finally:
        reader.close()
    assert rows == ["one", "two"]

