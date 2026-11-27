"""Fetch the runtime overview and write its summary next to the evidence.

The overview is a **read-only** GET of the local runtime.  The response is
written to `plan/runtime_evidence/logs/t401_runtime_sup/overview_d198.out.json`
so that the reading can be compared with the pre-restart one without
holding the terminal open for the whole request.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = (
    REPO_ROOT
    / "plan"
    / "runtime_evidence"
    / "logs"
    / "t401_runtime_sup"
    / "overview_d198.out.json"
)


def main() -> int:
    with urllib.request.urlopen("http://127.0.0.1:5000/api/dex/overview", timeout=300) as response:
        payload = json.load(response)
    summary = payload.get("summary", payload)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
