#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump the resident workers' review-publish state from the live runtime.

The Rust publisher only writes a review generation when the accumulated
evidence covers the whole closed-loop catalog.  This probe reads the
runtime's own view of that accumulator (per worker ``last_review_publish``
and ``review_wait_ready``) so the blocking reason is observable instead of
guessed.

Read-only: it only performs GETs against the local runtime.

Usage:
    python probe_t401_publish_state_20261124.py [runtime_base]
"""

import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")


def get_json(url):
    with urllib.request.urlopen(url, timeout=120) as handle:
        return json.loads(handle.read().decode("utf-8", errors="replace"))


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
        "RUNTIME_BASE", "http://127.0.0.1:5000"
    )
    snapshot = get_json(base.rstrip("/") + "/api/dex/opportunity-scanner/snapshot")
    data = snapshot.get("data") or {}
    scanner = data.get("scanner") or snapshot.get("scanner") or {}
    rust = scanner.get("closed_loop_rust_workers") or {}
    workers = rust.get("workers") or []

    def walk(node, path="", depth=0, out=None):
        if out is None:
            out = []
        if depth > 4:
            return out
        if isinstance(node, dict):
            for key, value in node.items():
                child = path + "." + key if path else key
                if isinstance(value, (dict, list)):
                    out.append((child, type(value).__name__, len(value)))
                else:
                    out.append((child, type(value).__name__, value))
                walk(value, child, depth + 1, out)
        elif isinstance(node, list) and node:
            walk(node[0], path + "[]", depth + 1, out)
        return out

    rows = []
    for worker in workers:
        rows.append(
            {
                "groupKey": worker.get("groupKey") or worker.get("group_key"),
                "state": worker.get("state"),
                "phase": worker.get("phase"),
                "review_wait_ready": worker.get("review_wait_ready"),
                "last_review_publish": worker.get("last_review_publish"),
                "last_error": worker.get("last_error"),
                "last_phase_error": worker.get("last_phase_error"),
                "probe_count": worker.get("probe_count"),
                "queued_count": worker.get("queued_count"),
                "updated_at": worker.get("updated_at"),
                "hasLastResult": worker.get("last_result") is not None,
                "lastResultKeys": sorted((worker.get("last_result") or {}).keys()),
            }
        )

    supervisors = scanner.get("closed_loop_supervisors") or {}
    print(
        json.dumps(
            {
                "supervisorsEnabled": supervisors.get("enabled"),
                "supervisorBroadcast": supervisors.get("broadcast"),
                "supervisorReview": supervisors.get("review"),
                "pythonFallbackEnabled": supervisors.get("pythonFallbackEnabled"),
                "rustSummary": rust.get("summary"),
                "rustReviewQueue": rust.get("reviewQueue"),
                "rustMaintenance": rust.get("maintenance"),
                "rustContextCache": rust.get("contextCache"),
                "rustExecutionQueue": rust.get("executionQueue"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    for path, kind, size in walk(snapshot):
        low = path.lower()
        if low.count(".") <= 2 and any(
            token in low
            for token in ("worker", "publish", "review", "scanner", "supervisor", "closed_loop")
        ):
            print("%-70s %-6s %s" % (path, kind, size))
    print(json.dumps({"scannerKeys": sorted(scanner.keys())}, ensure_ascii=False, indent=2))
    print(json.dumps({"rustWorkerKeys": sorted(rust.keys())}, ensure_ascii=False, indent=2))
    print(json.dumps({"workerCount": len(rows)}, ensure_ascii=False, indent=2))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
