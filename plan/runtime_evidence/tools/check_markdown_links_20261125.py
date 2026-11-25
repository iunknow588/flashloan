#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only check: do the relative links of the plan tree still resolve?

Every ``](target)`` whose target is relative is resolved against the file that
contains it.  Absolute URLs and pure in-page anchors are ignored; for an
``anchor#fragment`` target only the file part is resolved.  The archive
directory is skipped because it snapshots paths that used to exist.

Exit code 1 when at least one link is broken.
"""

from __future__ import annotations

import os
import re
import sys


LINK_RE = re.compile(r"\]\(([^)\s]+)")
SKIP_DIRS = {"archive", ".git", "__pycache__", "target", "node_modules"}


def iter_markdown(root: str):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.lower().endswith(".md"):
                yield os.path.join(current, name)


def main() -> int:
    default_root = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", ".."
    )
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else default_root)
    broken = []
    checked = 0
    for path in iter_markdown(root):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        for raw in LINK_RE.findall(text):
            target = raw.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            if not target or target.startswith(("http://", "https://", "mailto:", "#", "/")):
                continue
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue
            checked += 1
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), file_part))
            if not os.path.exists(resolved):
                broken.append((path, target, resolved))
    for path, target, resolved in broken:
        print("BROKEN {0} -> {1} (resolved {2})".format(
            os.path.relpath(path, root), target, resolved))
    print("---LINK-CHECK-DONE checked={0} broken={1}---".format(checked, len(broken)))
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
