#!/usr/bin/env python3
"""Measure whether the runtime API serves requests concurrently.

The healer fires a whole batch of live-review requests at once, so the
server has to be able to hold more than one request open.  The probe
times one request and then a batch of them, on a read-only route.
"""
import json
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def call(url: str) -> tuple[int, float, int]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            body = response.read()
        return response.status, time.perf_counter() - started, len(body)
    except Exception as error:  # noqa: BLE001 - the probe reports the failure
        return -1, time.perf_counter() - started, str(error)[:200]  # type: ignore[return-value]


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
    path = sys.argv[2] if len(sys.argv) > 2 else "/api/dex/closed-loop-groups?chain_id=4663"
    url = base + path
    single = call(url)
    workers = 8
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _index: call(url), range(workers)))
    batch_seconds = time.perf_counter() - started
    per_request = [item[1] for item in results]
    print(json.dumps({
        "url": url,
        "single": {"status": single[0], "seconds": round(single[1], 3), "bytes": single[2]},
        "batch": {
            "workers": workers,
            "wallSeconds": round(batch_seconds, 3),
            "perRequestSeconds": [round(value, 3) for value in per_request],
            "medianPerRequestSeconds": round(statistics.median(per_request), 3),
            "sumOfPerRequestSeconds": round(sum(per_request), 3),
            "statuses": [item[0] for item in results],
        },
        "concurrent": bool(batch_seconds < sum(per_request) * 0.75),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
