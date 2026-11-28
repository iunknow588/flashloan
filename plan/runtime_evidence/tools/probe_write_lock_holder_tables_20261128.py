#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name the tables inside the write transactions that hold the write lock.

The store runs in WAL mode, so a transaction that has **not** committed yet
has already written its pages into the ``-wal`` file -- only the commit
marker is missing.  The frames after the last commit frame therefore belong
to the transaction that is open right now, and the page numbers in them name
the tables the transaction touches.

`read_wal_open_transaction_20261128.py` already reads those frames, but it
only maps the **root** pages onto tables, and it took a single snapshot.  This
probe watches the WAL over a window, keeps **every** transaction it sees with
its duration, and attributes the pages of each transaction to tables:

* a table whose root page is a leaf page writes its root page on every
  change (the root page *is* the table), and
* a big table writes the **interior** pages on the path to the changed leaf,
  so the interior pages of every b-tree are collected as well.

Collecting the interior pages reads the interior page headers only -- the
leaf pages are never touched, so the 18 GB of overflow pages are not read.

The probe is **read only**: it opens the WAL file and the database file
directly and never opens the store itself.

Usage::

    python probe_write_lock_holder_tables_20261128.py --seconds 120
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import time
from collections import Counter
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

INTERIOR_TABLE = 0x05
INTERIOR_INDEX = 0x02


def wal_frames(wal: Path) -> tuple[list[dict], int]:
    raw = wal.read_bytes()
    if len(raw) < 32:
        return [], 0
    magic, _version, page_size = struct.unpack(">III", raw[:12])
    if page_size == 1:
        page_size = 65536
    frame_size = 24 + page_size
    frames: list[dict] = []
    offset = 32
    index = 0
    while offset + frame_size <= len(raw):
        page_number, db_size = struct.unpack(">II", raw[offset : offset + 8])
        frames.append({"index": index, "pageNumber": page_number, "commit": db_size != 0})
        offset += frame_size
        index += 1
    return frames, page_size


class PageReader:
    """Read page headers of the database file without touching the store."""

    def __init__(self, path: Path) -> None:
        self.handle = path.open("rb")
        header = self.handle.read(100)
        self.page_size = int.from_bytes(header[16:18], "big")
        if self.page_size == 1:
            self.page_size = 65536
        self.reserved = header[20]
        self.usable = self.page_size - self.reserved

    def close(self) -> None:
        self.handle.close()

    def byte_at(self, number: int, offset: int = 0) -> int:
        self.handle.seek((number - 1) * self.page_size + offset)
        raw = self.handle.read(1)
        if not raw:
            raise IndexError(number)
        return raw[0]

    def children(self, number: int) -> list[int]:
        """Return the child page numbers of an interior page."""
        self.handle.seek((number - 1) * self.page_size)
        buffer = self.handle.read(self.page_size)
        offset = 100 if number == 1 else 0
        kind = buffer[offset]
        if kind not in (INTERIOR_TABLE, INTERIOR_INDEX):
            return []
        cells = int.from_bytes(buffer[offset + 3 : offset + 5], "big")
        right = int.from_bytes(buffer[offset + 8 : offset + 12], "big")
        out = [right]
        for index in range(cells):
            pointer = int.from_bytes(
                buffer[offset + 12 + index * 2 : offset + 14 + index * 2], "big"
            )
            out.append(int.from_bytes(buffer[pointer : pointer + 4], "big"))
        return out


def collect_interior_pages(reader: PageReader, roots: dict[int, str]) -> dict[int, str]:
    """Map every interior page of every b-tree onto the object it belongs to."""
    interior: dict[int, str] = {}
    for root, name in roots.items():
        pending = [root]
        visited: set[int] = set()
        while pending:
            number = pending.pop()
            if number in visited or number < 1:
                continue
            visited.add(number)
            try:
                kind = reader.byte_at(number, 100 if number == 1 else 0)
            except IndexError:
                continue
            if kind not in (INTERIOR_TABLE, INTERIOR_INDEX):
                continue
            interior[number] = name
            try:
                pending.extend(reader.children(number))
            except (IndexError, OSError):
                continue
    return interior


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()

    wal = args.database.with_name(args.database.name + "-wal")
    if not wal.exists():
        print(json.dumps({"error": f"the wal file is missing: {wal}"}))
        return 1

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True, timeout=30)
    try:
        rows = connection.execute(
            "SELECT name, rootpage FROM sqlite_master WHERE type IN ('table','index')"
        ).fetchall()
    finally:
        connection.close()
    roots = {int(rootpage): name for name, rootpage in rows if rootpage}

    reader = PageReader(args.database)
    interior = collect_interior_pages(reader, roots)
    reader.close()

    def attribute(pages: Counter) -> dict:
        names = Counter()
        unknown = 0
        for page, hits in pages.items():
            name = roots.get(page) or interior.get(page)
            if name is None:
                unknown += hits
            else:
                names[name] += hits
        return {"tables": dict(names.most_common()), "unmappedFrames": unknown}

    transactions: list[dict] = []
    seen_open: dict | None = None
    last_commit_index = -1
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        time.sleep(0.05)
        frames, _size = wal_frames(wal)
        commits = [frame["index"] for frame in frames if frame["commit"]]
        if not commits:
            continue
        last = commits[-1]
        open_frames = frames[last + 1 :]
        if last != last_commit_index:
            # A transaction just committed: the frames before it are done.
            if seen_open is not None:
                transactions.append(seen_open)
            seen_open = None
            last_commit_index = last
        if open_frames:
            pages = Counter(frame["pageNumber"] for frame in open_frames)
            if seen_open is None:
                seen_open = {
                    "frames": len(open_frames),
                    "attribution": attribute(pages),
                    "startedAt": time.time(),
                }
            elif len(open_frames) > seen_open["frames"]:
                seen_open["frames"] = len(open_frames)
                seen_open["attribution"] = attribute(pages)
    if seen_open is not None:
        seen_open["stillOpenAtEnd"] = True
        transactions.append(seen_open)

    payload = {
        "tool": "probe_write_lock_holder_tables_20261128",
        "database": str(args.database),
        "watchedSeconds": args.seconds,
        "interiorPagesKnown": len(interior),
        "transactions": transactions,
        "note": (
            "every entry is one write transaction that the wal shows; "
            "the frames after the last commit frame are the pages it wrote"
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    Path(__file__).with_name(
        "probe_write_lock_holder_tables_20261128_result.json"
    ).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
