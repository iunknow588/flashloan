"""Measure how much chain traffic the configured RPC endpoints actually carry.

The review rate of the closed-loop pool is bounded by the RPC: a review is a
sequence of chain reads, and every read waits for a round trip.  Before the
review concurrency is raised, the endpoint has to be measured at the load the
new concurrency would create - otherwise the extra workers only produce
rejections instead of reviews.

Usage:
    python probe_rpc_capacity_20261126.py [concurrency ...]
"""

from __future__ import annotations

import concurrent.futures as futures
import json
import sys
import time
import urllib.request


ENDPOINTS = {
    "publicnode": "https://robinhood-rpc.publicnode.com",
    "quiknode": (
        "https://blissful-billowing-pine.robinhood-mainnet.quiknode.pro/"
        "f90239a7fb1d3a80d614bc2434716b9ffc7e117b"
    ),
    "mainnet_chain": "https://rpc.mainnet.chain.robinhood.com",
    "ordofi": "https://rpc.ordofi.network",
    "alchemy": "https://robinhood-mainnet.g.alchemy.com/v2/alch_X81ySO9Rq5_9fARKfxR_T",
}

PAYLOAD = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}).encode()


def call(url: str) -> tuple[float, str | None]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(
            url, data=PAYLOAD, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            response.read()
        return time.perf_counter() - started, None
    except Exception as error:  # noqa: BLE001 - the probe reports every failure
        return time.perf_counter() - started, f"{type(error).__name__}: {str(error)[:60]}"


def main() -> int:
    levels = [int(item) for item in sys.argv[1:]] or [1, 8, 16, 32]
    for name, url in ENDPOINTS.items():
        for level in levels:
            with futures.ThreadPoolExecutor(max_workers=level) as pool:
                started = time.perf_counter()
                results = list(pool.map(lambda _: call(url), range(level)))
                wall = time.perf_counter() - started
            latencies = sorted(item[0] for item in results)
            errors = [item[1] for item in results if item[1]]
            median = latencies[len(latencies) // 2] * 1000
            p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] * 1000
            print(
                f"{name:14s} conc={level:3d} wall={wall:6.2f}s rate={level / wall:6.2f}/s "
                f"median={median:7.1f}ms p95={p95:7.1f}ms errors={len(errors)}/{level} "
                f"{errors[0] if errors else ''}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
