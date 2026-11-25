"""Recount the remaining work items of P-01 (read only).

The counting rules are the ones D-014 section 1.3 states:

  * a secondary task: the `- [ ]` boxes of its own checklist file;
  * the first level items: the `- [ ]` boxes of D-004 sections 2 and 5;
  * the cross cutting items: the `- [ ]` boxes of D-011 sections 3, 4 and 5.

The script prints the measured numbers so that every number of the ledger can
be recomputed instead of being carried over.
"""

from __future__ import annotations

import re
sys_path = __import__("sys").path
sys_path.insert(0, __import__("pathlib").Path(__file__).resolve().parent)

from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
P01 = REPO / "plan" / "tasks" / "P-01-池子重构"
TASKS = P01 / "tasks"
D004 = P01 / "D-004-checklist.md"
D011 = P01 / "docs" / "D-011-check_list_20261123.md"

UNCHECKED = re.compile(r"^- \[ \]")
CHECKED = re.compile(r"^- \[[xX]\]")


def read_lines(path: Path) -> list[str]:
    """The lines of a file, whatever its line endings are."""
    return path.read_text(encoding="utf-8").split(chr(10))


def count_boxes(lines: list[str]) -> tuple[int, int]:
    unchecked = sum(1 for line in lines if UNCHECKED.match(line))
    checked = sum(1 for line in lines if CHECKED.match(line))
    return unchecked, checked


def section(lines: list[str], start: str, end: str | None) -> list[str]:
    """The lines of one section of a markdown file."""
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith(start)
            continue
        if inside:
            collected.append(line)
    if end is not None and not inside:
        raise SystemExit(f"the section {start!r} was not found")
    return collected


def task_checklist(directory: Path) -> Path | None:
    """The checklist of a task directory."""
    for candidate in sorted(directory.glob("*-checklist.md")):
        return candidate
    return None


def main() -> int:
    task_rows = []
    for directory in sorted(TASKS.iterdir()):
        if not directory.is_dir():
            continue
        checklist = task_checklist(directory)
        if checklist is None:
            continue
        unchecked, checked = count_boxes(read_lines(checklist))
        task_rows.append((directory.name, checklist.name, unchecked, checked))

    print("== the secondary tasks (on the board) ==")
    task_total = 0
    for name, file_name, unchecked, checked in task_rows:
        task_total += unchecked
        print(f"  {name:<14} unchecked={unchecked:>3} checked={checked:>3}  ({file_name})")

    d004 = read_lines(D004)
    first_level = 0
    print("== the first level items (D-004) ==")
    for title in ("## 2.", "## 5."):
        lines = section(d004, title, None)
        unchecked, checked = count_boxes(lines)
        first_level += unchecked
        print(f"  {title:<8} unchecked={unchecked:>3} checked={checked:>3}")

    d011 = read_lines(D011)
    cross = 0
    print("== the cross cutting items (D-011) ==")
    for title in ("## 3.", "## 4.", "## 5."):
        lines = section(d011, title, None)
        unchecked, checked = count_boxes(lines)
        cross += unchecked
        print(f"  {title:<8} unchecked={unchecked:>3} checked={checked:>3}")

    print("")
    print(f"  secondary tasks = {task_total}")
    print(f"  first level     = {first_level}")
    print(f"  cross cutting   = {cross}")
    print(f"  TOTAL           = {task_total + first_level + cross}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
