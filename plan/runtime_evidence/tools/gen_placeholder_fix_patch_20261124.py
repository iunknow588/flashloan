#!/usr/bin/env python3
"""Generate an apply_patch payload that converts stray U+00A7 code-span
placeholders back into real backticks (U+0060).

Rule (applied per line, left to right):

1. A run of three or more ``\u00a7`` is a code fence -> backticks.
2. A ``\u00a7`` followed by a **section-reference token** (starts with an
   upper-case letter, a digit or a CJK char; continues with the same class
   plus ``.`` and ``-``) is a legitimate anchor such as ``\u00a7M-8``,
   ``\u00a7D-048``, ``\u00a7A2``, ``\u00a74.2.2``.  It is left untouched;
   when the very next character is another ``\u00a7`` that second one is the
   closing delimiter of the same reference and is left untouched too.
3. Any other ``\u00a7`` is a **code-span delimiter**: it and the next
   ``\u00a7`` on the same line become backticks.
4. A ``\u00a7`` with no partner on the line is left untouched.

The script only *prints* a patch; it never writes to the target files.
"""

import re
import sys
from pathlib import Path

SECT = "\u00a7"
BT = chr(96)
SECT_TOKEN = re.compile(r"[A-Z0-9\u4e00-\u9fff][A-Z0-9\u4e00-\u9fff.\-]*")


def fix_line(line: str) -> str:
    if SECT not in line:
        return line
    out = re.sub(SECT + "{3,}", lambda m: BT * len(m.group(0)), line)
    chars = list(out)
    i = 0
    n = len(chars)
    in_span = False
    while i < n:
        if chars[i] == BT:
            in_span = not in_span
            i += 1
            continue
        if chars[i] != SECT:
            i += 1
            continue
        if in_span:
            # a stray placeholder closing a real backtick-delimited span
            chars[i] = BT
            in_span = False
            i += 1
            continue
        m = SECT_TOKEN.match(out, i + 1)
        if m:
            # legitimate section reference -> skip token (and its closing §)
            i = m.end()
            if i < n and chars[i] == SECT:
                i += 1
            continue
        # code-span delimiter -> pair with the next § on this line
        j = out.find(SECT, i + 1)
        if j == -1:
            i += 1
            continue
        chars[i] = BT
        chars[j] = BT
        i = j + 1
    return "".join(chars)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: gen_placeholder_fix_patch_20261124.py <file> [<file> ...]")
        return 2
    print("*** Begin Patch")
    changed = 0
    for arg in argv[1:]:
        path = Path(arg)
        text = path.read_text(encoding="utf-8")
        raw = text.split("\n")
        new = [fix_line(line) for line in raw]
        hunks = []
        for lineno, (old, cur) in enumerate(zip(raw, new), start=1):
            if old == cur:
                continue
            changed += 1
            hunks.append((old, cur))
        if not hunks:
            continue
        print(f"*** Update File: {path.resolve()}")
        for old, cur in hunks:
            print("@@")
            print(f"-{old}")
            print(f"+{cur}")
    print("*** End Patch")
    print(f"---GEN-PATCH lines={changed}---", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
