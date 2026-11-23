#!/usr/bin/env python3
"""Check that every ``清单 §x.y`` cross reference resolves to a real section.

Read-only.  The three active documents reference each other with textual
anchors such as "清单 §2.5b".  A markdown link checker cannot see those, so a
section can disappear while the references keep pointing at it.  This script
lists every referenced section number and whether the task list actually has
it.
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


def _find(name: str) -> Path:
    """按「名字」定位文档 —— 名字段可改，故用通配；编号段 D-nnn 稳定。"""
    matches = sorted(DOC_DIR.glob("D-*-" + name))
    if not matches:
        raise SystemExit("找不到文档：%s（目录 %s）" % (name, DOC_DIR))
    return matches[0]


DOC_DIR = _find_doc_dir()
TODO_LIST = _find("未完成任务清单_20261123.md")
CHECK_LIST = _find("check_list_20261123.md")
PLAN = _find("开发计划_20261123.md")

HEADING_RE = re.compile(r"^#{2,4}\s+(?:§\s*)?([0-9]+(?:\.[0-9a-z]+)*)\b", re.IGNORECASE)
REFERENCE_RE = re.compile(r"(清单|checklist|开发计划)\s*(?:§\s*)?([0-9]+(?:\.[0-9a-z]+)*)")


def headings(path: Path) -> set[str]:
    found: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = HEADING_RE.match(line)
        if match:
            found.add(match.group(1).rstrip("."))
    return found


def main() -> int:
    todo_sections = headings(TODO_LIST)

    def sort_key(section: str) -> tuple:
        parts = []
        for part in section.split("."):
            parts.append((0, int(part)) if part.isdigit() else (1, part))
        return tuple(parts)

    print(f"任务清单现有章节编号 {len(todo_sections)} 个：")
    print("  " + ", ".join(sorted(todo_sections, key=sort_key)))
    print()

    broken: list[str] = []
    for path in (CHECK_LIST, PLAN, TODO_LIST):
        text = path.read_text(encoding="utf-8-sig")
        for number, line in enumerate(text.splitlines(), start=1):
            for source, section in REFERENCE_RE.findall(line):
                if source != "清单":
                    continue
                if section in todo_sections:
                    continue
                broken.append(f"{path.name}:{number} -> 清单 §{section}")

    unique = sorted(set(broken), key=lambda item: item.split("->")[-1])
    print(f"指向任务清单的失效章节引用：{len(unique)} 条（去重后）")
    for item in unique:
        print(f"  {item}")
    return 1 if unique else 0


if __name__ == "__main__":
    sys.exit(main())
