"""Small helper for editing the plan documents from a script.

The plan documents are Markdown with code spans, so the replacement text
contains backticks.  A script that is written through a shell one-liner
would have to escape every one of them, so the helpers here take the text
with "@" standing in for a backtick and restore it on the way in.

The line separator is written as chr(10) on purpose: the file that carries
this helper is generated from a shell tool that would otherwise turn the
two-character sequence into a real newline inside a string literal.

The files keep CRLF line endings; the normalizer
(normalize_crlf_20260724.py / normalize_crlf_20261125.py) is the single
place that decides the final encoding.
"""

from __future__ import annotations

from pathlib import Path

NL = chr(10)


def restore(text: str) -> str:
    """Turn "@" back into a backtick."""
    return text.replace("@", chr(96))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    """Replace one occurrence of old with new, or fail loudly."""
    old = restore(old)
    new = restore(new)
    if old not in text:
        raise SystemExit("mdpatch: anchor not found: " + label)
    if text.count(old) != 1:
        raise SystemExit(
            "mdpatch: anchor is not unique (%d): %s" % (text.count(old), label)
        )
    return text.replace(old, new, 1)


def replace_row(text: str, marker: str, new_row: str, label: str) -> str:
    """Replace the whole table row that starts with marker."""
    marker = restore(marker)
    lines = text.split(NL)
    hits = [i for i, line in enumerate(lines) if line.startswith(marker)]
    if len(hits) != 1:
        raise SystemExit(
            "mdpatch: row anchor count %d for %s" % (len(hits), label)
        )
    lines[hits[0]] = restore(new_row)
    return NL.join(lines)


def insert_before(text: str, marker: str, block: str, label: str) -> str:
    """Insert a block of lines before the line that starts with marker."""
    marker = restore(marker)
    lines = text.split(NL)
    hits = [i for i, line in enumerate(lines) if line.startswith(marker)]
    if len(hits) != 1:
        raise SystemExit(
            "mdpatch: insert anchor count %d for %s" % (len(hits), label)
        )
    block_lines = restore(block).split(NL)
    lines[hits[0] : hits[0]] = block_lines
    return NL.join(lines)


def insert_after(text: str, marker: str, block: str, label: str) -> str:
    """Insert a block of lines after the line that starts with marker."""
    marker = restore(marker)
    lines = text.split(NL)
    hits = [i for i, line in enumerate(lines) if line.startswith(marker)]
    if len(hits) != 1:
        raise SystemExit(
            "mdpatch: insert-after anchor count %d for %s" % (len(hits), label)
        )
    block_lines = restore(block).split(NL)
    lines[hits[0] + 1 : hits[0] + 1] = block_lines
    return NL.join(lines)
