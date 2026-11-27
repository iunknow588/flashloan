#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize the given files to **UTF-8 without BOM** and **LF** endings.

The repository design says every text file is written as UTF-8 with no
byte-order mark and LF line endings.  This script is the mechanical fix for
that contract: it reads each file as UTF-8 (tolerating a BOM), drops the
BOM, rewrites every line ending as LF, and writes the bytes back **without**
a BOM.

It reports ``CHANGED`` / ``same`` per file, and flags any U+FFFD
replacement character so a mis-decoded file is noticed rather than shipped.

It replaces ``normalize_crlf_20261125.py``, which normalized to CRLF.  CRLF
only ever existed in the checkout because ``core.autocrlf=true`` rewrites
LF to CRLF on the way out; the bytes git stores stay LF either way, and the
design asks for LF, so the normalizer now targets LF.

Usage:
    python normalize_text_lf_20261128.py <file> [<file> ...]
    python normalize_text_lf_20261128.py --line-endings-only <file> [<file> ...]
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")


def normalize(path, line_endings_only=False):
    with open(path, "rb") as handle:
        raw = handle.read()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    if line_endings_only:
        body = text.encode("utf-8")
        data = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        changed = data != raw
        if changed:
            with open(path, "wb") as handle:
                handle.write(data)
        print(("CHANGED " if changed else "same    ") + path
              + ("  (had BOM)" if had_bom else ""))
        return
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    data = text.encode("utf-8")
    changed = data != raw
    if changed:
        with open(path, "wb") as handle:
            handle.write(data)
    print(("CHANGED " if changed else "same    ") + path + ("  (had BOM)" if had_bom else ""))
    if "\ufffd" in text:
        for number, line in enumerate(text.splitlines(), start=1):
            if "\ufffd" in line:
                print("  U+FFFD %s:%d %s" % (path, number, line.strip()[:160]))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    line_endings_only = "--line-endings-only" in sys.argv
    suffix = None
    arguments = [path for path in sys.argv[1:] if path != "--line-endings-only"]
    if "--suffix" in arguments:
        index = arguments.index("--suffix")
        suffix = arguments[index + 1]
        del arguments[index:index + 2]
    paths = []
    for path in arguments:
        if os.path.isdir(path):
            for root, _dirs, names in os.walk(path):
                for name in names:
                    if suffix is None or name.endswith(suffix):
                        paths.append(os.path.join(root, name))
        else:
            paths.append(path)
    for path in paths:
        if not os.path.isfile(path):
            print("missing   %s" % path)
            continue
        normalize(path, line_endings_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
