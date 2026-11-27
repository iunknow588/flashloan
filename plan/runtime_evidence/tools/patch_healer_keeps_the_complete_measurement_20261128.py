"""Stop the orchestrator from replacing a complete measurement with a summary.

The runtime publishes every closed loop's review with the **complete**
measurement: the amount ladder, the ceiling the ladder searched inside and
the route it priced.  The orchestrator's own manual review only carries a
summary of the same measurement (the record table holds no ladder), and the
merge preferred the summary, so every cycle replaced a complete row with a
summary of it and the ceiling was lost from the review library.

The patch keeps the measurement that is already there: a new measurement only
replaces a stored one when it can show the ceiling itself.
"""

from pathlib import Path

HEALER = Path("scripts/mainnet_arbitrage_healer.py")

NEW_HELPERS = '''
def review_static_preview(item: dict[str, Any]) -> dict[str, Any]:
    """The static preview of a stored review or of an evidence block."""
    for key in ("staticPreview", "static_preview", "static_preview_json"):
        preview = item.get(key)
        if isinstance(preview, dict):
            return preview
        if isinstance(preview, str) and preview.strip():
            try:
                parsed = json.loads(preview)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def review_shows_the_search_ceiling(item: dict[str, Any]) -> bool:
    """Whether the review can show the ceiling its amount was searched inside.

    The kernel publishes the ceiling **with** the review: the selected amount
    is executable only when it stayed inside the published single-tick
    capacity, so a review that cannot show the ceiling cannot show that its
    own amount was inside the bound the review enforces.  The ceiling fields
    are therefore part of the measurement, and a review that carries only a
    summary of one is not a measurement the broadcast layer may act on.
    """
    preview = review_static_preview(item)
    return "searchCeilingRaw" in preview and "searchCeilingBasis" in preview
'''

OLD_MERGE = '''        if loop_key in seen:
            continue
        seen.add(loop_key)
        merged.append(item)
    stored = stored_review_evidence(db, manager_api)
'''

NEW_MERGE = '''        if loop_key in seen:
            continue
        seen.add(loop_key)
        merged.append(item)
    stored = stored_review_evidence(db, manager_api)
    # The Manager library holds the runtime's own measurement of every
    # closed loop, and that measurement is **complete**: it carries the
    # amount ladder, the ceiling the ladder searched inside and the route it
    # priced.  The orchestrator's manual review only carries a summary of
    # the same measurement, because the record table holds no ladder.  A
    # submission that prefers the summary replaces a complete measurement
    # with a summary of it, and the review library loses the ceiling that
    # says the amount was inside the bound the review enforces.
    #
    # The measurement that is already there therefore wins: a gap is filled
    # with a measurement that can show its own ceiling, and a measurement
    # that can is never replaced by one that cannot.
    complete = {
        str(row.get("loopKey") or "").strip(): row
        for row in stored
        if review_shows_the_search_ceiling(row)
    }
    downgraded: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for item in merged:
        loop_key = str(item.get("loopKey") or "").strip()
        if loop_key in complete and not review_shows_the_search_ceiling(item):
            downgraded.append(item)
            continue
        kept.append(item)
    merged = kept
'''

OLD_RESULT = '''    return {"mode": "materialize-reviews", "generatedAt": now(), "apply": apply,
            "dryRun": not apply, "evidenceCount": len(merged),
            "submittedEvidenceCount": len(evidence),
'''

NEW_RESULT = '''    return {"mode": "materialize-reviews", "generatedAt": now(), "apply": apply,
            "dryRun": not apply, "evidenceCount": len(merged),
            "submittedEvidenceCount": len(evidence),
            "keptStoredMeasurementCount": len(downgraded),
'''


def patch() -> None:
    raw = HEALER.read_bytes()
    text = raw.decode("utf-8")
    crlf = "\r\n" in text
    # The anchors below are written with LF; the file is matched on its
    # normalized text and written back with the endings it had.
    text = text.replace("\r\n", "\n")
    newline = "\n"

    anchor = "def merge_with_stored_evidence("
    if anchor not in text:
        raise SystemExit("anchor not found: the merge of the stored evidence")
    helper_block = NEW_HELPERS.strip("\n")
    text = text.replace(anchor, helper_block + newline + newline + anchor, 1)

    for old, new in ((OLD_MERGE, NEW_MERGE), (OLD_RESULT, NEW_RESULT)):
        if old not in text:
            raise SystemExit(f"anchor not found: {old.splitlines()[0].strip()}")
        text = text.replace(old, new, 1)

    if crlf:
        text = text.replace("\n", "\r\n")
    HEALER.write_bytes(text.encode("utf-8"))
    print(f"patched {HEALER}")


if __name__ == "__main__":
    patch()
