#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only probe: which runtime instances wrote to the chain database?

The runtime stamps every committed snapshot with the ``instance_id`` of the
process that produced it (``runtime-core-<pid>-<chain>-<n>``).  A supervised
restart therefore leaves a *reset* generation counter behind, and any third
party that managed to bind the port before the supervisor came back leaves a
generation run of its own.  This probe groups the persisted runtime events by
``instance_id`` and reports the generation range and wall-clock span of each,
so a restart can be attributed to a concrete pid instead of guessed.

It opens the chain database read-only and writes nothing.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from collections import OrderedDict


DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "..",
    "flashloan",
    "db",
    "local_store_4663.sqlite3",
)

INSTANCE_RE = re.compile(r"runtime-core-(\d+)-\d+-\d+")


def _candidate_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows if "event" in row[0].lower() or "runtime" in row[0].lower()]


def main() -> int:
    db_path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB)
    if not os.path.exists(db_path):
        print(f"database not found: {db_path}")
        return 1
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = _candidate_tables(connection)
        print(f"database: {db_path}")
        print(f"candidate tables: {tables}")
        for table in tables:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            print(f"\n== {table} ==")
            print(f"columns: {columns}")
            if not any("generation" in column for column in columns):
                continue
            generation_column = next(column for column in columns if "generation" in column)
            instance_column = next(
                (column for column in columns if "instance" in column),
                None,
            )
            time_column = next(
                (column for column in columns if "created" in column or "at" in column),
                None,
            )
            selected = [generation_column]
            instance_index = None
            time_index = None
            if instance_column:
                instance_index = len(selected)
                selected.append(instance_column)
            if time_column:
                time_index = len(selected)
                selected.append(time_column)
            rows = connection.execute(
                f"SELECT {', '.join(selected)} FROM {table}"
            ).fetchall()
            groups: OrderedDict[str, dict] = OrderedDict()
            for row in rows:
                generation = row[0]
                instance = str(row[instance_index]) if instance_index is not None else "(no instance column)"
                stamp = str(row[time_index]) if time_index is not None else ""
                bucket = groups.setdefault(
                    instance,
                    {"count": 0, "min": generation, "max": generation, "first": stamp, "last": stamp},
                )
                bucket["count"] += 1
                bucket["min"] = min(bucket["min"], generation)
                bucket["max"] = max(bucket["max"], generation)
                if stamp:
                    bucket["first"] = min(bucket["first"], stamp) if bucket["first"] else stamp
                    bucket["last"] = max(bucket["last"], stamp) if bucket["last"] else stamp
            for instance, bucket in groups.items():
                pid = INSTANCE_RE.search(instance)
                pid_text = pid.group(1) if pid else "?"
                print(
                    "instance={instance} pid={pid} rows={count} generation={low}..{high} "
                    "first={first} last={last}".format(
                        instance=instance,
                        pid=pid_text,
                        count=bucket["count"],
                        low=bucket["min"],
                        high=bucket["max"],
                        first=bucket["first"],
                        last=bucket["last"],
                    )
                )
        payload_groups = _payload_instance_groups(connection)
        if payload_groups:
            print("\n== instance ids seen inside manager_events payloads ==")
            for instance, bucket in payload_groups.items():
                pid = INSTANCE_RE.search(instance)
                print(
                    "instance={instance} pid={pid} rows={count} first={first} last={last}".format(
                        instance=instance,
                        pid=pid.group(1) if pid else "?",
                        count=bucket["count"],
                        first=bucket["first"],
                        last=bucket["last"],
                    )
                )
    finally:
        connection.close()
    return 0


def _payload_instance_groups(connection: sqlite3.Connection) -> OrderedDict[str, dict]:
    """Group ``manager_events`` rows by the runtime instance inside the payload."""
    try:
        rows = connection.execute(
            "SELECT payload_json, created_at FROM manager_events ORDER BY created_at"
        ).fetchall()
    except sqlite3.Error:
        return OrderedDict()
    groups: OrderedDict[str, dict] = OrderedDict()
    for payload_json, created_at in rows:
        if not payload_json:
            continue
        found = INSTANCE_RE.findall(str(payload_json))
        for pid in found:
            instance = "runtime-core-{pid}".format(pid=pid)
            bucket = groups.setdefault(
                instance,
                {"count": 0, "first": str(created_at), "last": str(created_at)},
            )
            bucket["count"] += 1
            bucket["first"] = min(bucket["first"], str(created_at))
            bucket["last"] = max(bucket["last"], str(created_at))
    return groups


if __name__ == "__main__":
    raise SystemExit(main())
