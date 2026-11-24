#!/usr/bin/env python3
"""Read-only probe: does the record layer keep the **two directions apart**?

The pool refactor gives every token set **two** closed loops (direction 0 and
direction 1).  The record layer must not merge them:

* the **review** audit stream writes **one file per direction closed loop**
  (``review_record_jsonl/<链路>/<chain_id>/<YYYY-MM-DD>/<闭环>.jsonl``);
* the **broadcast** records write **one monthly SQLite per chain** that holds
  **every** direction (``broadcast_transaction_db/<链路>/<chain_id>/<YYYY-MM>/``).

This probe answers three questions against the **production** store, read-only:

1. do the two mirrored closed loops really have different identities?
2. would the Rust file stem put them into **two different files**?
3. does every review row key on a closed loop that exists?

It never writes anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict


def repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


ROOT = repo_root()
DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")


def loop_file_stem(loop_key: str) -> str:
    """The Rust `loop_file_stem()` of ``review_manager::records`` — same rule.

    Every character that is not ``[0-9A-Za-z._-]`` becomes ``_``; the stem is
    cut to 64 characters and the first six bytes of the SHA-256 of the **raw**
    key are appended, so two different keys can never share a file name.
    """
    safe = "".join(
        character
        if (character.isascii() and (character.isalnum() or character in ".-_"))
        else "_"
        for character in loop_key
    ).strip(".")
    if not safe:
        safe = "loop"
    safe = safe[:64]
    digest = hashlib.sha256(loop_key.encode("utf-8")).hexdigest()[:12]
    return "%s_%s" % (safe, digest)


def main() -> int:
    if not os.path.exists(DB):
        print("production store not found: %s" % DB)
        return 2
    connection = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    connection.execute("PRAGMA query_only = ON")

    loops = connection.execute(
        "SELECT loop_key, anchor_address, token_y_key_id, token_z_key_id "
        "FROM manager_v4_closed_loops"
    ).fetchall()
    hops = defaultdict(list)
    for loop_key, hop_index, direction in connection.execute(
        "SELECT loop_key, hop_index, hop_direction FROM manager_v4_closed_loop_hops"
    ):
        hops[loop_key].append((hop_index, direction))

    groups = defaultdict(list)
    for loop_key, anchor, token_y, token_z in loops:
        groups[frozenset([str(anchor), str(token_y), str(token_z)])].append(loop_key)

    print("=== closed loops ===")
    print("closed loops            : %d" % len(loops))
    print("distinct loop_key       : %d" % len({row[0] for row in loops}))
    print("token sets              : %d" % len(groups))
    sizes = Counter(len(members) for members in groups.values())
    print("loops per token set     : %s" % dict(sizes))

    direction_histogram = Counter()
    for members in groups.values():
        for loop_key in members:
            for _hop_index, direction in hops.get(loop_key, []):
                direction_histogram[direction] += 1
    print("hop_direction histogram : %s" % dict(sorted(direction_histogram.items())))

    # --- 1 + 2: the mirrored pair must differ in identity **and** in file ---
    same_identity = 0
    same_file = 0
    complementary = 0
    checked = 0
    for members in groups.values():
        if len(members) != 2:
            continue
        checked += 1
        left, right = members
        if left == right:
            same_identity += 1
        if loop_file_stem(left) == loop_file_stem(right):
            same_file += 1
        # 逐跳比较：镜像闭环的**每一跳**方向都必须相反（0 <-> 1）。
        left_by_hop = {hop_index: direction for hop_index, direction in hops.get(left, [])}
        right_by_hop = {hop_index: direction for hop_index, direction in hops.get(right, [])}
        if len(left_by_hop) == 3 and len(right_by_hop) == 3:
            if all(
                left_by_hop[index] != right_by_hop[index] for index in left_by_hop
            ):
                complementary += 1
    print()
    print("=== direction split ===")
    print("mirrored pairs checked  : %d" % checked)
    print("pairs with same identity: %d  (must be 0)" % same_identity)
    print("pairs sharing a file    : %d  (must be 0)" % same_file)
    print("pairs with complementary hop directions: %d / %d" % (complementary, checked))

    # --- 3: every review keys on a closed loop that exists ---
    review_rows, review_keys = connection.execute(
        "SELECT COUNT(*), COUNT(DISTINCT loop_key) FROM manager_v4_reviews"
    ).fetchone()
    known = {row[0] for row in loops}
    orphans = connection.execute(
        "SELECT COUNT(*) FROM manager_v4_reviews "
        "WHERE loop_key NOT IN (SELECT loop_key FROM manager_v4_closed_loops)"
    ).fetchone()[0]
    print()
    print("=== reviews ===")
    print("review rows             : %d (distinct loop_key %d)" % (review_rows, review_keys))
    print("reviews without a loop  : %d  (must be 0)" % orphans)

    # --- the record trees the production store would use ---
    record_root = os.path.join(ROOT, "flashloan", "db")
    print()
    print("=== record roots (derived from the production store path) ===")
    print("reviews : %s" % os.path.join(record_root, "review_record_jsonl"))
    print("trades  : %s" % os.path.join(record_root, "broadcast_transaction_db"))
    for name in ("review_record_jsonl", "broadcast_transaction_db"):
        path = os.path.join(record_root, name)
        print("%-24s exists=%s" % (name, os.path.isdir(path)))

    ok = same_identity == 0 and same_file == 0 and orphans == 0 and complementary == checked
    print()
    print("verdict: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
