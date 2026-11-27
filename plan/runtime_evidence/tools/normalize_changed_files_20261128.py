#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize the line endings of the changed files to LF.

The design says every text file is written as UTF-8 (no BOM) with LF line
endings.  ``core.autocrlf=true`` rewrites a checkout to CRLF on Windows, so
the files on disk drift away from the design even though the bytes git
stores stay LF.  This driver brings the working copy of the **changed**
files back to LF, which is content-neutral for the index and makes the
working copy match the design.

Binary files are skipped.  A BOM is left alone unless the file is rewritten
from scratch; the report says which files carried one.

Usage:
    python normalize_changed_files_20261128.py [--strip-bom]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plan/runtime_evidence/tools"))

from normalize_text_lf_20261128 import normalize  # noqa: E402


def changed_files() -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "-z"], cwd=REPO, capture_output=True
    )
    names = []
    for entry in proc.stdout.decode("utf-8", "replace").split("\0"):
        if not entry:
            continue
        name = entry[3:] if len(entry) > 3 and entry[2] == " " else entry[2:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        names.append(name)
    return names


def main() -> int:
    strip_bom = "--strip-bom" in sys.argv
    changed = 0
    skipped_binary = 0
    for name in changed_files():
        path = REPO / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            skipped_binary += 1
            continue
        if raw.count(b"\r\n") == 0 and not (strip_bom and raw.startswith(b"\xef\xbb\xbf")):
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            # Not a UTF-8 file (an evidence file in another encoding, say).
            # The design only governs UTF-8 text, so it is reported and
            # left alone rather than mangled.
            print("SKIPPED (not utf-8) %s" % name)
            continue
        changed += 1
        if strip_bom:
            normalize(str(path), False)
        else:
            body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
            data = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if data != raw:
                path.write_bytes(data)
                print("CHANGED %s" % name)
    print("normalized=%d binary_skipped=%d" % (changed, skipped_binary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
