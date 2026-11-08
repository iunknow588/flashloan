#!/usr/bin/env python3
"""Measure Robinhood DEX page API warm/cold latency and write evidence.

The accepted performance gate is warm (cache-hit) latency under 1 second for
the static page surfaces.  ``/api/dex/quote-routes`` performs live on-chain
merges, so it is recorded as a baseline-only row and is not part of the gate.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "flashloan"
    / "src"
    / "runtime"
    / "evidence"
    / "robinhood-testnet"
    / "page_perf_latest.json"
)

TARGET_ENDPOINTS = [
    ("dex_pool_candidates", "/api/dex-pools/candidates"),
    ("dex_overview", "/api/dex/overview"),
    ("dex_submissions", "/api/dex/submissions"),
    ("dex_market_observation", "/api/dex/market-observation"),
    ("dex_quote_routes_static", "/api/dex/quote-routes/static"),
]
BASELINE_ENDPOINTS = [
    ("dex_quote_routes_live", "/api/dex/quote-routes"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--chain-id", type=int, default=46630)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when any accepted static surface warm is >= 1.0s",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


def measure_once(
    base_url: str,
    path: str,
    chain_id: int,
    timeout: float,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({"chain_id": str(chain_id)})
    url = f"{base_url.rstrip('/')}{path}?{query}"
    started = time.perf_counter()
    error: str | None = None
    status: int | None = None
    body_bytes: int | None = None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = int(response.status)
            payload = response.read()
            body_bytes = len(payload)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            body_bytes = len(exc.read())
        except Exception:
            body_bytes = 0
        error = f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    return {
        "status": status,
        "elapsedSeconds": round(elapsed, 4),
        "bodyBytes": body_bytes,
        "error": error,
        "url": url,
    }


def main() -> int:
    args = parse_args()
    rows: list[dict[str, Any]] = []
    accepted_targets = [name for name, _ in TARGET_ENDPOINTS]
    baseline_targets = [name for name, _ in BASELINE_ENDPOINTS]

    for name, path in TARGET_ENDPOINTS + BASELINE_ENDPOINTS:
        cold = measure_once(args.base_url, path, args.chain_id, args.timeout)
        warm = measure_once(args.base_url, path, args.chain_id, args.timeout)
        rows.append(
            {
                "page": name,
                "url": path,
                "acceptedTarget": name in accepted_targets,
                "cold": cold,
                "warm": warm,
            }
        )

    output = {
        "schema": "robinhood-page-perf",
        "network": "robinhood-testnet",
        "chainId": args.chain_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "server": args.base_url,
        "method": "real HTTP first request (cold) vs immediate repeat (warm/cache-hit)",
        "target": {
            "warmCacheHitSecondsLt": 1.0,
            "acceptedSurfaces": accepted_targets,
            "baselineOnlySurfaces": baseline_targets,
        },
        "rows": rows,
        "warmAllStaticUnderOneSecond": all(
            row["acceptedTarget"]
            and row["warm"].get("error") is None
            and row["warm"].get("status") == 200
            and row["warm"].get("elapsedSeconds") is not None
            and row["warm"]["elapsedSeconds"] < 1.0
            for row in rows
            if row["acceptedTarget"]
        ),
        "note": (
            "/api/dex/quote-routes is a live on-chain verification endpoint; "
            "it is recorded as a baseline only and is not part of the P2-1 "
            "warm <1s acceptance gate."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.output}")
    for row in rows:
        warm = row["warm"]
        status = "OK" if warm.get("status") == 200 else "FAIL"
        print(
            f"{row['page']}: warm={warm.get('elapsedSeconds')}s "
            f"cold={row['cold'].get('elapsedSeconds')}s {status}"
        )

    if args.strict and not output["warmAllStaticUnderOneSecond"]:
        print("strict gate failed: at least one accepted static surface >= 1.0s")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
