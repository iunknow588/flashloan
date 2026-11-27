"""Read the RuntimeCore's own view of one chain.

The runtime's status endpoint answers slowly while the startup sweep runs,
so the tool waits for it and writes the answer to a file.  The answer is
the runtime's own state, its snapshot generation and the last error it
kept -- the three facts that say whether the runtime publishes a snapshot
or refuses it.

Usage:
    python fetch_runtime_core_20261126.py [port]
"""

from __future__ import annotations

import datetime
import json
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    port = int(sys.argv[1] if len(sys.argv) > 1 else 5000)
    log = (
        Path(__file__).resolve().parents[1]
        / "logs"
        / "t401_runtime_sup"
        / "runtime_core_state_20261126.log"
    )
    with log.open("w", encoding="utf-8") as out:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status?chain_id=4663", timeout=240
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001 - the tool reports, it does not raise
            out.write(f"ERROR {error}\n")
            return 1
        runtime = payload.get("chain_runtime") or {}
        core = runtime.get("runtimeCore") or {}
        out.write(f"at {datetime.datetime.now().isoformat(timespec='seconds')}\n")
        out.write(f"runtimeCore.state = {core.get('state')!r}\n")
        out.write(f"runtimeCore.generation = {core.get('generation')!r}\n")
        out.write(f"runtimeCore.lastError = {core.get('lastError')!r}\n")
        for event in (runtime.get("recentEvents") or [])[:14]:
            out.write(f"event {event.get('type')} {event.get('createdAt')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
