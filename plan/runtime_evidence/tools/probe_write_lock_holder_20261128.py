#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find out which process holds the write lock of the local store.

The store has one writer at a time, and a writer that cannot take the lock
fails with ``database is locked``.  The probe tells that a lock is held, but
not **who** holds it, and the two candidates (the Manager API and the
Runtime) answer the same error.

This probe tells them apart by **pausing** one candidate at a time: a paused
process keeps the lock it already holds, so the store stays locked for the
holder and frees up for everybody else.  Pausing a process does not abort its
transaction -- the operating system freezes its threads -- so the probe is
reversible and leaves the store exactly as it found it.

Usage::

    python probe_write_lock_holder_20261128.py --candidates 29424,30348
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sqlite3
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def _nt_process(process_id: int, suspend: bool) -> None:
    """Freeze or wake every thread of a process."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    handle = kernel32.OpenProcess(0x0800 | 0x1000, False, process_id)
    if not handle:
        raise OSError(f"cannot open process {process_id}")
    try:
        entry = ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess
        status = entry(handle)
        if status != 0:
            raise OSError(
                f"{'suspend' if suspend else 'resume'} failed for {process_id}"
                f": NTSTATUS {status}"
            )
    finally:
        kernel32.CloseHandle(handle)


def probe(database: Path, samples: int, busy_timeout_ms: int) -> dict:
    """Take the write lock as often as the store lets it through."""
    locked = 0
    free = 0
    errors: list[str] = []
    for _ in range(samples):
        connection = sqlite3.connect(
            str(database), timeout=busy_timeout_ms / 1000.0, isolation_level=None
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ROLLBACK")
                free += 1
            except sqlite3.OperationalError as failure:
                locked += 1
                errors.append(str(failure))
        finally:
            connection.close()
    return {
        "samples": samples,
        "locked": locked,
        "free": free,
        "errors": errors[:3],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--candidates",
        default="",
        help="comma separated process ids to pause one at a time",
    )
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.7)
    parser.add_argument("--busy-timeout-ms", type=int, default=500)
    args = parser.parse_args()

    baseline = probe(args.database, args.samples, args.busy_timeout_ms)
    results = {"baseline": baseline, "candidates": []}
    for raw in [item.strip() for item in args.candidates.split(",") if item.strip()]:
        process_id = int(raw)
        entry: dict = {"pid": process_id}
        try:
            _nt_process(process_id, suspend=True)
            entry["paused"] = True
            time.sleep(args.interval)
            entry["whilePaused"] = probe(
                args.database, args.samples, args.busy_timeout_ms
            )
        except OSError as failure:
            entry["error"] = str(failure)
        finally:
            try:
                _nt_process(process_id, suspend=False)
                entry["resumed"] = True
            except OSError as failure:
                entry["resumeError"] = str(failure)
        results["candidates"].append(entry)

    payload = json.dumps(results, ensure_ascii=False, indent=2)
    print(payload)
    Path(__file__).with_name(
        "probe_write_lock_holder_20261128_result.json"
    ).write_text(payload + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
