# -*- coding: utf-8 -*-
"""TASK-183 / TASK-184 取证：测试套件对生产主网库的写入。

背景（第 68 轮确证）：`pytest` 每次全量运行都会向 **活的主网链库**
`flashloan/db/local_store_4663.sqlite3` 提交事务。`TASK-183` 只收口了
`core/local_kv.py::_ensure_schema()` 里那一处 `kv_meta.schema_version`
的无条件写入；其余写入路径由 `TASK-184` 承接（开放中）。

本工具提供两种口径：

1. `analyze`（默认）：**离线**解析探针产物，不碰生产库。
   输入是语句级探针日志 + `PRAGMA data_version` 时间线，
   输出 DML 分类计数、写入来源代码位点、提交次数与时间窗。

2. `measure`：**在线**复测（会写生产库 —— 仅在需要重取证据时使用）。
   在子进程里跑 pytest，同时挂语句探针与 `data_version` 采样线程。

两个口径都保证：**主库全程只读打开**（`mode=ro` + `query_only=ON`），
采集器自身不产生任何写入。

用法：
    python analyze_task183_184_production_db_writes_20261123.py \
        --probe-log <probe.log> --dataversion <dataversion.tsv> \
        --label post_fix --out-json <a.json> --out-md <a.md>
    python analyze_task183_184_production_db_writes_20261123.py \
        --measure --label post_fix --out-json <a.json> --out-md <a.md>
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_DB = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"

_DML = re.compile(
    r"^\s*(?P<verb>INSERT OR REPLACE INTO|INSERT INTO|UPDATE|DELETE FROM)\s+(?P<table>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_KV_KEY = re.compile(r"VALUES\s*\(\s*'(?P<value>[^']*)'")


def _read_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8", errors="replace")


def _open_readonly(database: Path) -> sqlite3.Connection:
    """以只读方式打开主库 —— 采集器自身永远不写生产数据。"""
    uri = "file:" + str(database).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def analyze_probe_log(probe_log: Path) -> dict:
    """解析语句级探针日志，得出 DML 分类与来源代码位点。"""
    by_statement: collections.Counter = collections.Counter()
    by_table: collections.Counter = collections.Counter()
    by_key: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_site: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    connections = 0
    statements = 0
    current_stack = ""

    for line in _read_text(probe_log).splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        kind = parts[1]
        if kind == "CONNECT":
            connections += 1
            current_stack = parts[2]
            continue
        if kind != "SQL":
            continue
        statements += 1
        statement = parts[2]
        match = _DML.match(statement)
        if not match:
            continue
        verb = match.group("verb").upper().replace(" OR REPLACE", "")
        table = match.group("table")
        key = "%s %s" % (verb, table)
        by_statement[key] += 1
        by_table[table] += 1

        if table in ("kv_entries", "kv_meta"):
            value = _KV_KEY.search(statement)
            by_key[key][value.group("value") if value else "<unparsed>"] += 1

        frames = [frame for frame in current_stack.split(" <- ") if frame]
        site = " <- ".join(frames[-3:]) if frames else "<unknown>"
        by_site[key][site] += 1

    return {
        "probe_log": str(probe_log),
        "connections_to_live_db": connections,
        "statements_traced": statements,
        "dml_statement_total": sum(by_statement.values()),
        "dml_by_statement": dict(sorted(by_statement.items())),
        "dml_by_table": dict(sorted(by_table.items())),
        "dml_keys": {k: dict(v) for k, v in sorted(by_key.items())},
        "write_sites": {k: v.most_common(5) for k, v in sorted(by_site.items())},
    }


def analyze_dataversion(dataversion: Path) -> dict:
    """解析 `PRAGMA data_version` 时间线，得出提交次数与时间窗。"""
    init_version = None
    commits = []
    for line in _read_text(dataversion).splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            stamp = float(parts[0])
        except ValueError:
            continue
        if parts[1] == "INIT":
            init_version = int(parts[2])
        elif parts[1] == "COMMIT":
            commits.append((stamp, int(parts[2])))

    final_version = commits[-1][1] if commits else init_version
    window = (commits[-1][0] - commits[0][0]) if commits else 0.0
    return {
        "dataversion_file": str(dataversion),
        "data_version_initial": init_version,
        "data_version_final": final_version,
        "commit_count": len(commits),
        "window_seconds": round(window, 3),
        "first_commit_iso": (
            dt.datetime.fromtimestamp(commits[0][0], dt.timezone.utc).isoformat()
            if commits
            else None
        ),
        "last_commit_iso": (
            dt.datetime.fromtimestamp(commits[-1][0], dt.timezone.utc).isoformat()
            if commits
            else None
        ),
    }


PROBE_PLUGIN = r'''# -*- coding: utf-8 -*-
"""Throwaway pytest plugin: log every sqlite connection that targets the
live mainnet store, together with the statements it runs."""
import os
import sqlite3
import time
import traceback

import pytest

_LOG = os.environ["DBPROBE_LOG"]
_TARGET = os.environ["DBPROBE_TARGET"]
_MAX_STATEMENTS = 400
_real_connect = sqlite3.connect


def _resolve(target):
    try:
        text = str(target)
    except Exception:
        return ""
    if text.startswith("file:"):
        text = text[5:].split("?", 1)[0]
    try:
        return os.path.abspath(text).lower()
    except Exception:
        return ""


def _install(conn):
    seen = {"count": 0}

    def _trace(statement):
        if seen["count"] >= _MAX_STATEMENTS:
            return
        seen["count"] += 1
        try:
            with open(_LOG, "a", encoding="utf-8") as handle:
                handle.write("%.3f\tSQL\t%s\n" % (time.time(), statement.replace("\n", " ")))
        except OSError:
            pass

    try:
        conn.set_trace_callback(_trace)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _dbprobe_recorder():
    if getattr(sqlite3.connect, "_dbprobe_wrapped", False):
        yield
        return

    def _wrapped(*args, **kwargs):
        conn = _real_connect(*args, **kwargs)
        path = _resolve(args[0] if args else "")
        if path and path == _TARGET:
            stack = [
                "%s:%d:%s" % (frame.filename, frame.lineno, frame.name)
                for frame in traceback.extract_stack()[:-1]
                if "dbprobe" not in frame.filename
            ]
            try:
                with open(_LOG, "a", encoding="utf-8") as handle:
                    handle.write("%.3f\tCONNECT\t%s\n" % (time.time(), " <- ".join(reversed(stack[-10:]))))
            except OSError:
                pass
            _install(conn)
        return conn

    _wrapped._dbprobe_wrapped = True
    sqlite3.connect = _wrapped
    try:
        yield
    finally:
        sqlite3.connect = _real_connect
'''


def _watch_data_version(database: Path, out: Path, stop: threading.Event) -> None:
    """在独立线程里只读采样 `PRAGMA data_version`。"""
    samples = []
    connection = _open_readonly(database)
    last = connection.execute("PRAGMA data_version").fetchone()[0]
    samples.append((time.time(), "INIT", int(last)))
    while not stop.is_set():
        try:
            current = connection.execute("PRAGMA data_version").fetchone()[0]
        except sqlite3.Error:
            break
        if current != last:
            samples.append((time.time(), "COMMIT", int(current)))
            last = current
        time.sleep(0.1)
    connection.close()
    with out.open("w", encoding="utf-8") as handle:
        for stamp, kind, version in samples:
            handle.write("%.3f\t%s\t%d\n" % (stamp, kind, version))


def measure(args) -> dict:
    """在线复测：跑一遍 pytest，同时采语句级探针与 data_version 时间线。

    该模式**会**向生产主库写入（这正是被测缺陷本身）。
    仅在需要重取证据时使用。
    """
    if not LIVE_DB.exists():
        raise SystemExit("live database not found: %s" % LIVE_DB)

    workdir = Path(tempfile.mkdtemp(prefix="task184_measure_"))
    plugin = workdir / "dbprobe_plugin.py"
    plugin.write_text(PROBE_PLUGIN, encoding="utf-8")
    probe_log = workdir / "probe.log"
    dataversion = workdir / "dataversion.tsv"
    probe_log.touch()

    env = dict(os.environ)
    env["DBPROBE_LOG"] = str(probe_log)
    env["DBPROBE_TARGET"] = str(LIVE_DB).lower()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(workdir), env.get("PYTHONPATH", "")]  # noqa: W503
    ).strip(os.pathsep)

    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_data_version, args=(LIVE_DB, dataversion, stop), daemon=True
    )
    watcher.start()
    started = time.time()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "dbprobe_plugin", *args.pytest_args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stop.set()
    watcher.join(timeout=10)
    elapsed = time.time() - started

    tail = (completed.stdout or "").strip().splitlines()[-3:]
    return {
        "mode": "measure",
        "pytest_args": args.pytest_args,
        "pytest_exit_code": completed.returncode,
        "pytest_tail": tail,
        "elapsed_seconds": round(elapsed, 3),
        "probe_log": str(probe_log),
        "dataversion_file": str(dataversion),
    }


def build_report(args, extra: dict) -> dict:
    report = {
        "report": "task183_184_production_db_writes",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "label": args.label,
        "live_database": str(LIVE_DB),
        "collection_method": (
            "live measurement (subprocess pytest + statement probe + data_version sampler)"
            if args.measure
            else "offline analysis of captured probe artifacts (no database access)"
        ),
        "caveats": [
            "The collector itself never writes: the live database is only opened "
            "with mode=ro and PRAGMA query_only=ON.",
            "data_version counts commit transactions observed by an external "
            "read-only connection; attribution to the pytest window rests on a "
            "silent control window of the same shape recording zero commits.",
        ],
    }
    report.update(extra)
    if args.probe_log:
        report["probe_analysis"] = analyze_probe_log(Path(args.probe_log))
    if args.dataversion:
        report["dataversion_analysis"] = analyze_dataversion(Path(args.dataversion))
    return report


def render_markdown(report: dict) -> str:
    lines = []
    lines.append("# %s —— 测试套件对生产主网库的写入取证" % report.get("label", ""))
    lines.append("")
    lines.append("- 生成时间（UTC）：`%s`" % report.get("generated_at"))
    lines.append("- 生产主库：`%s`" % report.get("live_database"))
    lines.append("- 采集方式：%s" % report.get("collection_method"))
    lines.append("")

    dataversion = report.get("dataversion_analysis")
    if dataversion:
        lines.append("## 1. 提交计数（`PRAGMA data_version`，只读采样）")
        lines.append("")
        lines.append("| 项 | 值 |")
        lines.append("|---|---|")
        lines.append("| 初始 `data_version` | `%s` |" % dataversion["data_version_initial"])
        lines.append("| 结束 `data_version` | `%s` |" % dataversion["data_version_final"])
        lines.append("| **提交次数** | **%s** |" % dataversion["commit_count"])
        lines.append("| 观测窗口 | `%s` s |" % dataversion["window_seconds"])
        lines.append("")

    probe = report.get("probe_analysis")
    if probe:
        lines.append("## 2. 语句级 DML 分布（探针打点）")
        lines.append("")
        lines.append("- 命中生产库的连接数：**%s**" % probe["connections_to_live_db"])
        lines.append("- 打点语句总数：**%s**" % probe["statements_traced"])
        lines.append("- DML 语句总数：**%s**" % probe["dml_statement_total"])
        lines.append("")
        lines.append("| 语句 | 次数 |")
        lines.append("|---|---|")
        for name, count in probe["dml_by_statement"].items():
            lines.append("| `%s` | %s |" % (name, count))
        lines.append("")
        if probe.get("dml_keys"):
            lines.append("### 2.1 写入的键分布")
            lines.append("")
            lines.append("| 语句 | 键 | 次数 |")
            lines.append("|---|---|---|")
            for name, keys in probe["dml_keys"].items():
                for key, count in keys.items():
                    lines.append("| `%s` | `%s` | %s |" % (name, key, count))
            lines.append("")
        lines.append("### 2.2 写入来源代码位点（每类前 5）")
        lines.append("")
        for name, sites in probe.get("write_sites", {}).items():
            lines.append("**`%s`**" % name)
            lines.append("")
            for site, count in sites:
                lines.append("- %s —— %s 次" % (site, count))
            lines.append("")

    if "pytest_exit_code" in report:
        lines.append("## 3. pytest 运行读数")
        lines.append("")
        lines.append("- 退出码：`%s`" % report["pytest_exit_code"])
        lines.append("- 用时：`%s` s" % report["elapsed_seconds"])
        lines.append("")
        lines.append("```")
        for line in report.get("pytest_tail", []):
            lines.append(line)
        lines.append("```")
        lines.append("")

    lines.append("## 4. 判据与限制")
    lines.append("")
    for caveat in report.get("caveats", []):
        lines.append("- %s" % caveat)
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-log", default="")
    parser.add_argument("--dataversion", default="")
    parser.add_argument("--label", default="post_fix")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--measure", action="store_true")
    parser.add_argument("pytest_args", nargs="*")
    args = parser.parse_args()

    extra = {}
    if args.measure:
        extra = measure(args)
        if not args.probe_log:
            args.probe_log = extra["probe_log"]
        if not args.dataversion:
            args.dataversion = extra["dataversion_file"]

    report = build_report(args, extra)
    Path(args.out_json).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path(args.out_md).write_text(render_markdown(report), encoding="utf-8")
    print("wrote %s / %s" % (args.out_json, args.out_md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
