"""Read the overview endpoint and keep only the summary block.

The endpoint aggregates the whole runtime and can take longer than a
minute while a review round is running, so the caller runs this in the
background and polls the output file.
"""

from __future__ import annotations

import json
import sys
import urllib.request


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000/api/dex/overview"
    with urllib.request.urlopen(url, timeout=300) as response:
        payload = json.loads(response.read().decode("utf-8"))
    summary = payload.get("summary", payload)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
