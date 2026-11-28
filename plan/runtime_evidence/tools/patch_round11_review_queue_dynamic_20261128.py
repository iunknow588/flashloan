"""Round 11: the review queue is dynamic -- a claim no longer rewrites the
review moment of a closed loop.

The healer is patched by **substring replacement**, because the sentences
that carry the rule are not unique enough for a line based patch.
"""

from pathlib import Path


HEALER = Path(__file__).resolve().parents[3] / "scripts" / "mainnet_arbitrage_healer.py"


REPLACEMENTS: list[tuple[str, str]] = [
    (
        "        if is_review_sentinel(due_at):\n"
        "            # The loop is being measured right now, or it waits for the\n"
        "            # receipt of its trade: it is working, not quiet.\n"
        "            continue\n",
        "        if is_review_sentinel(due_at) or str(\n"
        "                schedule.get(\"runtimeStatus\") or \"\") in {\n"
        "                \"review_pending\", \"broadcast_pending\"}:\n"
        "            # The loop is being measured right now, or it waits for the\n"
        "            # receipt of its trade: it is working, not quiet.  The state\n"
        "            # of the loop says so: a claim keeps the moment the loop was\n"
        "            # scheduled at, so the marker is not there to be read.\n"
        "            continue\n",
    ),
    (
        "    A claim parks the loop at the review sentinel, and a parked loop is\n"
        "    and it is not claimed a second time.  The claim does **not** rewrite\n",
        "    A claim takes the loop: the loop waits for the round that is running\n"
        "    and it is not claimed a second time.  The claim does **not** rewrite\n",
    ),
    (
        "        \" WHERE runtime_status = 'review_pending' AND next_review_at = ?1\",\n"
        "        (REVIEW_PENDING_SENTINEL_AT,),\n",
        "        \" WHERE runtime_status = 'review_pending'\",\n"
        "        (),\n",
    ),
    (
        "# A review claim parks its loop at the review sentinel until the round it\n"
        "# started lands, and a parked loop is never due again and never claimed\n"
        "# again.  Only the healer takes a claim, so a claim that is older than\n"
        "# this window belongs to a healer that is gone, and the loop it parked\n"
        "# has to be given back.  The window is far longer than a round of the\n"
        "# review takes, so a round that is still running is never mistaken for an\n"
        "# abandoned one.\n",
        "# A review claim takes the loop until the round it started lands, and a\n"
        "# taken loop is never due again and never claimed again.  Only the healer\n"
        "# takes a claim, so a claim that is older than this window belongs to a\n"
        "# healer that is gone, and the loop it took has to be given back.  The\n"
        "# window is far longer than a round of the review takes, so a round that is\n"
        "# still running is never mistaken for an abandoned one.\n",
    ),
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
