#!/usr/bin/env python3
"""Survey the open work across the active pool-refactor documents (v3.0 / v4.0 layout).

Read-only.  Acceptance boxes now live in **one directory per task** under
``plan/tasks/P-<nn>-<title>/tasks/T-<nn>/checklist.md``; the top-level checklist is
an index plus the binding rules and the cross-cutting sections.

The script therefore surveys

* every task directory (task-owned boxes), and
* the top-level checklist (cross-cutting boxes),

so the consolidated ledger is built from measured evidence rather than from
recollection.  It also prints every task / acceptance id found in any of the
active documents, which is what makes the ledger's coverage auditable.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
def _find_doc_dir() -> Path:
    """专题文档区（方案素材）：优先一级任务的 docs/，回退旧的 plan/池子重构方案/。"""
    for h in sorted((REPO_ROOT / "plan" / "tasks").glob("P-*/docs")):
        if (h / "未完成任务清单_20261123.md").exists():
            return h
    legacy = REPO_ROOT / "plan" / "池子重构方案"
    return legacy if legacy.is_dir() else REPO_ROOT / "plan"


DOC_DIR = _find_doc_dir()
CHECK_LIST = DOC_DIR / "check_list_20261123.md"
TODO_LIST = DOC_DIR / "未完成任务清单_20261123.md"
PLAN = DOC_DIR / "开发计划_20261123.md"
TASKS_DIR = REPO_ROOT / "plan" / "tasks"
# 二级任务在 一级任务/tasks/ 下；兼容旧的顶层平铺
PRIMARY_DIRS = sorted(p for p in TASKS_DIR.glob("P-*") if p.is_dir()) if TASKS_DIR.is_dir() else []
SECONDARY_DIRS = [d for pr in PRIMARY_DIRS for d in sorted((pr / "tasks").glob("T-*")) if d.is_dir()]
LEGACY_DIRS = [d for d in sorted(TASKS_DIR.glob("T-*")) if d.is_dir()] if TASKS_DIR.is_dir() else []

SECTION_RE = re.compile(r"^#{2,5}\s+(.*)$")
BOX_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(.*)$")
TASK_RE = re.compile(r"TASK-(\d+)")
AC_RE = re.compile(r"AC-(\d+)")
NEW_TASK_RE = re.compile(r"\bT-(\d\d)\b")
NEW_AC_RE = re.compile(r"\bAC-(\d\d)\b")


def survey_sections(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    # A document may open with boxes before its first heading; count those too.
    current: dict[str, object] = {
        "line": 1,
        "title": path.stem,
        "tasks": [],
        "acs": [],
        "open": 0,
        "done": 0,
    }
    rows.append(current)
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        heading = SECTION_RE.match(line)
        if heading:
            title = heading.group(1).strip()
            tasks = sorted({f"TASK-{m}" for m in TASK_RE.findall(title)})
            acs = sorted({f"AC-{m}" for m in AC_RE.findall(title)})
            current = {
                "line": number,
                "title": title,
                "tasks": tasks,
                "acs": acs,
                "open": 0,
                "done": 0,
            }
            rows.append(current)
            continue
        box = BOX_RE.match(line)
        if box:
            if box.group(1) == " ":
                current["open"] = int(current["open"]) + 1
            else:
                current["done"] = int(current["done"]) + 1
    return rows


def main() -> int:
    task_rows: list[dict[str, object]] = []
    total_task_open = 0
    total_task_done = 0
    for task_dir in SECONDARY_DIRS + LEGACY_DIRS + PRIMARY_DIRS:
        if True:
            checklist = task_dir / "checklist.md"
            if not checklist.is_file():
                continue
            rows = survey_sections(checklist)
            open_items = sum(int(r["open"]) for r in rows)
            done_items = sum(int(r["done"]) for r in rows)
            total_task_open += open_items
            total_task_done += done_items
            task_rows.append(
                {
                    "task": task_dir.name,
                    "open": open_items,
                    "done": done_items,
                }
            )

    print("=== task directories (task-owned acceptance boxes) ===")
    for row in task_rows:
        print(f"{row['task']:<8} open={row['open']:<4} done={row['done']:<4}")
    print(f"\ntotal task-owned unchecked boxes: {total_task_open}")
    print(f"total task-owned checked boxes:   {total_task_done}")

    print("\n=== top-level checklist sections with unchecked items ===")
    rows = survey_sections(CHECK_LIST)
    total_cross_open = 0
    for row in rows:
        open_items = int(row["open"])
        total_cross_open += open_items
        ids = ", ".join(row["tasks"] + row["acs"])  # type: ignore[arg-type]
        if open_items or row["tasks"] or row["acs"]:
            print(
                f"L{row['line']:>5}  open={open_items:<4} done={row['done']:<4} "
                f"{ids:<28} {row['title'][:78]}"
            )
    print(f"\ntotal cross-cutting unchecked boxes: {total_cross_open}")
    print(f"GRAND TOTAL unchecked boxes:         {total_task_open + total_cross_open}")

    print("\n=== current id usage (new scheme) ===")
    for label, pattern in (("T-", NEW_TASK_RE), ("AC-", NEW_AC_RE)):
        seen: dict[str, set[str]] = {}
        sources = [CHECK_LIST, TODO_LIST, PLAN]
        sources.extend(d / "checklist.md" for d in SECONDARY_DIRS + LEGACY_DIRS)
        sources.extend(d / "README.md" for d in SECONDARY_DIRS + LEGACY_DIRS)
        sources.extend(d / "README.md" for d in PRIMARY_DIRS)
        for src in sources:
            for match in pattern.finditer(src.read_text(encoding="utf-8-sig")):
                seen.setdefault(f"{label}{match.group(1)}", set()).add(src.name)
        for key in sorted(seen, key=lambda item: int(item.split("-")[1])):
            print(f"{key:<8} in {', '.join(sorted(seen[key]))}")

    print("\n=== legacy ids (should only appear in archived-history notes) ===")
    for path in (CHECK_LIST, TODO_LIST, PLAN):
        text = path.read_text(encoding="utf-8-sig")
        ids = sorted({f"TASK-{m}" for m in TASK_RE.findall(text)})
        acs = sorted({f"AC-{m}" for m in AC_RE.findall(text)})
        if ids or acs:
            print(f"{path.name}: {' '.join(ids)} {' '.join(acs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
