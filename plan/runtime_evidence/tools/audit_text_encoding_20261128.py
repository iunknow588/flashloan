#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit the byte-level text encoding of every tracked file.

The repository design says that every text file is written as
**UTF-8 without BOM** and **LF** line endings.  This script proves that
claim against the two places where bytes actually live:

* the **index** -- what git stores and what a push carries.  Read with
  ``git ls-files --eol`` (``i/`` field) and ``git cat-file``.
* the **worktree** -- what sits on disk right now.  Read from the file
  bytes directly.

``core.autocrlf=true`` means the index is always LF while a Windows
checkout rewrites the worktree copy to CRLF.  That is a checkout
artifact, not a data difference: the bytes that are committed stay LF.

The report is written as UTF-8 (no BOM) with LF line endings.

Usage:
    python audit_text_encoding_20261128.py [--json <path>]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.stdout.reconfigure(encoding="utf-8")


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return proc.stdout


def classify(raw: bytes) -> tuple[bool, str]:
    """Return (has_bom, line_ending_kind) for a byte blob."""
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    crlf = body.count(b"\r\n")
    lone_lf = body.count(b"\n") - crlf
    lone_cr = body.count(b"\r") - crlf
    if crlf and not lone_lf and not lone_cr:
        return bom, "crlf"
    if lone_lf and not crlf and not lone_cr:
        return bom, "lf"
    if not body:
        return bom, "empty"
    if lone_cr and not lone_lf and not crlf:
        return bom, "cr"
    return bom, "mixed"


def main() -> int:
    out_json = None
    if "--json" in sys.argv:
        out_json = Path(sys.argv[sys.argv.index("--json") + 1])
    changed_only = "--changed-only" in sys.argv

    # core.quotePath=false keeps non-ASCII paths readable so the two lists
    # (names and eol attributes) can be matched one to one.
    eol_lines = git("-c", "core.quotePath=false", "ls-files", "--eol").splitlines()
    index_kind: dict[str, str] = {}
    for line in eol_lines:
        fields = line.split("\t", 1)
        if len(fields) != 2:
            continue
        head = fields[0].split()
        if not head:
            continue
        index_kind[fields[1]] = head[0]

    buckets: dict[str, int] = {}
    index_buckets: dict[str, int] = {}
    bom_files: list[str] = []
    exceptions: list[dict] = []
    worktree_crlf: list[str] = []
    scanned = 0

    names = git("-c", "core.quotePath=false", "ls-files", "-z").split("\0")
    if changed_only:
        dirty = set()
        for line in git("status", "--porcelain", "-z").split("\0"):
            if not line:
                continue
            name = line[3:] if line[2] == " " else line[2:]
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            dirty.add(name)
        names = [name for name in names if name in dirty]
    for count, name in enumerate(names, start=1):
        if not name:
            continue
        if count % 500 == 0:
            print("  ... %d/%d" % (count, len(names)), file=sys.stderr, flush=True)
        path = ROOT / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            index_buckets["binary"] = index_buckets.get("binary", 0) + 1
            continue
        scanned += 1
        bom, kind = classify(raw)
        buckets[kind] = buckets.get(kind, 0) + 1
        if bom:
            bom_files.append(name)
        if kind == "crlf":
            worktree_crlf.append(name)
        ik = index_kind.get(name, "unknown")
        index_buckets[ik] = index_buckets.get(ik, 0) + 1
        if ik not in ("i/lf", "i/-text", "i/none", "unknown"):
            exceptions.append(
                {"path": name, "index": ik, "worktree": kind, "bom": bom}
            )

    report = {
        "tool": "audit_text_encoding_20261128",
        "design": "UTF-8 no BOM, LF line endings",
        "scannedTextFiles": scanned,
        "worktreeLineEndings": buckets,
        "indexLineEndings": index_buckets,
        "filesWithBom": bom_files,
        "indexExceptions": exceptions,
        "worktreeCrlfCount": len(worktree_crlf),
        "worktreeCrlfSample": worktree_crlf[:12],
        "scope": "changed-only" if changed_only else "all-tracked",
    }

    print("scanned text files      : %d" % scanned)
    print("worktree line endings   : %s" % json.dumps(buckets, ensure_ascii=False))
    print("index    line endings   : %s" % json.dumps(index_buckets, ensure_ascii=False))
    print("files carrying a BOM    : %d %s" % (len(bom_files), bom_files[:6]))
    print("index CRLF/mixed files  : %d %s" % (len(exceptions), [e["path"] for e in exceptions]))
    print("worktree CRLF (checkout : %d files -- core.autocrlf=true artifact)" % len(worktree_crlf))

    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print("report -> %s" % out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
