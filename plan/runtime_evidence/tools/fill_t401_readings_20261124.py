#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill the readings section of D-104 from a collector run.

The section is **replaced**, never appended to, so a re-run of the
collector is the only way the document changes.  A reading that was not
measured is written as "未测量" -- never as a pass.

Usage:
    python fill_t401_readings_20261124.py <current.json> [<baseline.json>]
"""

import io
import os
import subprocess
import sys

ROOT = r"E:\2026OPC大赛\flashLoan"
DOC = os.path.join(ROOT, r"plan\tasks\P-01-池子重构\tasks\T-复编.02\docs\D-104-复核完整性实测.md")
RENDER = os.path.join(ROOT, r"plan\runtime_evidence\tools\render_t401_readings_20261124.py")

START = "## 3. 读数"
END = "## 4. 边界与诚实登记"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    current = sys.argv[1]
    baseline = sys.argv[2] if len(sys.argv) > 2 else None
    cmd = [sys.executable, RENDER, current]
    if baseline:
        cmd.append(baseline)
    rendered = subprocess.run(cmd, capture_output=True, check=True).stdout.decode("utf-8")

    with io.open(DOC, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    normalized = text.replace("\r\n", "\n")
    i = normalized.find(START)
    j = normalized.find(END)
    if i < 0 or j < 0 or j <= i:
        raise SystemExit("[D-104] the anchors of the readings section were not found")

    block = [START, "", rendered.strip(), "", "---", "", ""]
    normalized = normalized[:i] + "\n".join(block) + normalized[j:]
    normalized = normalized.replace("\n", "\r\n")
    with io.open(DOC, "w", encoding="utf-8", newline="") as handle:
        handle.write(normalized)
    print("[D-104] readings filled from %s" % current)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
