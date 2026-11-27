#!/usr/bin/env python3
"""Run the downstream rebuild chain after a scope repair.

The pool scope owns the admission set; every layer below it is materialized
from it.  A scope repair therefore invalidates the layers below, and they
have to be rebuilt in order -- the order is a hard constraint, because each
layer reads the version and checksum of the one above it:

    candidate edge -> candidate exchange (and the observation layer it
    materializes) -> directional pools -> closed loops

Every step is rehearsed with dryRun first and the rehearsal is recorded
before the step is applied.

Usage:
  rebuild_cascade_20261127.py            # dry run every step
  rebuild_cascade_20261127.py --apply    # dry run, then apply, step by step
"""
import json
import sys
import urllib.error
import urllib.request
import uuid


MANAGER_API = "http://127.0.0.1:8091"
CHAIN_ID = 4663
STEPS = (
    ("candidateEdge", "/v1/candidate-edge-pools/rebuild"),
    ("candidateExchange", "/v1/candidate-exchange-pools/rebuild"),
    ("directionalPools", "/v1/directional-pools/rebuild"),
    ("closedLoops", "/v1/closed-loops/rebuild"),
)


def post(path: str, body: dict, request_id: str, idempotency_key: str) -> dict:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        MANAGER_API + path, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
            "Idempotency-Key": idempotency_key,
        })
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return {"status": response.status,
                    "body": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as error:
        return {"status": error.code,
                "body": error.read().decode("utf-8", "replace")[:4000]}
    except Exception as error:  # noqa: BLE001 - the chain reports failures
        return {"status": -1, "body": str(error)}


def summarize(response: dict) -> dict:
    body = response.get("body")
    if not isinstance(body, dict):
        return {"status": response.get("status"), "body": str(body)[:400]}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    keys = ("apply", "dryRun", "insertedRows", "updatedRows", "deletedRows",
            "changedRows", "activePoolCount", "eligiblePoolCount", "loopCount",
            "hopCount", "directionCount", "anchorGroupCount", "pairCount",
            "materializedChecksum", "consumersNeedRebuild", "idempotentReplay",
            "skipReasonCounts")
    return {
        "status": response.get("status"),
        "ok": body.get("ok"),
        "error": body.get("error"),
        "data": {key: data[key] for key in keys if key in data},
    }


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    out: dict = {"apply": apply, "steps": {}}
    for name, path in STEPS:
        body = {"chainId": CHAIN_ID, "dryRun": not apply}
        if name == "directionalPools":
            body["scopeKey"] = "core4"
        rehearsal = post(path, dict(body), f"healer-cascade-{name}-dry-{uuid.uuid4().hex[:12]}",
                         f"healer-cascade-{name}-dry-{uuid.uuid4().hex[:12]}")
        entry = {"rehearsal": summarize(rehearsal)}
        if apply:
            request_id = f"healer-cascade-{name}-{uuid.uuid4().hex[:12]}"
            applied = post(path, dict(body), request_id,
                           f"healer-cascade-{name}-apply-{uuid.uuid4().hex[:12]}")
            entry["applied"] = summarize(applied)
        out["steps"][name] = entry
        print(json.dumps({name: entry}, ensure_ascii=False))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
