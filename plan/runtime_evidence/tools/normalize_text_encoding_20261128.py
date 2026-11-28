#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rewrite the given files as UTF-8 without BOM and with LF line endings.

The project's text files are UTF-8 (no BOM) with LF line endings.  A file
that already carries a BOM is rewritten without it and a CRLF line ending
is rewritten as LF.  The rewrite is byte level: the content is decoded as
UTF-8, the BOM is dropped, the line endings are normalized and the file is
written back with ``newline="\n"`` so no platform specific ending is
introduced.

Usage::

    python normalize_text_encoding_20261128.py [--check] FILE [FILE ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


BOM = b"\xef\xbb\xbf"


def normalize(path: Path, *, check: bool) -> dict[str, object]:
    raw = path.read_bytes()
    had_bom = raw.startswith(BOM)
    body = raw[len(BOM):] if had_bom else raw
    text = body.decode("utf-8")
    crlf = text.count("\r\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    payload = text.encode("utf-8")
    changed = had_bom or crlf > 0
    if changed and not check:
        path.write_bytes(payload)
    return {
        "path": str(path),
        "hadBom": had_bom,
        "crlfLines": crlf,
        "changed": changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true",
                        help="report the state without writing")
    args = parser.parse_args()

    report = [normalize(path, check=args.check) for path in args.files]
    for item in report:
        print(f"{item['path']}: bom={item['hadBom']} crlf={item['crlfLines']} "
              f"changed={item['changed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
