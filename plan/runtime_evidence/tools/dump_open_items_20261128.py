"""Dump every open checklist item with its file and line.

The recount tool counts the open items; this one prints what they actually
say, so the completion analysis can name each one instead of counting them.
"""

import json
from pathlib import Path

ROOT = Path("plan/tasks/P-01-池子重构")
OUT = Path("plan/runtime_evidence/diagnostics/open_items_20261128.json")


def checklist_files() -> list[Path]:
    """The checklist of every task directory, plus the two ledger files."""
    found = []
    for directory in sorted((ROOT / "tasks").iterdir()):
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*-checklist.md")):
            found.append(candidate)
            break
    found.append(ROOT / "D-004-checklist.md")
    found.append(ROOT / "docs/D-011-check_list_20261123.md")
    return found


def main() -> None:
    out = []
    for path in checklist_files():
        if not path.exists():
            out.append({"file": str(path), "missing": True})
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("- [ ]"):
                out.append({"file": str(path), "line": number, "text": stripped[6:].strip()})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"items": len(out), "written": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
