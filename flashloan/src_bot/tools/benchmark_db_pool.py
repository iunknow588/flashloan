from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from db import storage_common


class SyntheticConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SyntheticPoolConnection:
    def __enter__(self):
        return SyntheticConnection()

    def __exit__(self, exc_type, exc, tb):
        return False


def run_benchmark(operations: int = 200, connect_delay_ms: float = 2.0) -> dict:
    operations = max(1, int(operations))
    delay = max(0.0, float(connect_delay_ms)) / 1000.0
    original_require_psycopg = storage_common.require_psycopg
    original_require_psycopg_pool = storage_common.require_psycopg_pool
    original_pool_enabled = storage_common._pool_enabled

    class SyntheticPsycopg:
        @staticmethod
        def connect(database_url, connect_timeout=8):
            time.sleep(delay)
            return SyntheticConnection()

    class SyntheticConnectionPool:
        def __init__(self, **kwargs):
            time.sleep(delay)
            self.closed = False

        def connection(self):
            return SyntheticPoolConnection()

        def close(self):
            self.closed = True

    def run_direct() -> float:
        storage_common.close_connection_pools()
        storage_common.require_psycopg = lambda: SyntheticPsycopg
        storage_common.require_psycopg_pool = lambda: None
        storage_common._pool_enabled = lambda: False
        started = time.perf_counter()
        for _ in range(operations):
            with storage_common.db_connection("postgresql://synthetic"):
                pass
        return time.perf_counter() - started

    def run_pooled() -> float:
        storage_common.close_connection_pools()
        storage_common.require_psycopg = lambda: SyntheticPsycopg
        storage_common.require_psycopg_pool = lambda: SyntheticConnectionPool
        storage_common._pool_enabled = lambda: True
        started = time.perf_counter()
        for _ in range(operations):
            with storage_common.db_connection("postgresql://synthetic"):
                pass
        return time.perf_counter() - started

    try:
        direct_seconds = run_direct()
        pooled_seconds = run_pooled()
    finally:
        storage_common.close_connection_pools()
        storage_common.require_psycopg = original_require_psycopg
        storage_common.require_psycopg_pool = original_require_psycopg_pool
        storage_common._pool_enabled = original_pool_enabled

    improvement = 0.0 if direct_seconds <= 0 else (direct_seconds - pooled_seconds) / direct_seconds
    return {
        "operations": operations,
        "connect_delay_ms": connect_delay_ms,
        "direct_seconds": direct_seconds,
        "pooled_seconds": pooled_seconds,
        "improvement_percent": improvement * 100.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a synthetic database connection pool benchmark.")
    parser.add_argument("--operations", type=int, default=200)
    parser.add_argument("--connect-delay-ms", type=float, default=2.0)
    parser.add_argument("--min-improvement-percent", type=float, default=50.0)
    args = parser.parse_args()

    result = run_benchmark(args.operations, args.connect_delay_ms)
    print(
        "operations={operations} direct={direct_seconds:.3f}s pooled={pooled_seconds:.3f}s "
        "improvement={improvement_percent:.1f}%".format(**result)
    )
    if result["improvement_percent"] < args.min_improvement_percent:
        raise SystemExit(f"pool improvement below {args.min_improvement_percent:.1f}%")


if __name__ == "__main__":
    main()
