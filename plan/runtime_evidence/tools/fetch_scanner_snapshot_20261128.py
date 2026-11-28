#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch the runtime's scanner snapshot and save it as evidence.

The scanner snapshot endpoint rebuilds the whole route universe, so it can
take a minute or more while the runtime measures.  The fetch therefore runs
with a generous timeout and stores the raw body next to the other runtime
evidence, so the resident worker list can be read without asking the busy
runtime again.

Usage::

    python fetch_scanner_snapshot_20261128.py [--out report.json] [--timeout 600]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO / "plan" / "runtime_evidence" / "reports" / "runtime_scanner_snapshot_20261128.json"
RUNTIME_API = "http://127.0.0.1:5000"
SNAPSHOT_PATH = (
    "/api/dex/opportunity-scanner/snapshot?chain_id=4663"
    "&protocols=uniswap_v4&summary=1"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--runtime-api", default=RUNTIME_API)
    args = parser.parse_args()

    url = args.runtime_api.rstrip("/") + SNAPSHOT_PATH
    started = time.time()
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except Exception as error:  # noqa: BLE001 - the failure is the evidence
        payload = {"ok": False, "error": str(error), "url": url}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(body, encoding="utf-8", newline="\n")
    summary = {
        "ok": True,
        "status": status,
        "bytes": len(body),
        "seconds": round(time.time() - started, 3),
        "out": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
