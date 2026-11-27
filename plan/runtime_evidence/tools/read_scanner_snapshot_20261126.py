"""Read the scanner snapshot and keep the broadcast gate.

The endpoint is polled by the runtime itself, so it answers quickly even
while a review round is running.
"""

from __future__ import annotations

import json
import sys
import urllib.request


def main() -> int:
    url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    found: dict[str, object] = {}

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else key
                if key in ("broadcastGate", "broadcastEnabled", "broadcastAllowed", "autoExecuteEnabled", "privateKeyConfigured"):
                    found[here] = value
                walk(value, here)
        elif isinstance(node, list):
            for index, value in enumerate(node[:5]):
                walk(value, f"{path}[{index}]")

    walk(payload, "")
    print(json.dumps({"found": found, "topLevelKeys": sorted(payload)[:40]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
