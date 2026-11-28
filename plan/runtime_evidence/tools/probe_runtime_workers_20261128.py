#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the resident worker list the runtime scanner reports.

The healer resolves a route to the group key its worker runs under.  It
reads that list from the scanner snapshot.  The summary flavour of the
snapshot may omit the worker list, and then every group looks
unregistered -- which is a **reading** problem, not a production one: the
runtime still answers some routes with closed_loop_group_busy, so their
workers exist.

This probe reads the snapshot in both flavours and prints where the worker
list is found and how many keys it holds.

Usage:
    python probe_runtime_workers_20261128.py [--runtime-api URL]
"""

from __future__ import annotations

import argparse
import json
import urllib.request


def fetch(base: str, path: str, timeout: int = 120) -> dict:
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout) as response:
            return {"ok": True, "status": response.status,
                    "payload": json.loads(response.read().decode("utf-8", errors="replace"))}
    except Exception as error:  # noqa: BLE001 - the probe reports every failure
        return {"ok": False, "error": str(error)}


def find_worker_blocks(node, path: str = "", found: list | None = None) -> list:
    """Return every block that looks like a resident worker list."""
    if found is None:
        found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if "worker" in str(key).lower():
                found.append({"path": here, "type": type(value).__name__,
                              "size": len(value) if isinstance(value, (list, dict)) else None,
                              "sample": value[:3] if isinstance(value, list) else
                              (list(value)[:5] if isinstance(value, dict) else None)})
            find_worker_blocks(value, here, found)
    elif isinstance(node, list):
        for index, value in enumerate(node[:3]):
            find_worker_blocks(value, f"{path}[{index}]", found)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-api", default="http://127.0.0.1:5000")
    args = parser.parse_args()

    report = {}
    for flavour, path in (
        ("summary", "/api/dex/opportunity-scanner/snapshot"
                     "?chain_id=4663&protocols=uniswap_v4&summary=1"),
        ("full", "/api/dex/opportunity-scanner/snapshot"
                 "?chain_id=4663&protocols=uniswap_v4"),
    ):
        response = fetch(args.runtime_api, path)
        entry = {"ok": response.get("ok"), "status": response.get("status")}
        if response.get("ok"):
            payload = response.get("payload") or {}
            scanner = (payload.get("scanner") or {}) if isinstance(payload, dict) else {}
            entry["scannerKeys"] = sorted(scanner)[:40] if isinstance(scanner, dict) else []
            entry["workerBlocks"] = find_worker_blocks(payload)[:12]
        else:
            entry["error"] = response.get("error")
        report[flavour] = entry
    print(json.dumps(report, ensure_ascii=False, indent=2)[:8000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
