#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Count how many write transactions the store commits per minute.

Every committed write transaction leaves one **commit frame** in the WAL, so
the commit frames of the WAL are the write transactions of the store.  The
probe counts them over a window and reports the rate, which is the baseline
the write-transaction work needs: a runtime that issues its state writes one
transaction at a time shows a low rate, a runtime that merges them shows a
rate that drops by an order of magnitude.

The probe only reads the WAL file.  A checkpoint resets the WAL, so the
counter restarts when the file shrinks instead of counting a negative delta.

Usage::

    python probe_write_commit_rate_20261128.py --seconds 60
"""

from __future__ import annotations

import argparse
import json
import struct
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def wal_state(wal: Path) -> tuple[int, int, int]:
    """Return (frames, commits, page size) of the WAL file."""
    raw = wal.read_bytes()
    if len(raw) < 32:
        return 0, 0, 0
    magic, _version, page_size = struct.unpack(">III", raw[:12])
    if page_size == 1:
        page_size = 65536
    frame_size = 24 + page_size
    frames = 0
    commits = 0
    offset = 32
    while offset + frame_size <= len(raw):
        _page_number, db_size = struct.unpack(">II", raw[offset : offset + 8])
        frames += 1
        if db_size != 0:
            commits += 1
        offset += frame_size
    return frames, commits, page_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    wal = args.database.with_name(args.database.name + "-wal")
    if not wal.exists():
        print(json.dumps({"error": f"the wal file is missing: {wal}"}))
        return 1

    started = time.monotonic()
    deadline = started + args.seconds
    frames, commits, page_size = wal_state(wal)
    last_frames, last_commits = frames, commits
    epoch_commits = 0
    total_commits = 0
    resets = 0
    samples = 0
    commit_at: list[float] = []
    last_seen_commits = commits
    while time.monotonic() < deadline:
        time.sleep(args.interval)
        samples += 1
        frames, commits, _size = wal_state(wal)
        if frames < last_frames:
            # The WAL was reset by a checkpoint: the epoch ends here.
            total_commits += max(0, epoch_commits)
            resets += 1
            last_frames, last_commits = frames, commits
            last_seen_commits = commits
            epoch_commits = 0
            continue
        if commits > last_seen_commits:
            now = time.monotonic() - started
            for _ in range(commits - last_seen_commits):
                commit_at.append(round(now, 3))
        last_seen_commits = commits
        epoch_commits = max(epoch_commits, commits - last_commits)
        last_frames, last_commits = frames, commits

    elapsed = time.monotonic() - started
    total_commits += max(0, epoch_commits)
    frames, commits, page_size = wal_state(wal)
    payload = {
        "seconds": round(elapsed, 3),
        "samples": samples,
        "walResets": resets,
        "writeTransactions": total_commits,
        "writeTransactionsPerMinute": round(total_commits / max(1e-9, elapsed) * 60.0, 2),
        "commitAtSeconds": commit_at,
        "commitGapsSeconds": [
            round(commit_at[index] - commit_at[index - 1], 3)
            for index in range(1, len(commit_at))
        ],
        "pageSize": page_size,
        "walFrames": frames,
        "walCommitFrames": commits,
        "walBytes": wal.stat().st_size,
        "note": "the wal holds the transactions since the last checkpoint",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    Path(__file__).with_name(
        "probe_write_commit_rate_20261128_result.json"
    ).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
