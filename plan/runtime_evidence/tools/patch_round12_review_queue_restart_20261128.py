"""Round 12: the main thread of the review refreshes the queue and starts
again from the head.

The queue of the review is **dynamic**: the moments it is ordered by are
written while it is read.  A walk that stopped at a loop whose moment has
not come says therefore nothing about the work behind it, and the manual
review of a loop -- which makes the loop due at once -- lands in a queue
that has to be read again.

The healer is patched by **substring replacement**, because the sentences
that carry the rule are not unique enough for a line based patch.
"""

from pathlib import Path


HEALER = Path(__file__).resolve().parents[3] / "scripts" / "mainnet_arbitrage_healer.py"


OLD_ANSWER = (
    '    loops = data.get("loops") if isinstance(data.get("loops"), list) else []\n'
    '    return {"ok": bool(response.get("ok")) and "loops" in data,\n'
    '            "dueCount": int(data.get("dueCount") or 0),\n'
    '            "waitingCount": int(data.get("waitingCount") or 0),\n'
    '            "loops": [row for row in loops if isinstance(row, dict)]}'
)

NEW_ANSWER = (
    '    loops = data.get("loops") if isinstance(data.get("loops"), list) else []\n'
    '    return {"ok": bool(response.get("ok")) and "loops" in data,\n'
    '            "dueCount": int(data.get("dueCount") or 0),\n'
    '            "waitingCount": int(data.get("waitingCount") or 0),\n'
    '            "loops": [row for row in loops if isinstance(row, dict)],\n'
    '            "walkStoppedAt": data.get("walkStoppedAt"),\n'
    '            "walkStoppedReason": data.get("walkStoppedReason"),\n'
    '            "queueNeedsRefresh": bool(data.get("queueNeedsRefresh")),\n'
    '            "restartFromHead": bool(data.get("restartFromHead"))}'
)


OLD_ANCHOR = (
    '# A review claim takes the loop until the round it started lands, and a\n'
    '# taken loop is never due again and never claimed again.  Only the healer\n'
)

NEW_FUNCTION = (
    '# The number of times the queue is read again in one round of the review\n'
    '# when the walk stopped.  The queue is dynamic, so the walk has to start\n'
    '# again from the head -- but a queue whose head never comes due must not\n'
    '# hold the main thread of the review for ever, so the number is bounded.\n'
    'REVIEW_QUEUE_REFRESH_LIMIT = 3\n'
    '\n'
    '\n'
    'def walk_review_queue(manager_api: str | None,\n'
    '                      chain_id: int | None = None,\n'
    '                      limit: int = REVIEW_QUEUE_REFRESH_LIMIT\n'
    '                      ) -> dict[str, Any]:\n'
    '    """The walk of the main thread of the review over the queue.\n'
    '\n'
    '    The queue is **dynamic**: the moments it is ordered by are written\n'
    '    while it is read, so a walk that stopped at a loop whose moment has\n'
    '    not come says nothing about the work behind it.  The queue is\n'
    '    therefore read again and the walk starts again from the head -- the\n'
    '    main thread never waits on the entry it stopped at.\n'
    '\n'
    '    A manual review lands in the same queue: it makes the loop due at\n'
    '    once, and the loop is picked up from the head of the queue that was\n'
    '    read again.\n'
    '\n'
    '    The number of reads is bounded, and the answer says how many reads\n'
    '    were needed, so a queue that never comes due is visible instead of\n'
    '    silent.\n'
    '    """\n'
    '    queue = due_review_queue(manager_api, chain_id)\n'
    '    refreshes = 0\n'
    '    while bool(queue.get("queueNeedsRefresh")) and refreshes < int(limit):\n'
    '        queue = due_review_queue(manager_api, chain_id)\n'
    '        refreshes += 1\n'
    '    return {"ok": bool(queue.get("ok")),\n'
    '            "queue": queue,\n'
    '            "loops": list(queue.get("loops") or []),\n'
    '            "refreshes": refreshes}\n'
    '\n'
    '\n'
) + OLD_ANCHOR


OLD_CLAIM_COMMENT = (
    '                # A claim that no round will ever land parks its loop at\n'
    '                # the review sentinel, and a parked loop never becomes due\n'
    '                # again.  The claims of a healer that is gone are given\n'
    '                # back first, so the loops they parked can be measured in\n'
    '                # this round.\n'
)

NEW_CLAIM_COMMENT = (
    '                # A claim that no round will ever land takes its loop, and\n'
    '                # a busy loop is never due again.  The claims of a healer\n'
    '                # that is gone are given back first, so the loops they\n'
    '                # took can be measured in this round.\n'
)


OLD_WALK = (
    '                # The queue of the due loops is the work list of the main\n'
    '                # thread of the review: every loop whose review time point\n'
    '                # has come, ordered by that time point.\n'
    '                due_queue = due_review_queue(manager_api)\n'
    '                due_loops = list(due_queue.get("loops") or [])\n'
)

NEW_WALK = (
    '                # The queue of the due loops is the work list of the main\n'
    '                # thread of the review: every loop whose review time point\n'
    '                # has come, ordered by that time point.  The queue is\n'
    '                # **dynamic**: the moments it is ordered by are written\n'
    '                # while it is read, so a walk that stopped at a loop whose\n'
    '                # moment has not come says nothing about the work behind\n'
    '                # it.  The queue is read again and the walk starts again\n'
    '                # from the head -- the main thread never waits on the\n'
    '                # entry it stopped at.\n'
    '                walk = walk_review_queue(manager_api)\n'
    '                due_queue = walk.get("queue") or {}\n'
    '                due_loops = list(walk.get("loops") or [])\n'
)


OLD_OUTCOME = (
    '                    {"action": "due_review_loops",\n'
    '                     "result": {"ok": bool(due_queue.get("ok")),\n'
    '                                "count": len(due_loops),\n'
    '                                "dueCount": int(due_queue.get("dueCount") or 0),\n'
    '                                "waitingCount": int(due_queue.get("waitingCount") or 0),\n'
    '                                "loops": [row.get("loopKey") for row in due_loops]}}\n'
)

NEW_OUTCOME = (
    '                    {"action": "due_review_loops",\n'
    '                     "result": {"ok": bool(due_queue.get("ok")),\n'
    '                                "count": len(due_loops),\n'
    '                                "dueCount": int(due_queue.get("dueCount") or 0),\n'
    '                                "waitingCount": int(due_queue.get("waitingCount") or 0),\n'
    '                                "loops": [row.get("loopKey") for row in due_loops],\n'
    '                                "walkStoppedAt": due_queue.get("walkStoppedAt"),\n'
    '                                "walkStoppedReason": due_queue.get("walkStoppedReason"),\n'
    '                                "queueNeedsRefresh": bool(due_queue.get("queueNeedsRefresh")),\n'
    '                                "restartFromHead": bool(due_queue.get("restartFromHead")),\n'
    '                                "queueRefreshes": int(walk.get("refreshes") or 0)}}\n'
)


REPLACEMENTS: list[tuple[str, str]] = [
    (OLD_ANSWER, NEW_ANSWER),
    (OLD_ANCHOR, NEW_FUNCTION),
    (OLD_CLAIM_COMMENT, NEW_CLAIM_COMMENT),
    (OLD_WALK, NEW_WALK),
    (OLD_OUTCOME, NEW_OUTCOME),
]


def main() -> int:
    source = HEALER.read_text(encoding="utf-8")
    for old, new in REPLACEMENTS:
        if new in source and old not in source:
            # The replacement is already in place: the patch is run once
            # per round, and a second run must not fail on its own work.
            continue
        if old not in source:
            raise SystemExit(f"pattern not found: {old[:80]!r}")
        if source.count(old) != 1:
            raise SystemExit(f"pattern is not unique: {old[:80]!r}")
        source = source.replace(old, new, 1)
    HEALER.write_text(source, encoding="utf-8", newline="\n")
    print(f"patched {HEALER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
