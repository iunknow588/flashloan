#!/usr/bin/env python3
"""Repair the two mangled characters of the D-262 document.

The document is written as UTF-8 without BOM and LF, and the repair
keeps both: the file is read as UTF-8, the two damaged spots are
rewritten, and the result is written back with ``newline=\"\\n\"``.
"""

from __future__ import annotations

import re
from pathlib import Path


DOCUMENT = Path(__file__).resolve().parents[3] / (
    "plan/tasks/P-01-\u6c60子重构/tasks/T-\u6c60\u7f16.02/docs/"
    "D-262-\u5e7f\u64ad\u7b56\u7565\u6539\u4e3a\u6b63\u5411\u76c8\u5229\u76f4\u53d1_20261128.md"
)


def main() -> int:
    text = DOCUMENT.read_text(encoding="utf-8")
    repaired = re.sub(
        "\u56e0\u4e3a\u5b83.{1,3}\u76f4\u63a5\u6267\u884c\u7684",
        "\u56e0\u4e3a\u5b83\u662f\u76f4\u63a5\u6267\u884c\u7684",
        text,
    )
    repaired = re.sub(
        "\u4e4b\u95f4\u90a3\u6bb5.{1,3}\u95f4\uff1a",
        "\u4e4b\u95f4\u90a3\u6bb5\u65f6\u95f4\uff1a",
        repaired,
    )
    if repaired == text:
        print("nothing to repair")
        return 0
    with DOCUMENT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(repaired)
    print(f"repaired {DOCUMENT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
