"""Rewrite the merge of the stored evidence so a measurement is never downgraded.

The merge prefers the fresh evidence over the stored one, and the fresh
evidence of the orchestrator's manual review is only a summary of the
measurement the runtime published.  The merge therefore replaced a complete
measurement with a summary of it, and the review library lost the ceiling
the amount was searched inside.

This patch rewrites the merge as a per-loop decision: the stored
measurement wins when it is complete and the fresh one is not.
"""

from pathlib import Path

HEALER = Path("scripts/mainnet_arbitrage_healer.py")

NEW_FUNCTION = '''def merge_with_stored_evidence(
    db: Path,
    manager_api: str | None,
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Return the evidence of **every** measured cycle of the chain.

    The Review Manager replaces the whole review set of the scope with the
    evidence it receives. Submitting only the cycles that were just
    re-reviewed would therefore erase the reviews of the cycles that were
    left out, and the library would report them as "evidence is missing"
    even though a valid measurement exists for them.

    The stored reviews are read back from the Manager library, because that
    is the authority for the structure of a review: it knows which loop a
    measurement belongs to and it already holds the signed profit.

    A stored review of a loop the catalog **no longer publishes** is not
    carried.  The Review Manager names every closed loop exactly and
    rejects a submission that names one it does not know, so one retired
    loop in the payload makes it refuse the **whole** write -- and with it
    every fresh measurement of the loops that are still live.  The second
    number of the result counts the reviews that were left out for that
    reason, so the omission is visible instead of silent.

    The Manager library holds the runtime's own measurement of every closed
    loop, and that measurement is **complete**: it carries the amount
    ladder, the ceiling the ladder searched inside and the route it priced.
    The orchestrator's own manual review only carries a summary of the same
    measurement, because the record table holds no ladder.  A submission
    that prefers the summary replaces a complete measurement with a summary
    of it, and the review library loses the ceiling that says the amount was
    inside the bound the review enforces.

    The measurement that is already there therefore wins: a gap is filled
    with a measurement that can show its own ceiling, and a measurement
    that can is never replaced by one that cannot.  The third number of the
    result names the loops whose stored measurement was kept.
    """
    new_by_loop: dict[str, dict[str, Any]] = {}
    for item in evidence:
        loop_key = str(item.get("loopKey") or "").strip()
        if not loop_key or loop_key in new_by_loop:
            continue
        new_by_loop[loop_key] = item
    stored = stored_review_evidence(db, manager_api)
    stored_by_loop: dict[str, dict[str, Any]] = {}
    for item in stored:
        loop_key = str(item.get("loopKey") or "").strip()
        if not loop_key or loop_key in stored_by_loop:
            continue
        stored_by_loop[loop_key] = item
    # The published catalog is the authority on which loops still exist.
    # When it cannot be read the list is empty and nothing is dropped: the
    # submission then fails loudly on its own instead of losing reviews to
    # a catalog that was never seen.
    published = {
        row["loop_key"].strip().lower()
        for row in manager_loop_rows(manager_api)
        if str(row.get("loop_key") or "").strip()
    }
    retired = 0
    kept_stored: list[str] = []
    merged: list[dict[str, Any]] = []
    for loop_key, fresh in new_by_loop.items():
        stored_item = stored_by_loop.get(loop_key)
        if (stored_item is not None
                and review_shows_the_search_ceiling(stored_item)
                and not review_shows_the_search_ceiling(fresh)):
            # The stored measurement is complete and the fresh one is only
            # a summary of it, so the stored measurement stays.
            merged.append(stored_item)
            kept_stored.append(loop_key)
            continue
        merged.append(fresh)
    for loop_key, item in stored_by_loop.items():
        if loop_key in new_by_loop:
            # The loop was measured again, and the measurement that is
            # carried is the one decided above.
            continue
        if published and loop_key.lower() not in published:
            retired += 1
            continue
        merged.append(item)
    return merged, retired, kept_stored


'''


def patch() -> None:
    text = HEALER.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    start = text.find("def merge_with_stored_evidence(")
    end = text.find("def stored_review_evidence(")
    if start < 0 or end < 0 or end <= start:
        raise SystemExit("anchor not found: the merge of the stored evidence")
    text = text[:start] + NEW_FUNCTION + text[end:]
    if crlf:
        text = text.replace("\n", "\r\n")
    HEALER.write_bytes(text.encode("utf-8"))
    print(f"patched {HEALER}")


if __name__ == "__main__":
    patch()
