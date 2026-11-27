"""Repair the two task rows the first run wrote badly.

The row text of the two new tasks was one Python string without a
separator, so the two rows ended up on **one** line, and the second run
inserted that line a second time (the presence check compared the
placeholder text instead of the restored one).

The repair keeps **one** copy and splits it back into the two rows.

The script is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, read, restore, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
D014 = ROOT / "plan" / "tasks" / "P-01-池子重构" / "docs" / "D-014-未完成任务清单_20261123.md"

ROW_28 = restore("| **28** | **@T-核.03@**")
ROW_29 = restore("| **29** | **@T-方.03@**")


def main() -> None:
    text = read(D014)
    lines = text.split(NL)
    out: list[str] = []
    seen = False
    for line in lines:
        if line.startswith(ROW_28):
            if seen:
                continue
            seen = True
            head, separator, tail = line.partition(ROW_29)
            out.append(head)
            if separator:
                out.append(separator + tail)
            continue
        out.append(line)
    repaired = NL.join(out)
    if repaired != text:
        write(D014, repaired)
        print("repair_d014_fang03_rows: ok")
    else:
        print("repair_d014_fang03_rows: already repaired")


if __name__ == "__main__":
    main()
