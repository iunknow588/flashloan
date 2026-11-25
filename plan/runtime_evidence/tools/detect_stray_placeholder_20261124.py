#!/usr/bin/env python3
"""Detect stray U+00A7 placeholders that should have been backticks.

Context: an earlier patch round used `\u00a7` as a stand-in for U+0060 inside
apply_patch payloads and some of them were never converted back.  A *legit*
section reference looks like `\u00a71.2.9` / `\u00a7D-104` / `\u00a7AC-x`; a
*stray* one wraps a code span (`\u00a7closedLoopKey\u00a7`) or opens a fence
(`\u00a7\u00a7\u00a7`).

Read-only: prints file:line with the offending text, never writes.
"""

import re
import sys
from pathlib import Path

SECT = "\u00a7"


def classify(line: str) -> list[tuple[int, str]]:
    bad: list[tuple[int, str]] = []
    for m in re.finditer(re.escape(SECT), line):
        i = m.start()
        nxt = line[i + 1] if i + 1 < len(line) else ""
        prv = line[i - 1] if i > 0 else ""
        # opening: followed by lowercase / underscore / bracket / another sect
        if nxt and (nxt.islower() or nxt in "_[" or nxt == SECT):
            bad.append((i, "open"))
            continue
        # closing: preceded by an identifier-ish char, next char is not a
        # section-reference continuation (digit or upper-case letter)
        if prv and (prv.isalnum() or prv in "/):._\u2026\"]"):
            if not (nxt.isdigit() or (nxt.isupper() and not nxt.isalpha())):
                if not (nxt.isupper() and nxt.isalpha() and prv.isupper()):
                    bad.append((i, "close"))
    return bad


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: detect_stray_placeholder_20261124.py <file> [<file> ...]")
        return 2
    total = 0
    for arg in argv[1:]:
        path = Path(arg)
        if not path.is_file():
            print(f"[skip] {arg} (not a file)")
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            hits = classify(line)
            if hits:
                total += len(hits)
                kinds = ",".join(k for _, k in hits)
                print(f"{path}:{lineno}: [{kinds}] {line.strip()[:200]}")
    print(f"---STRAY-DETECT-DONE total={total}---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
