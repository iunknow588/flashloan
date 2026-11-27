#!/usr/bin/env python3
"""Verify the pool-refactor document set after the TASK-166 gate ruling.

Read-only.  Checks, for every markdown file under ``plan/tasks/P-01-池子重构/docs``:

* the file decodes as UTF-8 with no U+FFFD replacement characters;
* the BOM / line-ending convention of the file is unchanged;
* every relative markdown link resolves to an existing file;
* no multi-line block is duplicated verbatim inside a single file;
* no *live* claim says TASK-166 is still waiting for a human signature.

The script writes nothing and touches no database.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
def _find_doc_dir() -> Path:
    """专题文档区（方案素材）：优先一级任务的 docs/，回退旧的 plan/池子重构方案/。"""
    for h in sorted((REPO_ROOT / "plan" / "tasks").glob("P-*/docs")):
        if list(h.glob("D-*-未完成任务清单_20261123.md")):
            return h
    legacy = REPO_ROOT / "plan" / "池子重构方案"
    return legacy if legacy.is_dir() else REPO_ROOT / "plan"


DOC_DIR = _find_doc_dir()

# (relative path, expect_bom, expect_lf) -- the design says every text file
# is written as **UTF-8 without BOM** and **LF** line endings, so the
# baseline records that form.  It is re-checked after the edits so a round
# cannot silently change an encoding.
ENCODING_BASELINE: dict[str, tuple[bool, bool]] = {
    "D-005-现状与可行性分析.md": (False, True),
    "D-006-目标架构与表结构.md": (False, True),
    "D-007-迁移实施与验收.md": (False, True),
    "D-008-内存对象与闭环引用链.md": (False, True),
    "D-009-记录存储剥离技术方案.md": (False, True),
    "D-010-记录存储剥离开发计划.md": (False, True),
    "D-011-check_list_20261123.md": (False, True),
    "D-013-开发计划_20261123.md": (False, True),
    "D-014-未完成任务清单_20261123.md": (False, True),
    "D-015-编号对照表_20261124.md": (False, True),
    "README.md": (False, True),
}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Phrases that would mean a reader still sees TASK-166 as blocked on a human
# signature.  Every legitimate mention has to carry a revocation or a
# "历史行" marker, so the check is on the whole line.
FORBIDDEN_LINE_MARKERS = (
    "待人工签字",
    "仍卡在",
    "不得开工",
    "待签字",
)
REVOCATION_MARKERS = (
    "历史行",
    "已作废",
    "作废",
    "取代",
    "撤销",
    "已闭合",
    "解除",
    "不再",
    "v1.3",
    "v2.14",
    "v2.15",
    "v3.3",
    "第 73 轮",
    "第 72 轮",
)


def decode(path: Path) -> tuple[str, bool, bool, int]:
    raw = path.read_bytes()
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    text = raw.decode("utf-8-sig")
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    return text, has_bom, crlf == 0 and (lf_only > 0 or not text), text.count("\ufffd")


def check_links(path: Path, text: str) -> list[str]:
    problems: list[str] = []
    for target in LINK_RE.findall(text):
        target = target.strip()
        if not target or target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            problems.append(f"broken link -> {target}")
    return problems


def check_duplicates(path: Path, text: str) -> list[str]:
    """Flag verbatim multi-line blocks that appear more than once."""
    lines = [line.strip() for line in text.splitlines()]
    window = 8
    seen: dict[tuple[str, ...], int] = {}
    problems: list[str] = []
    for index in range(len(lines) - window + 1):
        block = lines[index : index + window]
        if sum(1 for line in block if line) < window - 1:
            continue
        key = tuple(block)
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1:
            problems.append(f"duplicated {window}-line block x{count}: {key[0][:60]!r}")
    return problems


def check_gate_language(path: Path, text: str) -> list[str]:
    """No *live* line may claim TASK-166 is still waiting on a signature.

    A line is live unless it, or one of the two lines that follow it, carries
    a revocation marker.  The trailing window matters because the corrected
    banners quote the old wording in order to void it -- the voiding sentence
    usually sits on the next line.
    """
    problems: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        number = index + 1
        if "TASK-166" not in line:
            continue
        if not any(marker in line for marker in FORBIDDEN_LINE_MARKERS):
            continue
        window = "\n".join(lines[index : index + 3])
        if any(marker in window for marker in REVOCATION_MARKERS):
            continue
        problems.append(f"live blocked-on-signature claim at line {number}: {line.strip()[:90]}")
    return problems


def main() -> int:
    failures: list[str] = []
    files = sorted(p for p in DOC_DIR.rglob("*.md") if p.is_file())
    checked_links = 0
    for path in files:
        rel = path.relative_to(DOC_DIR).as_posix()
        text, has_bom, all_lf, bad_chars = decode(path)
        if bad_chars:
            failures.append(f"{rel}: {bad_chars} U+FFFD replacement characters")
        expected = ENCODING_BASELINE.get(rel)
        if expected is not None:
            want_bom, want_lf = expected
            if has_bom != want_bom:
                failures.append(f"{rel}: BOM={has_bom}, expected {want_bom}")
            if all_lf != want_lf:
                failures.append(f"{rel}: all_lf={all_lf}, expected {want_lf}")
        for problem in check_links(path, text):
            failures.append(f"{rel}: {problem}")
        for problem in check_duplicates(path, text):
            failures.append(f"{rel}: {problem}")
        for problem in check_gate_language(path, text):
            failures.append(f"{rel}: {problem}")
        checked_links += len(LINK_RE.findall(text))

    print(f"files checked      : {len(files)}")
    print(f"markdown links seen: {checked_links}")
    print(f"failures           : {len(failures)}")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
