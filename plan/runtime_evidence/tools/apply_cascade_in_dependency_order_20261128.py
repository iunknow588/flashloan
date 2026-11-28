#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the layers below the pool scope **in dependency order**.

The candidate edge layer and the candidate exchange layer publish the
direction layer's ranking, so the direction layer has to be materialized
first.  This tool calls the manager's rebuild endpoints in that order and
records every answer, so the order that was actually used is part of the
evidence.

Usage::

    python apply_cascade_in_dependency_order_20261128.py --manager-api http://127.0.0.1:8091
    python apply_cascade_in_dependency_order_20261128.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_MANAGER_API = "http://127.0.0.1:8091"
CHAIN_ID = 4663

#: The dependency order of the layers below the pool scope.
#:
#: The direction layer ranks the pools of the scope; the candidate edge
#: layer publishes that ranking for the ``core4`` scope; the candidate
#: exchange layer materializes the observation of the ``candidate_exchange``
#: scope, whose rows carry the ranking of **that** scope; the closed loop
#: layer reads the observation and therefore reads the ranking of the
#: ``candidate_exchange`` scope.  So both direction layers are rebuilt, and
#: the one the observation reads is rebuilt before the closed loop layer is
#: asked for its routes.
CHAIN = (
    ("directionalPools.core4", "/v1/directional-pools/rebuild", "core4"),
    ("candidateEdge", "/v1/candidate-edge-pools/rebuild", None),
    ("directionalPools.candidateExchange", "/v1/directional-pools/rebuild", "candidate_exchange"),
    ("candidateExchange", "/v1/candidate-exchange-pools/rebuild", None),
    ("closedLoops", "/v1/closed-loops/rebuild", None),
)


def post(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            # A fresh idempotency key per call: the manager replays the
            # stored answer of a key it has seen, so a fixed key would
            # replay an old result instead of rebuilding.
            "X-Request-Id": f"cascade-{uuid.uuid4().hex}",
            "Idempotency-Key": f"cascade-{uuid.uuid4().hex}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return {
                "status": response.status,
                "body": response.read().decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as error:
        return {
            "status": error.code,
            "body": error.read().decode("utf-8", errors="replace"),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"status": 0, "body": f"{type(error).__name__}: {error}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager-api", default=DEFAULT_MANAGER_API)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    results = []
    for name, path, scope_key in CHAIN:
        body: dict = {"chainId": CHAIN_ID, "dryRun": args.dry_run}
        if scope_key:
            body["scopeKey"] = scope_key
        answer = post(args.manager_api, path, body)
        results.append({"layer": name, "path": path, "body": body,
                        "status": answer["status"], "response": answer["body"]})
        print(f"{name}: HTTP {answer['status']}")

    report = {
        "managerApi": args.manager_api,
        "chainId": CHAIN_ID,
        "dryRun": args.dry_run,
        "order": [name for name, _path, _scope in CHAIN],
        "results": results,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {args.json_out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
