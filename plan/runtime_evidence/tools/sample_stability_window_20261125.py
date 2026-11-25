#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sample the live runtime once a minute and append the readings to a JSONL file.

``T-方储.01`` (stage 2) may only delete the old ordering after the new
ordering has been *live and stable*: the closed-loop count, the hop count,
the price quality and the broadcast output must not have gone back over a
continuous window.  A window cannot be argued out of a single reading -- it
has to be sampled.

The sampler is read-only: it reads the overview over HTTP and never opens the
store for writing.

Usage:
    python sample_stability_window_20261125.py <out.jsonl> [--url URL] [--interval SECONDS]
"""

import argparse
import datetime
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_URL = "http://127.0.0.1:5000/api/dex/overview"


def _number(value):
    return value if isinstance(value, (int, float)) or value is None else value


def sample(url):
    with urllib.request.urlopen(url, timeout=120) as handle:
        payload = json.loads(handle.read().decode("utf-8", errors="replace"))
    versions = payload.get("versions") or {}
    summary = payload.get("summary") or {}
    runtime = payload.get("runtimeCore") or {}
    quality = payload.get("dataQuality") or {}
    upstream = (payload.get("pipelineStatus") or {}).get("upstreamAssessment") or {}
    return {
        "sampledAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "closedLoopVersion": versions.get("closedLoopVersion"),
        "closedLoopChecksum": versions.get("closedLoopChecksum"),
        "reviewVersion": versions.get("reviewVersion"),
        "candidateEdgeVersion": versions.get("candidateEdgeVersion"),
        "observationVersion": versions.get("observationVersion"),
        "broadcastVersion": versions.get("broadcastVersion"),
        "versionsConsistent": versions.get("versionsConsistent"),
        "consistencyIssues": versions.get("consistencyIssues"),
        "upstreamConsistent": upstream.get("consistent"),
        "upstreamIssues": upstream.get("issues"),
        "reviewCount": summary.get("reviewCount"),
        "reviewCompletedCount": summary.get("reviewCompletedCount"),
        "reviewDataIncompleteCount": summary.get("reviewDataIncompleteCount"),
        "reviewNotExecutableCount": summary.get("reviewNotExecutableCount"),
        "reviewFailedCount": summary.get("reviewFailedCount"),
        "profitableRouteCount": summary.get("overviewProfitableRouteCount"),
        "bestObservedNetProfitUsd": _number(summary.get("overviewBestObservedNetProfitUsd")),
        "chainSubmissionCandidateCount": summary.get("overviewChainSubmissionCandidateCount"),
        "broadcastEnabled": summary.get("broadcastEnabled"),
        "broadcastRequestCount": summary.get("broadcastRequestCount"),
        "broadcastAttemptCount": summary.get("broadcastAttemptCount"),
        "broadcastSuccessCount": summary.get("broadcastSuccessCount"),
        "pnlMissingCount": quality.get("pnlMissingCount"),
        "missingPoolReferenceCount": quality.get("missingPoolReferenceCount"),
        "runtimeGeneration": runtime.get("generation"),
        "runtimeSnapshotAvailable": runtime.get("snapshotAvailable"),
        "runtimeSnapshotChecksum": runtime.get("snapshotChecksum"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--samples", type=int, default=0, help="0 = run until stopped")
    args = parser.parse_args()

    taken = 0
    with open(args.output, "a", encoding="utf-8", newline="\n") as handle:
        while args.samples == 0 or taken < args.samples:
            try:
                reading = sample(args.url)
            except Exception as error:  # a failed sample must not end the window
                reading = {
                    "sampledAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "error": "%s: %s" % (type(error).__name__, error),
                }
            handle.write(json.dumps(reading, ensure_ascii=False) + "\n")
            handle.flush()
            taken += 1
            print(json.dumps(reading, ensure_ascii=False))
            if args.samples == 0 or taken < args.samples:
                time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
