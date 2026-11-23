#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务归档工具（Task -> Archive）· 支持两级

**二级任务**（`T-nn`）：
  把 `plan/tasks/<一级>/tasks/T-nn/` 的四文件打包成**单个**归档 md 放入 **`plan/archive/`**（受控归档区），
  并同步登记两处索引：

    1. `plan/archive/README.md`            §1 已闭合任务归档索引
    2. `plan/tasks/<一级>/docs/未完成任务清单_20261123.md`  §6 已闭合任务表

  最后**删除** `plan/tasks/<一级>/tasks/T-nn/` 原目录。

**一级任务**（`P-nn-标题`）：
  把 `plan/tasks/P-nn-标题/` 的四文件打包成 `plan/archive/方案归档_P-nn-标题_<日期>.md`，
  并**附上全部二级归档件清单**；最后**删除整个一级目录**。

用法::

    # 二级
    python plan/runtime_evidence/tools/archive_task_20261124.py T-01
    python plan/runtime_evidence/tools/archive_task_20261124.py T-01 --dry-run

    # 一级（全部二级闭合后）
    python plan/runtime_evidence/tools/archive_task_20261124.py P-01-池子重构

规则：
  * 编号**保持原样**，标题**沿用**原目录，不重命名、不改号。
  * `docs/` \u6280\u672f\u6587\u6848\u533a\u4e5f\u4e00\u8d77\u6253\u5305\u8fdb\u5f52\u6863\u4ef6\u3002
  * 归档件内容**冻结**；更正走新版本 `..._v2.md`，旧版保留。
  * `--dry-run` 只打印将要发生的事，不落盘、不删除。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import io
import os
import re
import shutil
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
PLAN_DIR = os.path.join(REPO_ROOT, "plan")
TASKS_DIR = os.path.join(PLAN_DIR, "tasks")
ARCHIVE_DIR = os.path.join(PLAN_DIR, "archive")
BACK_DIR = os.path.join(PLAN_DIR, "back")  # \u4e34\u65f6\u901a\u9053\uff1a\u975e\u53d7\u63a7\uff0c\u4e0d\u653e\u5f52\u6863\u4ef6\uff08\u4e0d\u5199\u5165\uff09
ARCHIVE_INDEX = os.path.join(ARCHIVE_DIR, "README.md")  # \u53d7\u63a7\u5f52\u6863\u533a\u7684\u552f\u4e00\u7d22\u5f15


PART_FILES = [
    ("PLAN.md", "\u2460 \u4efb\u52a1\u8bf4\u660e \uff0b \u2461 \u5f00\u53d1\u8ba1\u5212"),
    ("SCOPE_GUARD.md", "\u2462 \u4fee\u6539\u8303\u56f4\u5b88\u62a4"),
    ("checklist.md", "\u2463 \u6d4b\u8bd5\u76ee\u6807 check list"),
    ("README.md", "\u5bfc\u822a"),
]


def read(path: str) -> str:
    with io.open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace("\n", "\r\n"))


def find_todo_list() -> str:
    """\u4e13\u9898\u6e05\u5355\uff08\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355\uff09\uff1a\u5148\u5728\u4e00\u7ea7\u4efb\u52a1\u7684 docs/ \u91cc\u627e\uff0c\u627e\u4e0d\u5230\u518d\u56de\u9000\u5230 plan/ \u6839\u3002"""
    for pat in ("plan/tasks/P-*/docs/\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355_*.md",
                "plan/*/\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355_*.md"):
        hits = sorted(glob.glob(os.path.join(REPO_ROOT, pat)))
        if hits:
            return hits[0]
    return ""


def task_title(task_dir: str) -> str:
    plan_path = os.path.join(task_dir, "PLAN.md")
    if not os.path.exists(plan_path):
        return os.path.basename(task_dir)
    plan = read(plan_path)
    for line in plan.splitlines():
        if line.startswith("# ") and "\uff5c" in line:
            tail = line.split("\uff5c", 1)[1]
            return tail.split("\u00b7", 1)[0].strip()
    return os.path.basename(task_dir)


def acceptance_id(task_dir: str) -> str:
    plan_path = os.path.join(task_dir, "PLAN.md")
    if not os.path.exists(plan_path):
        return "\u2014"
    plan = read(plan_path)
    m = re.search(r"\*\*\u9a8c\u6536\u7f16\u53f7\uff08\u7eaf ID\uff09\*\*\s*\|\s*\**`?(AC-\d+)`?", plan)
    return m.group(1) if m else "\u2014"


def detect_level(target_id: str) -> str:
    """Return 'primary' (P-nn-...) or 'secondary' (T-nn)."""
    return "primary" if target_id.startswith("P-") else "secondary"


def resolve_task_dir(target_id: str) -> str:
    """\u4e8c\u7ea7\u4efb\u52a1\uff1a\u5728\u6240\u6709\u4e00\u7ea7\u4efb\u52a1\u7684 tasks/ \u4e0b\u5b9a\u4f4d\u3002"""
    direct = os.path.join(TASKS_DIR, target_id)
    if os.path.isdir(direct):
        return direct
    hits = sorted(glob.glob(os.path.join(TASKS_DIR, "P-*", "tasks", target_id)))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SystemExit("ambiguous task id %s: %s" % (target_id, hits))
    raise SystemExit("task dir not found: %s" % target_id)


def owner_primary(task_dir: str) -> str:
    """\u8fd4\u56de\u6240\u5c5e\u4e00\u7ea7\u4efb\u52a1\u76ee\u5f55\u540d\uff08\u4e8c\u7ea7\u4efb\u52a1\u7528\uff09\u3002"""
    parent = os.path.dirname(os.path.dirname(task_dir))
    if os.path.basename(parent) == "tasks" and os.path.basename(os.path.dirname(parent)) != "tasks":
        return os.path.basename(os.path.dirname(parent))
    return ""


def build_archive(task_dir: str, task_id: str, title: str, date: str, level: str) -> str:
    chunks = []
    chunks.append("# `%s`\uff5c%s \u00b7 \u4efb\u52a1\u5f52\u6863\uff08**\u5df2\u95ed\u5408**\uff09" % (task_id, title))
    chunks.append("")
    chunks.append("> **\u5f52\u6863\u5143\u4fe1\u606f**")
    chunks.append(">")
    chunks.append("> | \u9879 | \u503c |")
    chunks.append("> |---|---|")
    chunks.append("> | \u4efb\u52a1\u7f16\u53f7\uff08\u7eaf ID\uff09 | `%s` |" % task_id)
    chunks.append("> | \u9a8c\u6536\u7f16\u53f7\uff08\u7eaf ID\uff09 | `%s` |" % acceptance_id(task_dir))
    chunks.append("> | \u6807\u9898 | %s |" % title)
    chunks.append("> | \u5c42\u7ea7 | %s |" % ("\u4e00\u7ea7\uff08\u65b9\u6848\uff0f\u8ba1\u5212\u7ea7\uff09" if level == "primary" else "\u4e8c\u7ea7\uff08\u6267\u884c\u7ea7\uff09"))
    owner = owner_primary(task_dir)
    if owner:
        chunks.append("> | \u6240\u5c5e\u4e00\u7ea7\u4efb\u52a1 | `%s` |" % owner)
    chunks.append("> | \u5f52\u6863\u65e5\u671f | %s |" % date)
    chunks.append("> | \u539f\u8def\u5f84 | `%s` |" % os.path.relpath(task_dir, REPO_ROOT).replace("\\", "/"))
    chunks.append("> | \u5f52\u6863\u7d22\u5f15 | `plan/archive/README.md` \u00a71 |")
    chunks.append(">")
    chunks.append("> **\u672c\u4ef6\u5df2\u51bb\u7ed3** \u2014\u2014 \u5185\u5bb9\u4e0d\u518d\u6539\u52a8\u3002\u66f4\u6b63\u8d70\u65b0\u7248\u672c `..._v2.md`\uff0c\u65e7\u7248\u4fdd\u7559\u3002")
    chunks.append("")
    chunks.append("---")
    chunks.append("")

    for fname, section in PART_FILES:
        path = os.path.join(task_dir, fname)
        chunks.append("## %s" % section)
        chunks.append("")
        chunks.append("<!-- \u539f\u6587\u4ef6\uff1a`%s` -->" % os.path.relpath(path, REPO_ROOT).replace("\\", "/"))
        chunks.append("")
        if os.path.exists(path):
            body = read(path).replace("\r\n", "\n").strip()
            chunks.append(body)
        else:
            chunks.append("> **\uff08\u8be5\u6587\u4ef6\u5728\u5f52\u6863\u65f6\u4e0d\u5b58\u5728\uff09**")
        chunks.append("")
        chunks.append("---")
        chunks.append("")

    if level == "primary":
        chunks.append("## \u4e8c\u7ea7\u4efb\u52a1\u5f52\u6863\u4ef6\u6e05\u5355")
        chunks.append("")
        chunks.append("> **\u4e8c\u7ea7\u5f52\u6863\u4ef6\u672c\u4f53\u4e0d\u5408\u5e76\u8fdb\u672c\u4ef6** \u2014\u2014 \u4fdd\u6301\u300c\u4e00\u7ea7\u770b\u5168\u5c40\u3001\u4e8c\u7ea7\u770b\u7ec6\u8282\u300d\u3002")
        chunks.append("")
        chunks.append("| \u4e8c\u7ea7 | \u6807\u9898 | \u5f52\u6863\u4ef6 |")
        chunks.append("|---|---|---|")
        for f in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "\u4efb\u52a1\u5f52\u6863_*.md"))):
            base = os.path.basename(f)
            m = re.match(r"\u4efb\u52a1\u5f52\u6863_(T-\d+)_(.+)_(\d{8})\.md$", base)
            if m:
                chunks.append("| `%s` | %s | [`%s`](./%s) |" % (m.group(1), m.group(2), base, base))
        chunks.append("")

    return "\n".join(chunks)


def register_back_index(task_id: str, title: str, date: str, filename: str, summary: str, level: str) -> bool:
    s = read(ARCHIVE_INDEX)
    if filename in s:
        return False
    anchor = "| \uff08\u6682\u65e0\uff09 | \u2014 | \u2014 | \u2014 | \u2014 | \u2014 |"
    if level == "primary":
        row = "| [%s](./%s) | `%s` | %s | %s | `plan/tasks/%s/` | %s |" % (
            filename, filename, task_id, title, date, task_id, summary)
    else:
        owner = task_id_owner(task_id)
        row = "| [%s](./%s) | `%s` | %s | %s | `plan/tasks/%s/tasks/%s/` | %s |" % (
            filename, filename, task_id, title, date, owner, task_id, summary)
    if anchor in s:
        s = s.replace(anchor, row, 1)
    else:
        raise SystemExit("back index anchor not found")
    write(ARCHIVE_INDEX, s)
    return True


def task_id_owner(task_id: str) -> str:
    hits = sorted(glob.glob(os.path.join(TASKS_DIR, "P-*", "tasks", task_id)))
    if hits:
        return os.path.basename(os.path.dirname(os.path.dirname(hits[0])))
    return ""


def register_todo_list(task_id: str, title: str, date: str) -> bool:
    todo = find_todo_list()
    if not todo:
        print("todo list not found - skip registration")
        return False
    s = read(todo)
    if task_id in s.split("## 6.")[-1][:5000] if "## 6." in s else False:
        return False
    lines = s.replace("\r\n", "\n").split("\n")
    for idx, line in enumerate(lines):
        if line.startswith("## 6."):
            for j in range(idx, min(idx + 60, len(lines))):
                if lines[j].startswith("|") and "---" in lines[j]:
                    owner = task_id_owner(task_id)
                    path = ("`plan/tasks/%s/tasks/%s/`" % (owner, task_id)) if owner else ("`plan/tasks/%s/`" % task_id)
                    row = "| `%s` | %s | \u5df2\u95ed\u5408\u00b7\u5df2\u5f52\u6863\uff08%s\uff09 | %s |" % (
                        task_id, title, date, path)
                    lines.insert(j + 1, row)
                    write(todo, "\n".join(lines))
                    return True
            break
    print("todo list \u00a76 table not found - skip registration")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive a closed task (primary or secondary) into plan/back/")
    ap.add_argument("task_id", help="P-nn-<title> (primary) or T-nn (secondary)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date", default=_dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--summary", default="")
    args = ap.parse_args()

    task_id = args.task_id
    level = detect_level(task_id)
    task_dir = resolve_task_dir(task_id)
    title = task_title(task_dir)
    date = args.date
    safe_title = re.sub(r"[\\/:*?\"<>|\s]+", "_", title)
    if level == "primary":
        # 一级：目录名已含标题（P-nn-标题）=> 不再追加标题
        filename = "\u65b9\u6848\u5f52\u6863_%s_%s.md" % (task_id, date)
    else:
        filename = "\u4efb\u52a1\u5f52\u6863_%s_%s_%s.md" % (task_id, safe_title, date)
    target = os.path.join(ARCHIVE_DIR, filename)

    body = build_archive(task_dir, task_id, title, date, level)
    summary = args.summary or ("\u4e00\u7ea7\u65b9\u6848\u5f52\u6863\uff1b\u542b\u5168\u90e8\u4e8c\u7ea7\u5f52\u6863\u4ef6\u6e05\u5355"
                               if level == "primary"
                               else "\u56db\u90e8\u5206\u6253\u5305\u5f52\u6863\uff1b\u6539\u52a8\u6a21\u5757\u4e0e\u56de\u5f52\u547d\u4ee4\u89c1\u5f52\u6863\u4ef6 \u2462")

    print("level     : %s" % level)
    print("task      : %s" % task_id)
    print("title     : %s" % title)
    print("archive   : %s" % target)
    print("indexes   : %s  +  \u4e13\u9898\u6e05\u5355 \u00a76" % os.path.relpath(ARCHIVE_INDEX, REPO_ROOT))
    print("remove    : %s" % os.path.relpath(task_dir, REPO_ROOT))
    print("bytes     : %d" % len(body.encode("utf-8")))

    if args.dry_run:
        print("\n--dry-run-- no files written, nothing removed.")
        return 0

    if os.path.exists(target):
        print("target already exists: %s" % target)
        return 3

    write(target, body)
    register_back_index(task_id, title, date, filename, summary, level)
    register_todo_list(task_id, title, date)
    print("archived OK")

    shutil.rmtree(task_dir)
    print("removed %s" % os.path.relpath(task_dir, REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
