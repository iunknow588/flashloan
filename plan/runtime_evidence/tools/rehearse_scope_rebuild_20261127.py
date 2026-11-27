#!/usr/bin/env python3
"""Rehearse the scope rebuild that the baseline audit asks for.

The audit demands a production scope whose anchor libraries are exactly
[core, stocks]; the production scopes carry [core, stocks, test].  The
scope tool owns that decision, so the repair is a tool call -- but it is
rehearsed with dryRun first, and the rehearsal is written down before
anything is applied.

Usage:
  rehearse_scope_rebuild_20261127.py            # dry run, both scopes
  rehearse_scope_rebuild_20261127.py --apply    # write
  rehearse_scope_rebuild_20261127.py --mode core --apply
"""
import json
import sys
import urllib.error
import urllib.request
import uuid


MANAGER_API = "http://127.0.0.1:8091"
CHAIN_ID = 4663
SCOPES = ("core4", "candidate_exchange")
DEFAULT_TOKEN_LOAD_MODE = "core"


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
        with urllib.request.urlopen(request, timeout=300) as response:
            return {"status": response.status,
                    "body": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as error:
        return {"status": error.code,
                "body": error.read().decode("utf-8", "replace")[:4000]}
    except Exception as error:  # noqa: BLE001 - the rehearsal reports failures
        return {"status": -1, "body": str(error)}


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    token_load_mode = DEFAULT_TOKEN_LOAD_MODE
    if "--mode" in sys.argv[1:]:
        token_load_mode = sys.argv[sys.argv.index("--mode") + 1]
    out = {"apply": apply, "tokenLoadMode": token_load_mode, "scopes": {}}
    for scope in SCOPES:
        request_id = f"healer-scope-{scope}-{uuid.uuid4().hex[:12]}"
        response = post(
            "/v1/pools/scopes/rebuild",
            {"chainId": CHAIN_ID, "scopeKey": scope, "dryRun": not apply,
             "tokenLoadMode": token_load_mode},
            request_id,
            f"healer-scope-idem-{scope}-{token_load_mode}",
        )
        out["scopes"][scope] = response
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
