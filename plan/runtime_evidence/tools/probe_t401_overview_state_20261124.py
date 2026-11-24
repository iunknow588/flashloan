#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the live overview payload and print the runtime-core status fields.

Usage:
    python probe_t401_overview_state_20261124.py [url]
"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000/api/dex/overview"


def main():
    with urllib.request.urlopen(URL, timeout=120) as handle:
        payload = json.loads(handle.read().decode("utf-8", errors="replace"))
    runtime_core = payload.get("runtime_core") or {}
    summary = {
        "state": runtime_core.get("state"),
        "generation": runtime_core.get("generation"),
        "snapshotAvailable": runtime_core.get("snapshotAvailable"),
        "lastError": runtime_core.get("lastError"),
        "consistencyIssues": payload.get("versions", {}).get("consistencyIssues"),
        "upstreamAssessment": payload.get("pipelineStatus", {}).get("upstreamAssessment"),
        "blockers": payload.get("blockers"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
