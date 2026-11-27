#!/usr/bin/env python3
'''Repair the depth verdict of the pools that the scope narrowing left out.

The 19 value-less reviews run through 15 pools.  Six of them are covered by
the production scopes again; the other nine are still out, and the reason is
**not** the library set: their depth verdict is @unavailable@, which the
scope admission rejects.  That verdict is a **missing measurement**, not a
thin pool -- the snapshot row exists and carries a real liquidity reading.

This script therefore asks the **pool tool** to repair the pool validity of
exactly those nine pools (@pool-manager depth --pool-id ... --apply@), which
is what the operator's rule says: a pool that is not valid is repaired with
the pool tool.  It never writes a verdict of its own and never changes the
library set.

Usage:
  repair_unavailable_pool_depth_20261128.py            # dry run
  repair_unavailable_pool_depth_20261128.py --apply
'''

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "flashloan/db/local_store_4663.sqlite3"
TOOLS = REPO / "flashloan/tools/target/release"
REPORT = REPO / "plan/runtime_evidence/reports/pool_depth_repair_20261128.json"

sys.path.insert(0, str(REPO / "flashloan/src"))
from core.env_loader import load_env_files  # noqa: E402


def uncovered_pools() -> list[tuple[str, str]]:
    """The pools of the value-less reviews that no scope covers."""
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=60000")
    cursor = connection.cursor()
    digests: set[str] = set()
    for (pool_ids_json,) in cursor.execute(
        "select pool_ids_json from manager_v4_reviews where signed_pnl_amount is null"
    ):
        digests.update(json.loads(pool_ids_json or "[]"))
    rows = []
    for digest in sorted({item.split(":")[5] for item in digests}):
        covered = (
            cursor.execute(
                "select count(*) from v4_exchange_pools_core4 where pool_id like ?",
                (f"%{digest}%",)).fetchone()[0]
            + cursor.execute(
                "select count(*) from v4_exchange_pools_candidate_exchange"
                " where pool_id like ?", (f"%{digest}%",)).fetchone()[0]
        )
        if covered:
            continue
        snapshot = cursor.execute(
            "select pool_id, depth_status from v4_exchange_pool_liquidity_snapshots"
            " where pool_id like ? limit 1", (f"%{digest}%",)).fetchone()
        if snapshot:
            rows.append((str(snapshot[0]), str(snapshot[1])))
    connection.close()
    return rows


def main() -> int:
    apply = "--apply" in sys.argv
    load_env_files(REPO / "flashloan/app_runtime")
    rows = uncovered_pools()
    print(json.dumps({"pools": len(rows),
                      "verdicts": sorted({status for _pool, status in rows})},
                     ensure_ascii=False))
    if not rows:
        return 0
    command = [str(TOOLS / "pool-manager.exe"), "depth", "--store", str(DB),
               "--chain-id", "4663"]
    for pool_id, _status in rows:
        command += ["--pool-id", pool_id]
    if apply:
        command.append("--apply")
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    output = {
        "apply": apply,
        "poolCount": len(rows),
        "returncode": result.returncode,
        "stdout": result.stdout[-20000:],
        "stderr": result.stderr[-4000:],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(output, ensure_ascii=False, indent=2),
                      encoding="utf-8", newline="\n")
    print("returncode=" + str(result.returncode))
    print(result.stdout[-4000:])
    if result.stderr:
        print("stderr: " + result.stderr[-2000:])
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
