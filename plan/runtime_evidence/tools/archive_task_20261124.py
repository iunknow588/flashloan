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


# ---------------------------------------------------------------- 归档件内部锚点

_DOC_ID_RE = re.compile(r"^D-(\d+)")


def anchor_for(rel_path: str) -> str:
    """归档件内的**稳定锚点 id**（由源文件相对路径推导，幂等）。

    - ``D-083-PLAN.md``     -> ``d083``
    - ``docs/D-099-xxx.md`` -> ``d099``
    - ``README.md``         -> ``readme``
    - ``docs/README.md``    -> ``docs-readme``
    """
    parts = rel_path.replace("\\", "/").split("/")
    base = parts[-1]
    m = _DOC_ID_RE.match(base)
    if m:
        return "d" + m.group(1)
    stem = base.rsplit(".", 1)[0]
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", stem).strip("-").lower()
    prefix = "docs-" if len(parts) > 1 else ""
    return prefix + slug


def collect_task_files(task_dir: str):
    """收集归档件要打包的源文件：**四个部分文件 ＋ ``docs/`` 全部 md**。

    返回 ``[(rel_path, abs_path, anchor), ...]``，``rel_path`` 相对任务目录。
    """
    out = []
    for fname, _section in PART_FILES:
        path = _task_file(task_dir, fname)
        if path and os.path.exists(path):
            rel = os.path.relpath(path, task_dir).replace("\\", "/")
            out.append((rel, path, anchor_for(rel)))
    docs_dir = os.path.join(task_dir, "docs")
    if os.path.isdir(docs_dir):
        for name in sorted(os.listdir(docs_dir)):
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(docs_dir, name)
            rel = "docs/" + name
            out.append((rel, path, anchor_for(rel)))
    return out


_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def rewrite_packed_links(body: str, src_rel: str, anchor_map: dict) -> str:
    """把归档件内部指向同任务文件的**相对链接**改写成 ``#锚点``。

    归档件是一个**合并后的单文件**，原来的 ``./D-083-PLAN.md``、
    ``docs/D-099-....md`` 在合并后不再可解析 ⇒ 必须改写为锚点，
    否则归档件自带断链。
    """
    src_dir = os.path.dirname(src_rel)

    def _sub_link(m):
        label, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        bare = target.split("#", 1)[0]
        if not bare:
            return m.group(0)
        resolved = os.path.normpath(os.path.join(src_dir, bare)).replace("\\", "/")
        if resolved in anchor_map:
            return "[%s](#%s)" % (label, anchor_map[resolved])
        return m.group(0)

    body = _MD_LINK_RE.sub(_sub_link, body)
    for rel in list(anchor_map):
        base = rel.rsplit("/", 1)[-1]
        if base not in body:
            continue
        doc_id = _DOC_ID_RE.match(base)
        body = body.replace(base, doc_id.group(0) if doc_id else base)
    return body


def rewrite_inbound_links(task_dir: str, archive_path: str, anchor_map: dict) -> list:
    """**入向链接重写**：把 ``plan/`` 下指向被删任务目录的链接改指归档件。

    返回 ``[(文件, 改写次数), ...]``。目录级链接指向归档件本身；
    文件级链接指向归档件内的**对应锚点**。
    """
    prefix = os.path.relpath(task_dir, REPO_ROOT).replace("\\", "/")
    touched = []
    task_abs = os.path.abspath(task_dir)
    for root, dirs, files in os.walk(PLAN_DIR):
        dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(root, d)) != os.path.abspath(ARCHIVE_DIR)]
        if os.path.abspath(root) == task_abs or os.path.abspath(root).startswith(task_abs + os.sep):
            continue
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(root, name)
            text = read(path)
            if prefix not in text and os.path.basename(task_dir) not in text:
                continue
            base_dir = os.path.dirname(path)
            rel_to_archive = os.path.relpath(archive_path, base_dir).replace("\\", "/")

            def _repl(m):
                label, target = m.group(1), m.group(2)
                if target.startswith(("http://", "https://", "mailto:")):
                    return m.group(0)
                bare = target.split("#", 1)[0]
                if not bare:
                    return m.group(0)
                if bare.startswith(prefix):
                    abs_target = os.path.normpath(os.path.join(REPO_ROOT, bare.replace("/", os.sep)))
                else:
                    abs_target = os.path.normpath(os.path.join(base_dir, bare.replace("/", os.sep)))
                if abs_target != task_abs and not abs_target.startswith(task_abs + os.sep):
                    return m.group(0)
                tail = os.path.relpath(abs_target, task_abs).replace("\\", "/")
                if tail == ".":
                    tail = ""
                if not tail:
                    return "[%s](%s)" % (label, rel_to_archive)
                anchor = anchor_map.get(tail)
                if anchor:
                    return "[%s](%s#%s)" % (label, rel_to_archive, anchor)
                return "[%s](%s)" % (label, rel_to_archive)

            new_text = _MD_LINK_RE.sub(_repl, text)
            # A **back-tick** path in these documents is **repo-root
            # relative** (``plan/archive/...``), not relative to the file
            # that holds it.  A markdown link is relative to its own file,
            # so the two forms must not be rewritten the same way.
            rel_to_repo_archive = os.path.relpath(archive_path, REPO_ROOT).replace("\\", "/")
            for rel in list(anchor_map):
                old_full = "%s/%s" % (prefix, rel)
                if old_full in new_text:
                    new_text = new_text.replace(old_full, "%s#%s" % (rel_to_repo_archive, anchor_map[rel]))
            if new_text != text:
                write(path, new_text)
                touched.append((path, 1))
    return touched


def update_registry_paths(task_dir: str, archive_path: str, anchor_map: dict) -> int:
    """更新编号册 §5.5 文档总表里指向被删目录的**路径列**。"""
    registry = os.path.join(PLAN_DIR, "D-001-TASK_REGISTRY.md")
    if not os.path.exists(registry):
        return 0
    text = read(registry)
    prefix = os.path.relpath(task_dir, REPO_ROOT).replace("\\", "/")
    n = 0
    for rel in list(anchor_map):
        old = "%s/%s" % (prefix, rel)
        if old in text:
            text = text.replace(old, "%s#%s" % (archive_path.replace("\\", "/"), anchor_map[rel]))
            n += 1
    if n:
        write(registry, text)
    return n


_ARCHIVE_ANCHOR_RE = re.compile(r'<a id="([^"]+)"></a>')
_ARCHIVE_ORIG_RE = re.compile(r'<!-- \u539f\u6587\u4ef6\uff1a`([^`]+)` -->')


def parse_archive_anchors(archive_path: str):
    """从**已生成的归档件**反解 ``(任务目录, {相对路径: 锚点})``。

    归档件里每个被打包的源文件前都有 ``<a id=...>`` 与「原文件」注释 ⇒
    归档完成后**不需要**原目录仍在，就能重建锚点表（用于补做链接重写）。
    """
    text = read(archive_path)
    anchors = _ARCHIVE_ANCHOR_RE.findall(text)
    origs = _ARCHIVE_ORIG_RE.findall(text)
    pairs = list(zip(anchors, origs))
    if not pairs:
        return "", {}
    dirs = set()
    mapping = {}
    for anchor, orig in pairs:
        rel = orig.replace("\\", "/")
        parts = rel.split("/")
        # 去掉文件名 ⇒ 任务目录
        d = "/".join(parts[:-1])
        dirs.add(d)
        mapping["/".join(parts[-1:]) if len(parts) == 1 else rel[len(d) + 1:]] = anchor
    task_dir_rel = sorted(dirs, key=len)[0] if dirs else ""
    anchor_map = {}
    for anchor, orig in pairs:
        rel = orig.replace("\\", "/")
        if not task_dir_rel or not rel.startswith(task_dir_rel + "/"):
            continue
        anchor_map[rel[len(task_dir_rel) + 1:]] = anchor
    return task_dir_rel, anchor_map


def read(path: str) -> str:
    # utf-8-sig：**吞掉 BOM** —— 否则 BOM 会被原样打进归档件正文，
    # 使打包后的标题行不再以 `# D-nnn ` 开头（编号纪律校验会失败）。
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        # 先归一到 LF 再统一转 CRLF —— 否则对已是 CRLF 的文件会写成 \r\r\n
        fh.write(text.replace("\r\n", "\n").replace("\n", "\r\n"))


def find_todo_list() -> str:
    """\u4e13\u9898\u6e05\u5355\uff08\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355\uff09\uff1a\u5148\u5728\u4e00\u7ea7\u4efb\u52a1\u7684 docs/ \u91cc\u627e\uff0c\u627e\u4e0d\u5230\u518d\u56de\u9000\u5230 plan/ \u6839\u3002"""
    for pat in ("plan/tasks/P-*/docs/D-*\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355_*.md",
                "plan/*/D-*\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355_*.md",
                "plan/tasks/P-*/docs/\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355_*.md",
                "plan/*/\u672a\u5b8c\u6210\u4efb\u52a1\u6e05\u5355_*.md"):
        hits = sorted(glob.glob(os.path.join(REPO_ROOT, pat)))
        if hits:
            return hits[0]
    return ""


def _task_file(task_dir: str, name: str) -> str:
    """任务目录内的文件已带文档编号前缀 `D-nnn-`：定位带号与不带号两种形式。"""
    direct = os.path.join(task_dir, name)
    if os.path.exists(direct):
        return direct
    hits = sorted(glob.glob(os.path.join(task_dir, "D-*-" + name)))
    return hits[0] if hits else ""


def task_title(task_dir: str) -> str:
    plan_path = _task_file(task_dir, "PLAN.md")
    if not plan_path or not os.path.exists(plan_path):
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
    task_files = collect_task_files(task_dir)
    anchor_map = {rel: anchor for rel, _abs, anchor in task_files}
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
        path = _task_file(task_dir, fname) or os.path.join(task_dir, fname)
        rel = os.path.relpath(path, task_dir).replace("\\", "/") if os.path.exists(path) else fname
        anchor = anchor_map.get(rel)
        chunks.append("## %s" % section)
        chunks.append("")
        if anchor:
            chunks.append('<a id="%s"></a>' % anchor)
            chunks.append("")
        chunks.append("<!-- \u539f\u6587\u4ef6\uff1a`%s` -->" % os.path.relpath(path, REPO_ROOT).replace("\\", "/"))
        chunks.append("")
        if os.path.exists(path):
            body = rewrite_packed_links(read(path).replace("\r\n", "\n").strip(), rel, anchor_map)
            chunks.append(body)
        else:
            chunks.append("> **\uff08\u8be5\u6587\u4ef6\u5728\u5f52\u6863\u65f6\u4e0d\u5b58\u5728\uff09**")
        chunks.append("")
        chunks.append("---")
        chunks.append("")

    docs = [(rel, path, anchor) for rel, path, anchor in task_files if rel.startswith("docs/")]
    if docs:
        chunks.append("## \u2464 \u6280\u672f\u6587\u6848\u533a\uff08`docs/`\uff09")
        chunks.append("")
        chunks.append("> **\u539f `docs/` \u76ee\u5f55\u5df2\u5e76\u5165\u672c\u5f52\u6863\u4ef6** \u2014\u2014 \u9010\u4efd\u4fdd\u7559\uff0c\u951a\u70b9\u89c1\u4e0b\u3002")
        chunks.append("")
        for rel, path, anchor in docs:
            base = rel.rsplit("/", 1)[-1]
            chunks.append('<a id="%s"></a>' % anchor)
            chunks.append("")
            chunks.append("### `%s`" % base)
            chunks.append("")
            chunks.append("<!-- \u539f\u6587\u4ef6\uff1a`%s` -->" % os.path.relpath(path, REPO_ROOT).replace("\\", "/"))
            chunks.append("")
            chunks.append(rewrite_packed_links(read(path).replace("\r\n", "\n").strip(), rel, anchor_map))
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
            m = re.match(r"\u4efb\u52a1\u5f52\u6863_(T-[^_]+)_(.+)_(\d{8})\.md$", base)
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
        lines = s.replace("\r\n", "\n").split("\n")
        start = 0
        for idx, line in enumerate(lines):
            if line.startswith("## 1."):
                start = idx
                break
        for idx in range(start, len(lines)):
            line = lines[idx]
            if line.startswith("|") and set(line.replace("|", "").replace("-", "").strip()) <= {" "} and "-" in line:
                lines.insert(idx + 1, row)
                s = "\n".join(lines)
                break
        else:
            raise SystemExit("back index anchor not found")
    write(ARCHIVE_INDEX, s)
    return True


def task_id_owner(task_id: str) -> str:
    hits = sorted(glob.glob(os.path.join(TASKS_DIR, "P-*", "tasks", task_id)))
    if hits:
        return os.path.basename(os.path.dirname(os.path.dirname(hits[0])))
    return ""


def register_todo_list(task_id: str, title: str, date: str, filename: str = "") -> bool:
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
                    path = "`plan/archive/%s`" % filename if filename else "\u2014"
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
    ap.add_argument("--relink-only", action="store_true",
                    help="\u5f52\u6863\u5df2\u5b8c\u6210\u65f6\u8865\u505a\u5165\u5411\u94fe\u63a5\u91cd\u5199\uff08\u4e0d\u6253\u5305\u3001\u4e0d\u5220\u76ee\u5f55\uff09")
    args = ap.parse_args()

    task_id = args.task_id
    level = detect_level(task_id)

    if args.relink_only:
        hits = sorted(glob.glob(os.path.join(ARCHIVE_DIR, "\u4efb\u52a1\u5f52\u6863_%s_*.md" % task_id)))
        if not hits:
            raise SystemExit("archive file not found for %s" % task_id)
        archive = hits[-1]
        task_dir_rel, anchor_map = parse_archive_anchors(archive)
        if not anchor_map:
            raise SystemExit("no anchors parsed from %s" % archive)
        task_dir = os.path.join(REPO_ROOT, task_dir_rel.replace("/", os.sep))
        relinked = rewrite_inbound_links(task_dir, archive, anchor_map)
        reg_rows = update_registry_paths(task_dir, os.path.relpath(archive, REPO_ROOT).replace("\\", "/"), anchor_map)
        print("archive   : %s" % archive)
        print("task dir  : %s" % task_dir_rel)
        print("relinked  : %d file(s)" % len(relinked))
        print("registry  : %d path cell(s)" % reg_rows)
        return 0

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
    register_todo_list(task_id, title, date, filename)
    task_files = collect_task_files(task_dir)
    anchor_map = {rel: anchor for rel, _abs, anchor in task_files}
    relinked = rewrite_inbound_links(task_dir, target, anchor_map)
    reg_rows = update_registry_paths(task_dir, os.path.relpath(target, REPO_ROOT).replace("\\", "/"), anchor_map)
    print("relinked   : %d file(s)" % len(relinked))
    print("registry   : %d path cell(s) updated" % reg_rows)
    print("archived OK")

    shutil.rmtree(task_dir)
    print("removed %s" % os.path.relpath(task_dir, REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
