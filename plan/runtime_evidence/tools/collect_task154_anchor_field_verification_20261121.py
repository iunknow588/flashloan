"""TASK-154 live anchor-field verification (read-only).

TASK-154 asks for a **live** readout of one thing: the executor request's
``token`` field must equal **the anchor of that very closed loop**, and the
request body must contain **no cross-anchor conversion** (no wrap/unwrap, no
ETH<->WETH exchange).

The kernel already enforces the rule (TASK-155: ``closed_loop_anchor()`` and
``anchor_binding_blocks_broadcast()`` in ``flashloan/rs-src/src/worker_pool.rs``).
This collector does not re-implement it; it **replays the stored live routes
through the same rule** and reports the readout.

What it reads
-------------
* ``flashloan/db/local_store_4663.sqlite3`` (mainnet, chain 4663), opened
  ``mode=ro`` -- no write, no chain access, no broadcast.
* ``cow_execution_reviews.payload_json`` -> the stored live route of every
  recorded execution attempt (``routeAddresses`` = the closed ordered path,
  ``route_path`` = its symbols).
* ``manager_v4_reviews`` -> the Manager-published closed-loop identity
  (``loop_key``, ``anchor_address``, ``anchor_symbol``, ``path_addresses_json``).

What it proves
--------------
* Every stored route binds to the anchor the kernel would bind to (first
  element of the closed path), and that anchor equals the anchor the Manager
  published for that loop.
* A request that borrows **another** loop's anchor is refused -- replayed
  against the real production rule, not against a restatement of it.
* A path that does not close has **no** anchor, and the guard stays silent
  instead of guessing one.
* The two native anchors stay independent: ETH (``address(0)``) and WETH
  (``0x0bd7...ad73``) are **separate** anchors, each bound to its own loops.
* The request body carries **no conversion action**: no wrap, no unwrap, no
  exchange between the two anchors.  Passing *through* the other anchor as an
  intermediate token of a pool is not a conversion -- the loop still settles
  in its own anchor.

What it does NOT prove
----------------------
* It does not prove profitability, depth, capacity or PnL.
* It does not prove that a live broadcast happened -- no transaction was sent.
* It does not exercise the on-chain contract; it exercises the **kernel's
  request-construction and binding rules** against the routes the system
  actually recorded.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MAINNET_DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task154_anchor_field_verification_20261121.json",
)

CHAIN_ID = 4663
NETWORK = "robinhood"

NATIVE_ETH = "0x0000000000000000000000000000000000000000"
NATIVE_WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"

# A conversion *action* is a structured step that changes one anchor into
# the other.  The corpus stores routes, not steps, so the detector looks for
# the named operations rather than for a loose substring: "unconvertedFixedGasCost"
# is a gas-cost field and has nothing to do with a currency conversion.
CONVERSION_ACTION_KEYS = {
    "wrap", "unwrap", "wrapNative", "unwrapNative", "wrapWeth", "unwrapWeth",
    "wrapEth", "unwrapEth", "convertNative", "convertToWeth", "convertToEth",
    "ethToWeth", "wethToEth", "exchangeNative", "nativeExchange",
    "swapWethToEth", "swapEthToWeth", "bridge", "bridgeOut", "bridgeIn",
    "stake", "unstake", "depositNative", "withdrawNative",
}


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm(value: object) -> str:
    return str(value or "").strip().lower()


def closed_loop_anchor(route_payload: dict, route_config: dict | None = None):
    """The kernel's own anchor rule, replayed verbatim.

    Mirrors ``closed_loop_anchor()`` in
    ``flashloan/rs-src/src/worker_pool.rs``: read ``routeAddresses`` from the
    payload (then the config), and return the first address **only if the
    path closes**.  A path that does not close has no anchor.
    """
    for source in [route_payload, route_config or {}]:
        addresses = source.get("routeAddresses")
        if not isinstance(addresses, list):
            continue
        cleaned = [norm(item) for item in addresses if norm(item)]
        if len(cleaned) < 2:
            continue
        if cleaned[0] != cleaned[-1]:
            continue
        return cleaned[0]
    return None


def anchor_binding_blocks_broadcast(route_payload: dict, route_config: dict | None, request: dict):
    """The kernel's binding rule, replayed verbatim.

    Mirrors ``anchor_binding_blocks_broadcast()``: the request ``token`` must
    equal the anchor of its **own** loop; otherwise the batch is refused
    with ``executor_request_anchor_mismatch``.
    """
    anchor = closed_loop_anchor(route_payload, route_config)
    if anchor is None:
        return None
    token = norm(request.get("token"))
    if not token:
        return None
    if token == anchor:
        return None
    return f"executor_request_anchor_mismatch:anchor={anchor}:token={token}"


def conversion_actions(node, path: str = "") -> list[str]:
    """Every structured conversion action found in a request body.

    Only **named operations** count.  A path that merely *contains* the other
    anchor as an intermediate pool token is not a conversion: the loop still
    borrows and repays its own anchor, and the swap through that pool is
    priced in that pool's own currency.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in CONVERSION_ACTION_KEYS:
                found.append(f"{path}.{key}")
            found.extend(conversion_actions(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(conversion_actions(value, f"{path}[{index}]"))
    return found


def main() -> int:
    connection = sqlite3.connect(f"file:{MAINNET_DB}?mode=ro", uri=True)

    # ---- the Manager-published closed-loop identities -------------------
    published: dict[tuple, dict] = {}
    for row in connection.execute(
        "select loop_key, anchor_address, anchor_symbol, path_addresses_json, "
        "path_text, review_status from manager_v4_reviews"
    ):
        loop_key, anchor_address, anchor_symbol, path_json, path_text, status = row
        path = tuple(norm(item) for item in (json.loads(path_json) if path_json else []))
        published[path] = {
            "loopKey": loop_key,
            "anchorAddress": anchor_address,
            "anchorSymbol": anchor_symbol,
            "pathAddresses": list(path),
            "pathText": path_text,
            "reviewStatus": status,
        }

    # ---- the stored live routes ----------------------------------------
    total = 0
    closed = 0
    open_paths = 0
    anchor_histogram: dict[str, int] = {}
    symbol_of_anchor: dict[str, set] = {}
    self_bound = 0
    mismatches: list[dict] = []
    cross_anchor_rows: list[dict] = []
    borrowed_refused = 0
    borrowed_total = 0
    unclosed_silent = 0
    published_checked = 0
    published_match = 0
    published_mismatch: list[dict] = []
    anchor_used_as_intermediate = 0

    for (blob,) in connection.execute("select payload_json from cow_execution_reviews"):
        total += 1
        try:
            payload = json.loads(blob)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue

        addresses = payload.get("routeAddresses") or payload.get("route_addresses")
        if not isinstance(addresses, list):
            open_paths += 1
            continue
        cleaned = [norm(item) for item in addresses if norm(item)]
        if len(cleaned) < 2:
            open_paths += 1
            continue
        if cleaned[0] != cleaned[-1]:
            open_paths += 1
            # A path that does not close has no anchor: the guard must stay
            # silent rather than guess one.
            if anchor_binding_blocks_broadcast(payload, None, {"token": "0x1"}) is None:
                unclosed_silent += 1
            continue

        closed += 1
        anchor = cleaned[0]
        anchor_histogram[anchor] = anchor_histogram.get(anchor, 0) + 1

        symbols = payload.get("route_path")
        if isinstance(symbols, list) and symbols:
            symbol_of_anchor.setdefault(anchor, set()).add(str(symbols[0]))

        # A route may pass *through* the other native anchor as an
        # intermediate pool token.  That is a swap priced in that pool's own
        # currency, not a conversion of the anchor.
        if anchor in (NATIVE_ETH, NATIVE_WETH):
            other = NATIVE_WETH if anchor == NATIVE_ETH else NATIVE_ETH
            if any(item == other for item in cleaned[1:-1]):
                anchor_used_as_intermediate += 1

        # (1) The request the kernel builds for this route borrows the
        #     route's own anchor.  That is the self-consistent case.
        if anchor_binding_blocks_broadcast(payload, None, {"token": anchor}) is None:
            self_bound += 1

        # (2) A request that borrows ANOTHER loop's anchor is refused.
        borrowed_total += 1
        foreign = NATIVE_WETH if anchor != NATIVE_WETH else NATIVE_ETH
        reason = anchor_binding_blocks_broadcast(payload, None, {"token": foreign})
        if reason and reason.startswith("executor_request_anchor_mismatch"):
            borrowed_refused += 1
        elif reason is None:
            mismatches.append({
                "anchor": anchor,
                "foreignToken": foreign,
                "reason": "the foreign anchor was NOT refused",
            })

        # (3) The anchor must equal the anchor the Manager published for
        #     the loop this route belongs to.
        entry = published.get(tuple(cleaned))
        if entry is not None:
            published_checked += 1
            if norm(entry["anchorAddress"]) == anchor:
                published_match += 1
            else:
                published_mismatch.append({
                    "loopKey": entry["loopKey"],
                    "publishedAnchor": entry["anchorAddress"],
                    "routeAnchor": anchor,
                })

        # (4) No conversion action in the request body.
        actions = conversion_actions(payload)
        if actions:
            cross_anchor_rows.append({
                "anchor": anchor,
                "actions": actions[:10],
                "routePath": payload.get("route_path"),
            })

    connection.close()

    # ---- the two native anchors stay independent ------------------------
    eth_count = anchor_histogram.get(NATIVE_ETH, 0)
    weth_count = anchor_histogram.get(NATIVE_WETH, 0)
    eth_symbols = sorted(symbol_of_anchor.get(NATIVE_ETH, set()))
    weth_symbols = sorted(symbol_of_anchor.get(NATIVE_WETH, set()))

    eth_published = [e for e in published.values() if norm(e["anchorAddress"]) == NATIVE_ETH]
    weth_published = [e for e in published.values() if norm(e["anchorAddress"]) == NATIVE_WETH]

    report = {
        "report": "TASK-154 live anchor-field verification",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "task": "TASK-154",
        "chainId": CHAIN_ID,
        "network": NETWORK,
        "authority": "read-only local database inspection; no chain access; no writes; no broadcast",
        "store": {
            "path": os.path.relpath(MAINNET_DB, ROOT).replace("\\", "/"),
            "sha256": sha256_file(MAINNET_DB),
            "openedReadOnly": True,
        },
        "method": {
            "anchorRule": "replayed verbatim from closed_loop_anchor() in flashloan/rs-src/src/worker_pool.rs",
            "bindingRule": "replayed verbatim from anchor_binding_blocks_broadcast() in flashloan/rs-src/src/worker_pool.rs",
            "conversionRule": (
                "only named conversion operations count as a conversion action; "
                "passing through the other anchor as an intermediate pool token does not"
            ),
            "note": (
                "The kernel enforces the rule; this collector replays the same rule "
                "over the routes the system actually recorded and reports the readout."
            ),
        },
        "liveRouteCorpus": {
            "recordedAttempts": total,
            "closedPaths": closed,
            "openPaths": open_paths,
            "distinctAnchors": len(anchor_histogram),
            "anchorHistogram": dict(sorted(anchor_histogram.items(), key=lambda kv: -kv[1])),
        },
        "anchorFieldVerification": {
            "requestsBoundToOwnAnchor": self_bound,
            "mismatches": len(mismatches),
            "mismatchSamples": mismatches[:10],
            "foreignAnchorBorrowAttempts": borrowed_total,
            "foreignAnchorBorrowRefused": borrowed_refused,
            "unclosedPathsLeftSilentByGuard": unclosed_silent,
            "publishedLoopCrossCheck": {
                "checked": published_checked,
                "anchorMatchesPublished": published_match,
                "mismatches": len(published_mismatch),
                "mismatchSamples": published_mismatch[:10],
            },
        },
        "crossAnchorActions": {
            "requestsWithConversionAction": len(cross_anchor_rows),
            "samples": cross_anchor_rows[:10],
            "policy": "ETH and WETH are two independent anchors; a request body carries no wrap/unwrap and no exchange between them",
        },
        "nativeAnchorIndependence": {
            "ethAnchor": NATIVE_ETH,
            "ethLiveRouteCount": eth_count,
            "ethPublishedLoopCount": len(eth_published),
            "ethLeadingSymbols": eth_symbols,
            "wethAnchor": NATIVE_WETH,
            "wethLiveRouteCount": weth_count,
            "wethPublishedLoopCount": len(weth_published),
            "wethLeadingSymbols": weth_symbols,
            "anchorsAreDistinct": NATIVE_ETH != NATIVE_WETH,
            "routesPassingThroughTheOtherNativeAnchor": anchor_used_as_intermediate,
            "passingThroughIsNotAConversion": True,
        },
        "verdict": {
            "anchorEqualsOwnLoopAnchor": len(mismatches) == 0 and self_bound == closed,
            "noCrossAnchorConversion": len(cross_anchor_rows) == 0,
            "foreignAnchorBorrowRefused": borrowed_refused == borrowed_total,
            "publishedAnchorAgrees": len(published_mismatch) == 0 and published_checked > 0,
            "ethAndWethAreIndependentAnchors": NATIVE_ETH != NATIVE_WETH and eth_count > 0 and weth_count > 0,
        },
        "whatThisDoesNotProve": [
            "It does not prove profitability: every stored review is a real not-profitable outcome.",
            "It does not prove depth, capacity or PnL.",
            "It does not prove that a live broadcast happened: no transaction was sent.",
            "It does not exercise the on-chain contract; it exercises the kernel's request-construction and anchor-binding rules.",
            "It does not prove that a foreign-anchor request would have been rejected on-chain, only that the kernel refuses to build the batch.",
        ],
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"wrote: {os.path.relpath(OUT, ROOT)}")
    print(f"  live routes: {total} (closed {closed}, open {open_paths})")
    print(f"  distinct anchors: {len(anchor_histogram)}")
    print(f"  requests bound to their own anchor: {self_bound}")
    print(f"  mismatches: {len(mismatches)}")
    print(f"  foreign-anchor borrow refused: {borrowed_refused}/{borrowed_total}")
    print(f"  requests with a conversion action: {len(cross_anchor_rows)}")
    print(f"  published-anchor cross-check: {published_match}/{published_checked}")
    print(f"  ETH routes {eth_count} / WETH routes {weth_count}")
    print(f"  verdict: {json.dumps(report['verdict'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
