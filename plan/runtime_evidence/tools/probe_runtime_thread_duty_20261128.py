#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure where the runtime's threads spend their time.

The write lock of the main store is held almost all of the time, but the WAL
cannot say **which** statement holds it: a transaction writes its pages at
the moment it commits, so an open transaction is rarely visible in the WAL.

`py-spy` reads the Python stacks of a running process without touching it.
This probe samples the runtime repeatedly and tallies, per thread, the
innermost frame it was in -- so a thread that spends its life inside one
call (a schema migration, a chain measurement, a publish) shows up as a
thread with a single frame and a high duty cycle.

The probe is **read only**: it only reads stacks of a process it does not
modify, and it writes nothing to the store.

Usage::

    python probe_runtime_thread_duty_20261128.py --pid 27892 --samples 40
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_PY_SPY = Path(
    r"C:\Users\iunkn\AppData\Roaming\Python\Python312\Scripts\py-spy.exe"
)

THREAD_LINE = re.compile(r"^Thread (\d+)(?: \(([^)]*)\))?: \"([^\"]*)\"")
FRAME_LINE = re.compile(r"^\s{4}(\S+) \(([^)]*)\)")


def sample(pid: int, py_spy: Path) -> dict[str, list[str]]:
    """Return the stack of every thread of the process, top frame first."""
    completed = subprocess.run(
        [str(py_spy), "dump", "--pid", str(pid), "--nonblocking"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stacks: dict[str, list[str]] = defaultdict(list)
    current: str | None = None
    for line in completed.stdout.splitlines():
        header = THREAD_LINE.match(line)
        if header:
            current = header.group(3) or f"thread-{header.group(1)}"
            continue
        frame = FRAME_LINE.match(line)
        if frame and current is not None:
            stacks[current].append(f"{frame.group(1)} ({frame.group(2)})")
    return stacks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--py-spy", type=Path, default=DEFAULT_PY_SPY)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--interval", type=float, default=1.5)
    args = parser.parse_args()

    tops: dict[str, Counter] = defaultdict(Counter)
    seconds: dict[str, float] = defaultdict(float)
    samples = 0
    started = time.monotonic()
    for _ in range(args.samples):
        stacks = sample(args.pid, args.py_spy)
        samples += 1
        for thread, stack in stacks.items():
            if not stack:
                continue
            tops[thread][stack[0]] += 1
            seconds[thread] += args.interval
        time.sleep(args.interval)
    elapsed = time.monotonic() - started

    report = {
        "tool": "probe_runtime_thread_duty_20261128",
        "pid": args.pid,
        "samples": samples,
        "intervalSeconds": args.interval,
        "elapsedSeconds": round(elapsed, 1),
        "threads": {
            thread: {
                "topFrames": [
                    {"frame": frame, "samples": hits, "duty": round(hits / samples, 3)}
                    for frame, hits in counter.most_common(6)
                ]
            }
            for thread, counter in sorted(tops.items(), key=lambda item: -item[1].total())
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    Path(__file__).with_name("probe_runtime_thread_duty_20261128_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
