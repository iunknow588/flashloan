#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure how many pages every table of the store really occupies.

The store is 18.8 GB and every table anybody counted has a few thousand rows,
so the size has to live somewhere else -- in overflow pages of a table whose
payloads are large, or in an index nobody looked at.  ``PRAGMA`` does not
answer it (``dbstat`` is not compiled into this build), so the probe walks
the b-trees of the file itself and counts the pages, overflow chains
included.

The probe **only reads the file**.  It opens the store read-only for the
table list and reads the pages straight from disk, so it neither takes a lock
nor changes a row.

Usage::

    python measure_table_sizes_20261128.py --top 20
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"

LEAF_INDEX = 0x0A
INTERIOR_INDEX = 0x02
LEAF_TABLE = 0x0D
INTERIOR_TABLE = 0x05


def read_varint(buffer: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        byte = buffer[offset + index]
        if index == 8:
            return (value << 8) | byte, 9
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index + 1
    return value, 9


class Store:
    """A read-only reader over the pages of one SQLite file."""

    def __init__(self, path: Path) -> None:
        self.handle = path.open("rb")
        header = self.handle.read(100)
        self.page_size = int.from_bytes(header[16:18], "big")
        if self.page_size == 1:
            self.page_size = 65536
        self.reserved = header[20]
        self.usable = self.page_size - self.reserved
        self.max_local = self.usable - 35
        self.min_local = ((self.usable - 12) * 32 // 255) - 23

    def close(self) -> None:
        self.handle.close()

    def page(self, number: int) -> bytes:
        self.handle.seek((number - 1) * self.page_size)
        return self.handle.read(self.page_size)

    def count_table(self, root: int) -> dict:
        """Count the pages of one table b-tree, overflow chains included."""
        pages = 0
        overflow_pages = 0
        rows = 0
        first_page = None
        last_page = None
        pending = [root]
        visited: set[int] = set()
        while pending:
            number = pending.pop()
            if number in visited:
                continue
            visited.add(number)
            pages += 1
            first_page = number if first_page is None else min(first_page, number)
            last_page = number if last_page is None else max(last_page, number)
            buffer = self.page(number)
            offset = 100 if number == 1 else 0
            kind = buffer[offset]
            if kind == INTERIOR_TABLE:
                cells = int.from_bytes(buffer[offset + 3 : offset + 5], "big")
                right = int.from_bytes(
                    buffer[offset + 8 : offset + 12], "big"
                )
                pending.append(right)
                for index in range(cells):
                    pointer = int.from_bytes(
                        buffer[
                            offset + 12 + index * 2 : offset + 14 + index * 2
                        ],
                        "big",
                    )
                    child = int.from_bytes(buffer[pointer : pointer + 4], "big")
                    pending.append(child)
            elif kind == LEAF_TABLE:
                cells = int.from_bytes(buffer[offset + 3 : offset + 5], "big")
                rows += cells
                for index in range(cells):
                    pointer = int.from_bytes(
                        buffer[
                            offset + 8 + index * 2 : offset + 10 + index * 2
                        ],
                        "big",
                    )
                    payload, used = read_varint(buffer, pointer)
                    if payload <= self.max_local:
                        continue
                    local = self.min_local + (
                        (payload - self.min_local) % (self.usable - 4)
                    )
                    if local > self.max_local:
                        local = self.min_local
                    remaining = payload - local
                    overflow_pages += -(-remaining // (self.usable - 4))
        return {
            "pages": pages,
            "overflowPages": overflow_pages,
            "rows": rows,
            "firstPage": first_page,
            "lastPage": last_page,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    connection = sqlite3.connect(
        f"file:{args.database}?mode=ro", uri=True, timeout=30
    )
    try:
        entries = connection.execute(
            "SELECT name, rootpage, type FROM sqlite_master"
        ).fetchall()
    finally:
        connection.close()

    store = Store(args.database)
    results = []
    try:
        for name, rootpage, kind in entries:
            if kind not in ("table", "index") or not rootpage:
                continue
            try:
                counted = store.count_table(int(rootpage))
            except (IndexError, ValueError, OSError) as failure:
                counted = {"pages": 0, "overflowPages": 0, "rows": 0,
                           "error": str(failure)}
            counted["name"] = name
            counted["type"] = kind
            counted["bytes"] = counted["pages"] * store.page_size
            counted["bytesWithOverflow"] = (
                counted["pages"] + counted["overflowPages"]
            ) * store.page_size
            results.append(counted)
    finally:
        store.close()

    results.sort(key=lambda item: item["bytesWithOverflow"], reverse=True)
    payload = {
        "pageSize": store.page_size,
        "fileBytes": args.database.stat().st_size,
        "objects": results[: args.top],
        "measuredBytes": sum(item["bytesWithOverflow"] for item in results),
        "objectCount": len(results),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    Path(__file__).with_name(
        "measure_table_sizes_20261128_result.json"
    ).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
