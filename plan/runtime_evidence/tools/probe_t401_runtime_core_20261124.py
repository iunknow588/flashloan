#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the RuntimeCore snapshot state, and try to make it appear.

The RuntimeCore only publishes a snapshot when something asks it to refresh
(\u0060page_context\u0060 does).  This probe therefore:

1. reads the overview and reports the runtime-core state as it is;
2. asks the control-panel page for a page context, which refreshes the core;
3. reads the overview again and reports the state after the refresh.

Usage:
    python probe_t401_runtime_core_20261124.py
"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000"


def read_json(path, timeout=180):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as handle:
        return json.loads(handle.read().decode("utf-8", errors="replace"))


def core_of(payload):
    core = payload.get("runtimeCore") or {}
    return {
        "state": core.get("state"),
        "snapshotAvailable": core.get("snapshotAvailable"),
        "generation": core.get("generation"),
        "instanceId": core.get("instanceId"),
        "snapshotChecksum": core.get("snapshotChecksum"),
        "upstreamFingerprint": core.get("upstreamFingerprint"),
    }


def main():
    report = {}
    try:
        report["overviewBefore"] = core_of(read_json("/api/dex/overview"))
    except Exception as error:
        report["overviewBefore"] = {"error": "%s: %s" % (type(error).__name__, error)}
    try:
        # The status route installs the chain runtime, and the chain runtime
        # refreshes the RuntimeCore snapshot while it builds the page context.
        with urllib.request.urlopen(BASE + "/api/status", timeout=300) as handle:
            panel = json.loads(handle.read().decode("utf-8", errors="replace"))
        chain_runtime = panel.get("chain_runtime") or {}
        report["panelRuntimeCore"] = chain_runtime.get("runtimeCore")
        report["panelScannerRuntimeCore"] = (
            panel.get("dex_opportunity_scanner") or {}
        ).get("runtime_core")
        report["controlPanelRead"] = True
    except Exception as error:
        report["controlPanelRead"] = "%s: %s" % (type(error).__name__, error)
    try:
        report["overviewAfter"] = core_of(read_json("/api/dex/overview"))
    except Exception as error:
        report["overviewAfter"] = {"error": "%s: %s" % (type(error).__name__, error)}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
