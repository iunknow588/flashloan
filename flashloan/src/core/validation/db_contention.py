from __future__ import annotations

import threading
import time
import uuid
from statistics import median
from typing import Any

from core.local_kv import ensure_store, meta_get, meta_put


def run_live_contention(
    database_url: str,
    *,
    workers: int = 4,
    operations_per_worker: int = 5,
    hold_ms: float = 10.0,
) -> dict[str, Any]:
    """Use an isolated KV meta counter to measure local write contention."""
    counter_key = f"codex_db_contention_{uuid.uuid4().hex[:12]}"
    ensure_store(database_url)
    meta_put(database_url, counter_key, {"value": 0})
    workers = max(1, int(workers))
    operations = max(1, int(operations_per_worker))
    durations: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(operations):
            started = time.perf_counter()
            try:
                value = int((meta_get(database_url, counter_key) or {}).get("value") or 0)
                meta_put(database_url, counter_key, {"value": value + 1})
                time.sleep(max(0.0, float(hold_ms)) / 1000.0)
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
