from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.local_kv import ensure_store, put


def run_benchmark(operations: int = 200, connect_delay_ms: float = 2.0) -> dict:
    """Benchmark local SQLite KV writes; the legacy connection pool is gone."""

    operations = max(1, int(operations))
    delay = max(0.0, float(connect_delay_ms)) / 1000.0
    import tempfile

    kv_path = os.path.join(tempfile.gettempdir(), "local_store_benchmark.sqlite3")
    ensure_store(kv_path)
    started = time.perf_counter()
    for index in range(operations):
        if delay:
            time.sleep(delay)
        put(kv_path, "benchmark", f"row|{index}", {"index": index, "nested": {"ok": True}})
    total_seconds = time.perf_counter() - started
    return {
        "operations": operations,
        "connect_delay_ms": connect_delay_ms,
        "direct_seconds": total_seconds,
        "pooled_seconds": total_seconds,
        "improvement_percent": 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local SQLite KV write benchmark.")
    parser.add_argument("--operations", type=int, default=200)
    parser.add_argument("--connect-delay-ms", type=float, default=2.0)
    parser.add_argument("--min-improvement-percent", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(args.operations, args.connect_delay_ms)
    print(
        "operations={operations} direct={direct_seconds:.3f}s pooled={pooled_seconds:.3f}s "
        "improvement={improvement_percent:.1f}%".format(**result)
    )
    if result["improvement_percent"] < args.min_improvement_percent:
        raise SystemExit(
            f"improvement below {args.min_improvement_percent:.1f}% "
            "(legacy connection pool is disabled; local KV writes are the baseline)"
        )


if __name__ == "__main__":
    main()
