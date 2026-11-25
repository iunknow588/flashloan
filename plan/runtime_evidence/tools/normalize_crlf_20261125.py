#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize the line endings of the given files to CRLF, without a BOM.

The plan documents are checked in with CRLF and no byte-order mark; a few
editors and patch tools write LF.  This script is the mechanical fix: it
reads each file as UTF-8 (tolerating a BOM), rewrites every line ending as
CRLF, and writes the bytes back **without** a BOM.

It reports ``CHANGED`` / ``same`` per file, and flags any U+FFFD
replacement character so a mis-decoded file is noticed rather than shipped.

Usage:
    python normalize_crlf_20261125.py <file> [<file> ...]
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")


def normalize(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
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
    for path in sys.argv[1:]:
        if not os.path.isfile(path):
            print("missing   %s" % path)
            continue
        normalize(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
