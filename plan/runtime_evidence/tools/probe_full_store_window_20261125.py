#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wrap a command in a whole-store snapshot window (read only).

§probe_full_store_snapshot_20261125.py§ takes the snapshots; this tool runs
them **around** a command so that a full pytest run can be compared with a
silent control window of the same length.

The snapshot is taken with §mode=ro§, so the measurement itself can never
write into the production store.

Usage:
    python probe_full_store_window_20261125.py --db <path> --label <name>
                                                --out <json>
                                                --command-file <txt>
                                                [--interval 0.4]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_full_store_snapshot_20261125 import diff, snapshot  # noqa: E402


ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--command-file", required=True)
    parser.add_argument("--content-max-rows", type=int, default=20000)
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    with open(os.path.abspath(args.command_file), "r", encoding="utf-8") as handle:
        command_line = handle.read().strip()

    started = time.monotonic()
    before = snapshot(db_path, content_max_rows=args.content_max_rows)
    command = subprocess.run(
        command_line,
        shell=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
    )
    after = snapshot(db_path, content_max_rows=args.content_max_rows)
    elapsed = round(time.monotonic() - started, 3)

    report = {
        "label": args.label,
        "generatedAt": _now(),
        "database": db_path,
        "command": command_line,
        "commandExitCode": command.returncode,
        "commandStdoutTail": command.stdout[-2000:],
        "commandStderrTail": command.stderr[-2000:],
        "windowSeconds": elapsed,
        "diff": diff(before, after),
        "before": before,
        "after": after,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")
    print(text[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
