"""Check the two mechanical items of D-004 section 1 (read-only).

Item 1: the secondary task decomposition table of D-002 section 2.1 covers
every registered secondary task -- nothing missing, nothing invented.

Item 2: the module set of every task is stated once and does not
contradict the registry (D-001 section 4.2 is the single authority for the
numbering; D-014 section 0.2 is the single authority for the module
names).

The script prints the measured answer for each item so the checklist boxes
can be closed on evidence.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import read  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
P01 = REPO / "plan" / "tasks" / "P-01-池子重构"
TASKS = P01 / "tasks"
PLAN = P01 / "D-002-PLAN.md"
REGISTRY = REPO / "plan" / "D-001-TASK_REGISTRY.md"
ARCHIVE_INDEX = REPO / "plan" / "archive" / "README.md"

TICK = chr(96)
TASK_RE = re.compile(r"(T-[一-鿿]+(?:\.[0-9]{2})?)")
MODULE_RE = re.compile(r"(M-[0-9]{1,2})")


def decomposition_rows() -> dict:
    """The rows of the D-002 section 2.1 table."""
    text = read(PLAN)
    start = text.index("### 2.1")
    end = text.index("### 2.2", start)
    rows = {}
    for line in text[start:end].split(chr(10)):
        if not line.startswith("| " + TICK):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        name = cells[0].strip(TICK)
        if not name.startswith("T-"):
            continue
        modules = tuple(sorted(set(MODULE_RE.findall(cells[3]))))
        rows[name] = {"row": line, "modules": modules, "cells": cells}
    return rows


def registry_rows() -> dict:
    """The module set the registry declares for each task."""
    text = read(REGISTRY)
    rows = {}
    grouped = {}
    for line in text.split(chr(10)):
        if line.startswith("| " + TICK + "T-"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            name = cells[0].strip(TICK)
            if not name.startswith("T-"):
                continue
            modules = tuple(sorted(set(MODULE_RE.findall(cells[3]))))
            rows.setdefault(name, modules)
            continue
        # Section 4.2 groups the tasks by their number index: the index
        # column names the modules the group covers and the occupied
        # column lists the tasks of that group.
        if not line.startswith("| **"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        modules = tuple(sorted(set(MODULE_RE.findall(cells[1]))))
        for name in TASK_RE.findall(cells[3]):
            grouped.setdefault(name, modules)
    # The per-task rows are precise; the number-index groups only give the
    # module set of the whole group, so they fill in what is still missing.
    for name, modules in grouped.items():
        rows.setdefault(name, modules)
    return rows


def archived_tasks() -> set:
    text = read(ARCHIVE_INDEX)
    names = set()
    for line in text.split(chr(10)):
        if not line.startswith("| ["):
            continue
        for name in TASK_RE.findall(line):
            names.add(name)
    return names


def declared_modules() -> set:
    """The module numbers the registry declares in section 1.1.

    The module table of D-001 section 1.1 is the authority for which
    modules exist; the bound is derived from it so that a new module
    (M-13 was added in 2026-11) does not need a change here.
    """
    text = read(REGISTRY)
    start = text.index("### 1.1")
    end = text.index("### 1.2", start)
    modules = set()
    for line in text[start:end].split(chr(10)):
        if not line.startswith("| " + TICK + "M-"):
            continue
        name = line.strip().strip("|").split("|")[0].strip().strip(TICK)
        if MODULE_RE.fullmatch(name):
            modules.add(name)
    return modules


def main() -> None:
    rows = decomposition_rows()
    registry = registry_rows()
    on_disk = {d.name for d in TASKS.glob("T-*") if d.is_dir()}
    archived = archived_tasks()
    known_modules = declared_modules()

    print("== decomposition table ==")
    print("rows in D-002 section 2.1 : %d" % len(rows))
    print("tasks on disk             : %d" % len(on_disk))
    print("tasks in the archive index: %d" % len(archived))
    print("")

    missing_on_disk = sorted(on_disk - set(rows))
    print("item 1 -- on-disk tasks without a row: %s" % (missing_on_disk or "none"))

    known = set(rows) | on_disk | archived
    unknown_rows = sorted(set(rows) - known)
    print("item 1 -- rows without a task: %s" % (unknown_rows or "none"))

    print("")
    print("== module sets (item 2) ==")
    conflicts = []
    for name in sorted(rows):
        table_modules = rows[name]["modules"]
        registry_modules = registry.get(name)
        if registry_modules is None:
            print("%-16s table=%-28s registry=<not registered>" % (name, table_modules))
            continue
        flag = "OK " if table_modules == registry_modules else "DIFF"
        if table_modules != registry_modules:
            conflicts.append(name)
        print("%-16s table=%-28s registry=%-28s %s" % (name, table_modules, registry_modules, flag))

    print("")
    print("item 2 -- module set conflicts: %s" % (conflicts or "none"))
    unknown_modules = sorted(
        {
            module
            for row in rows.values()
            for module in row["modules"]
            if module not in known_modules
        }
    )
    print(
        "item 2 -- module numbers outside the registry table (M-1..M-%d): %s"
        % (max(int(m.split("-")[1]) for m in known_modules), unknown_modules or "none")
    )


if __name__ == "__main__":
    main()
