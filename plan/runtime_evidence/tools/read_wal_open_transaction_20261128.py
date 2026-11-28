#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the pages the store's **open** write transaction is changing.

The store is in WAL mode, so a transaction that has not committed yet has
already written its pages into the ``-wal`` file -- only the commit marker is
missing.  The frames after the last commit frame therefore belong to the
transaction that is open right now, and the page numbers in them name the
tables the transaction touches.

The probe is **read only**: it opens the WAL file, walks the frames and maps
the page numbers onto the root pages of the tables.  It never opens the
store itself, so it cannot take a lock or change a row.

Usage::

    python read_wal_open_transaction_20261128.py
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
        frames.append(
            {
                "index": index,
                "pageNumber": page_number,
                "commit": db_size != 0,
                "dbSize": db_size,
            }
        )
        offset += frame_size
        index += 1
    return frames, page_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="watch the wal for this long and keep the longest open transaction",
    )
    args = parser.parse_args()

    wal = args.database.with_name(args.database.name + "-wal")
    if not wal.exists():
        print(json.dumps({"error": f"the wal file is missing: {wal}"}))
        return 1

    connection = sqlite3.connect(
        f"file:{args.database}?mode=ro", uri=True, timeout=30
    )
    try:
        rows = connection.execute(
            "SELECT name, rootpage FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    root_pages = {int(rootpage): name for name, rootpage in rows if rootpage}

    def snapshot() -> tuple[int, list[dict]]:
        frames, size = wal_frames(wal)
        last_commit = -1
        for frame in frames:
            if frame["commit"]:
                last_commit = frame["index"]
        return size, frames[last_commit + 1 :]

    frames, page_size = wal_frames(wal)
    page_size, open_frames = snapshot()
    seen_open = len(open_frames)
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        time.sleep(0.05)
        _, current = snapshot()
        if len(current) > seen_open:
            seen_open = len(current)
            open_frames = current

    pages = Counter(frame["pageNumber"] for frame in open_frames)
    touched = {
        root_pages[page]: count for page, count in pages.items() if page in root_pages
    }
    all_pages = Counter(frame["pageNumber"] for frame in frames)
    written = {
        root_pages[page]: count
        for page, count in all_pages.items()
        if page in root_pages
    }
    payload = {
        "walBytes": wal.stat().st_size,
        "pageSize": page_size,
        "watchedSeconds": args.seconds,
        "frames": len(frames),
        "openFrames": len(open_frames),
        "openPages": sorted(pages),
        "openRootPages": touched,
        "walRootPages": written,
        "unmappedOpenPages": sorted(
            page for page in pages if page not in root_pages
        )[:64],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    Path(__file__).with_name(
        "read_wal_open_transaction_20261128_result.json"
    ).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
