"""Round 13: the walk over the review queue stops for one reason only.

The user put the rule down twice:

* "复核队列一旦读取到一个时间复核时间未到的队列就需要刷新，重头开始读取。"
* "不一定是读到哨兵，有可能是读取到一个未到点的闭环，不能使用哨兵作为循环结束的条件。"

The walk therefore ends at the **first** entry whose review time point
has not come -- whatever that entry is.  A loop that is parked at a marker
carries no time point of its own, so it ends the walk for that same
reason; the marker is a **description** of the stop, never a second end
of the walk of its own.

The healer is patched by **substring replacement**, because the sentences
that carry the rule are not unique enough for a line based patch.
"""

from pathlib import Path


HEALER = Path(__file__).resolve().parents[3] / "scripts" / "mainnet_arbitrage_healer.py"


OLD_ANSWER = (
    '            "walkStoppedAt": data.get("walkStoppedAt"),\n'
    '            "walkStoppedReason": data.get("walkStoppedReason"),\n'
    '            "queueNeedsRefresh": bool(data.get("queueNeedsRefresh")),\n'
)

NEW_ANSWER = (
    '            "walkStoppedAt": data.get("walkStoppedAt"),\n'
    '            "walkStoppedReason": data.get("walkStoppedReason"),\n'
    '            "walkStoppedAtIsSentinel": bool(data.get("walkStoppedAtIsSentinel")),\n'
    '            "queueNeedsRefresh": bool(data.get("queueNeedsRefresh")),\n'
)


OLD_RULE = (
    '    The queue is **dynamic**: the moments it is ordered by are written\n'
    '    while it is read, so a walk that stopped at a loop whose moment has\n'
    '    not come says nothing about the work behind it.  The queue is\n'
    '    therefore read again and the walk starts again from the head -- the\n'
    '    main thread never waits on the entry it stopped at.\n'
)

NEW_RULE = (
    '    The queue is **dynamic**: the moments it is ordered by are written\n'
    '    while it is read, so a walk that stopped at a loop whose moment has\n'
    '    not come says nothing about the work behind it.  The queue is\n'
    '    therefore read again and the walk starts again from the head -- the\n'
    '    main thread never waits on the entry it stopped at.\n'
    '\n'
    '    The walk stops for **one** reason only: the review time point of the\n'
    '    entry it stopped at has not come.  A loop that is parked at a marker\n'
    '    carries no time point of its own, so it stops the walk for that same\n'
    '    reason -- the marker is never an end of the walk of its own, and it\n'
    '    never hides an entry that is due behind it.\n'
)


OLD_JOURNAL = (
    '                                "walkStoppedReason": due_queue.get("walkStoppedReason"),\n'
    '                                "queueNeedsRefresh": bool(due_queue.get("queueNeedsRefresh")),\n'
)

NEW_JOURNAL = (
    '                                "walkStoppedReason": due_queue.get("walkStoppedReason"),\n'
    '                                "walkStoppedAtIsSentinel": bool(due_queue.get("walkStoppedAtIsSentinel")),\n'
    '                                "queueNeedsRefresh": bool(due_queue.get("queueNeedsRefresh")),\n'
)


REPLACEMENTS = (
    (OLD_ANSWER, NEW_ANSWER),
    (OLD_RULE, NEW_RULE),
    (OLD_JOURNAL, NEW_JOURNAL),
)


def main() -> int:
    source = HEALER.read_text(encoding="utf-8")
    for old, new in REPLACEMENTS:
        if new in source:
            continue
        count = source.count(old)
        if count != 1:
            raise SystemExit(f"anchor found {count} times: {old[:70]!r}")
        source = source.replace(old, new, 1)
    HEALER.write_text(source, encoding="utf-8", newline="\n")
    print("round 13 patched: the walk stops for one reason only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
