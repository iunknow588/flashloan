#!/usr/bin/env python3
"""Check the structural acceptance items of the primary task P-01 (read-only).

Three of the primary task's own acceptance items are mechanical:

* every secondary task has its **own directory** with the **four fixed files**;
* every secondary task has its **own** regression commands in its plan;
* a secondary task's checklist **does not reference** another task's
  acceptance items.

The script prints the measured answer for each item so the checklist boxes can
be closed on evidence instead of on recollection.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TASKS = REPO / "plan" / "tasks" / "P-01-池子重构" / "tasks"

TASK_REF = re.compile(r"(T-[一-鿿]+(?:\.[0-9]{2})?)")
COMMAND = re.compile(r"^\s*(cargo |python |pytest|cd )", re.MULTILINE)


def main() -> None:
    directories = sorted(d for d in TASKS.glob("T-*") if d.is_dir())
    print("== in-disk secondary tasks: %d ==" % len(directories))
    print("")

    missing_four: list[str] = []
    missing_commands: list[str] = []
    cross_references: list[str] = []
    precondition_references: list[str] = []

    for directory in directories:
        files = {p.name for p in directory.glob("*.md")}
        plan = next((f for f in files if f.endswith("-PLAN.md")), None)
        scope = next((f for f in files if f.endswith("-SCOPE_GUARD.md")), None)
        checklist = next((f for f in files if "checklist" in f), None)
        readme = "README.md" if "README.md" in files else None
        absent = [
            name
            for name, value in (
                ("PLAN", plan),
                ("SCOPE_GUARD", scope),
                ("checklist", checklist),
                ("README", readme),
            )
            if value is None
        ]
        if absent:
            missing_four.append("%s: missing %s" % (directory.name, absent))

        if plan is None:
            missing_commands.append("%s: no PLAN" % directory.name)
        else:
            text = (directory / plan).read_text(encoding="utf-8")
            if not COMMAND.search(text):
                missing_commands.append(
                    "%s: no regression command in %s" % (directory.name, plan)
                )

        if checklist is None:
            continue
        own = directory.name
        for number, line in enumerate(
            (directory / checklist).read_text(encoding="utf-8").splitlines(), 1
        ):
            for reference in sorted(set(TASK_REF.findall(line))):
                if reference == own:
                    continue
                # A reference that points at another task's **acceptance
                # content** is the violation the item is about.  A reference
                # that states a **precondition** ("T-x is closed") is allowed
                # by the task rules, even though the rules also ask for it to
                # be written as a module-level precondition.
                points_at_acceptance = any(
                    word in line for word in ("验收框", "验收项", "checklist", "D-0")
                )
                if points_at_acceptance:
                    cross_references.append(
                        "%s: checklist line %d points at %s's acceptance content"
                        % (own, number, reference)
                    )
                else:
                    precondition_references.append(
                        "%s: checklist line %d mentions %s as a precondition"
                        % (own, number, reference)
                    )

    print("== item 1: every task has its own directory + four fixed files ==")
    print("   PASS" if not missing_four else "   FAIL")
    for line in missing_four:
        print("     - %s" % line)
    print("")

    print("== item 2: every task has its own regression commands ==")
    print("   PASS" if not missing_commands else "   FAIL")
    for line in missing_commands:
        print("     - %s" % line)
    print("")

    print("== item 3: a checklist does not reference another task's acceptance items ==")
    print("   PASS" if not cross_references else "   REVIEW")
    for line in cross_references:
        print("     - %s" % line)
    print("")
    print("== notes: precondition references (allowed; should be module-level) ==")
    for line in precondition_references:
        print("     - %s" % line)


if __name__ == "__main__":
    main()
