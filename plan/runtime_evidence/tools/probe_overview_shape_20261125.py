#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the shape of the live ``/api/dex/overview`` payload.

Read-only: the Manager API is only ever read over HTTP.
"""

from __future__ import annotations

import json
import sys
import urllib.request


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else (
        "http://127.0.0.1:5000/api/dex/overview?chainId=4663"
    )
    with urllib.request.urlopen(url, timeout=180) as response:
        payload = json.load(response)

    def walk(node, prefix="", depth=0):
        if depth > 2:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                label = f"{prefix}.{key}" if prefix else key
                if isinstance(value, (dict, list)):
                    size = len(value)
                    print(f"{label}: {type(value).__name__}[{size}]")
                    walk(value, label, depth + 1)
                else:
                    text = str(value)
                    print(f"{label}: {text[:120]}")
        elif isinstance(node, list):
            if node:
                walk(node[0], f"{prefix}[0]", depth + 1)

    if len(sys.argv) > 2 and sys.argv[2] == "--dump":
        print(json.dumps(payload.get("pipelineStatus"), ensure_ascii=False, indent=2))
        return 0
    if len(sys.argv) > 2 and sys.argv[2] == "--find":
        needle = sys.argv[3].lower()

        def search(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    label = f"{path}.{key}" if path else key
                    if needle in key.lower():
                        print(f"{label} = {json.dumps(value, ensure_ascii=False)[:400]}")
                    search(value, label)
            elif isinstance(node, list):
                for index, value in enumerate(node[:50]):
                    search(value, f"{path}[{index}]")

        search(payload)
        return 0
    if len(sys.argv) > 2 and sys.argv[2] == "--reviews":
        reviews = payload.get("data", {}).get("reviews", [])
        print("review count:", len(reviews))
        if reviews:
            print("review keys:", sorted(reviews[0].keys()))
            print(json.dumps(reviews[0], ensure_ascii=False, indent=2)[:2000])
        return 0
    if len(sys.argv) > 2 and sys.argv[2] == "--review-detail":
        reviews = payload.get("data", {}).get("reviews", [])
        for review in reviews[:2]:
            print(json.dumps(review.get("signedPnl"), ensure_ascii=False, indent=2))
            print(json.dumps(review.get("staticPreview"), ensure_ascii=False, indent=2)[:1200])
            print("---")
        return 0
    if len(sys.argv) > 2 and sys.argv[2] == "--queue":
        scanner = payload.get("scanner", {}) or {}
        queue = scanner.get("closed_loop_review_queue", {}) or {}
        wanted = (
            "backpressure", "claimed", "completed", "done", "error",
            "fallback_only", "executionBackend", "lastError", "lastErrorAt",
        )
        print(json.dumps({key: queue.get(key) for key in wanted},
                         ensure_ascii=False, indent=2))
        watch = queue.get("last_supervisor_watch") or {}
        print(json.dumps(watch.get("loopStateSummary", {}).get("cycleTerminals"),
                         ensure_ascii=False))
        return 0
    walk(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
