from __future__ import annotations

import threading
import time
import uuid
from statistics import median
from typing import Any

from db.storage_common import db_connection, run_db_transaction


def run_live_contention(
    database_url: str,
    *,
    workers: int = 4,
    operations_per_worker: int = 5,
    hold_ms: float = 10.0,
) -> dict[str, Any]:
    """Use an isolated table to measure row-lock waits and transaction errors."""
    table = f"codex_db_contention_{uuid.uuid4().hex[:12]}"
    workers = max(1, int(workers))
    operations = max(1, int(operations_per_worker))
    durations: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)')
            cursor.execute(f'INSERT INTO "{table}" (id, value) VALUES (1, 0)')

    def worker() -> None:
        for _ in range(operations):
            started = time.perf_counter()
            try:
                def contend(connection: object) -> None:
                    with connection.cursor() as cursor:
                        cursor.execute(f'SELECT value FROM "{table}" WHERE id = 1 FOR UPDATE')
                        row = cursor.fetchone() or (0,)
                        cursor.execute(f'UPDATE "{table}" SET value = %s WHERE id = 1', (int(row[0]) + 1,))
                        time.sleep(max(0.0, float(hold_ms)) / 1000.0)

                run_db_transaction(
                    database_url,
                    "validation.db_contention",
                    contend,
                    connect_timeout=8,
                    max_retries=2,
                    retry_backoff_ms=25,
                )
                with lock:
                    durations.append((time.perf_counter() - started) * 1000.0)
            except Exception as exc:
                with lock:
                    errors.append(str(exc)[:240])

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
    ordered = sorted(durations)
    return {
        "status": "calculated" if not errors else "completed_with_errors",
        "workers": workers,
        "operations_per_worker": operations,
        "hold_ms": float(hold_ms),
        "total_operations": workers * operations,
        "completed_operations": len(durations),
        "error_count": len(errors),
        "errors": errors[:10],
        "elapsed_ms": elapsed_ms,
        "lock_wait_ms": {
            "p50": median(ordered) if ordered else None,
            "p95": ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))] if ordered else None,
            "max": max(ordered) if ordered else None,
        },
    }
