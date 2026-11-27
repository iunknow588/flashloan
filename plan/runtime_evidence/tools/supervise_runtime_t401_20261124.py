#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep the opportunity-console runtime alive while the evidence is collected.

The runtime is the only process that measures the closed-loop groups, so the
review generation can only land while it runs.  It has been observed to stop
without a traceback and without a crash record, so the supervisor restarts it
and records **why** it stopped (the exit code) next to every start.

The supervisor's own command line deliberately mentions neither the runtime
script nor the repository root: the runtime stops "stale" instances of
itself at start-up, and the supervisor must not be mistaken for one.

Usage:
    python supervise_runtime_t401_20261124.py [port]
"""

import datetime
import os
import subprocess
import sys
import time

REPO = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
SCRIPT = os.path.join(REPO, "flashloan", "app_runtime", "run.py")
LOG_DIR = os.path.join(
    REPO, "plan", "runtime_evidence", "logs", "t401_runtime_sup"
)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "5000"
    os.makedirs(LOG_DIR, exist_ok=True)
    environment = os.environ.copy()
    # The ramp: how many groups may become resident per scan, and the bound
    # on the resident groups.  Both are read once by the runtime at start-up.
    #
    # The per-scan batch is **0**: the ramp is the wait, and a group that
    # is not admitted in the first scan is not measured in the first scan.
    # Zero means *no wait* - every pending group becomes resident at once
    # and the whole catalog is reviewed in the first sweep, so no closed
    # loop keeps a stale verdict (and a stale profit is no profit).  The
    # review concurrency inside the pool still bounds how many groups
    # measure at the same time, so the sweep costs no more concurrency
    # than the ramp ever did.
    environment["DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN"] = "0"
    environment["DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS"] = "256"
    # The resident worker cap is the **review concurrency**: the default is
    # 16, and a chain publishes more closed loops than that, so the surplus
    # loops never get a worker and their profit data never refreshes.  The
    # cap is a resource ceiling (256), not a count of loops.
    #
    # The value is **32**, not 48: the Python side clamps the override to
    # `CLOSED_LOOP_WORKER_CEILING = 32` (`closed_loop_state_machine.py`),
    # so 48 would be truncated to 32 anyway.  The **measured** effect of the
    # override is therefore 16 -> 32 review slots, and the runtime reports
    # `concurrencyLimit = 32`.  Raising it further is **not** the lever
    # that speeds a round up: the chain reads are rate-limited per address
    # (`throttle_rpc_call`, <= 5 calls/s), and 16 -> 48 did not move the
    # throughput at all (still ~34 reviews/min) -- see `D-202` section 2.
    environment["DEX_CLOSED_LOOP_MAX_WORKERS"] = "32"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    stdout = open(os.path.join(LOG_DIR, "runtime_stdout.log"), "a", encoding="utf-8", errors="replace")
    stderr = open(os.path.join(LOG_DIR, "runtime_stderr.log"), "a", encoding="utf-8", errors="replace")
    print(f"{now()} supervisor started (port {port})", flush=True)
    while True:
        process = subprocess.Popen(
            [sys.executable, SCRIPT, "--port", port],
            cwd=REPO,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        print(f"{now()} runtime started pid={process.pid}", flush=True)
        code = process.wait()
        print(
            f"{now()} runtime stopped pid={process.pid} exit_code={code}",
            flush=True,
        )
        time.sleep(15)


if __name__ == "__main__":
    main()
