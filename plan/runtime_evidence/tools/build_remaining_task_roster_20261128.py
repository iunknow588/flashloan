#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the roster of the **unfinished** work with its decomposition numbers.

The plan keeps every open item in the checklist of the task that owns it.
That is the right place for an acceptance box, but it is the wrong place for
an **execution order**: the items of one task are spread over several waves,
and the items of two tasks can be in the same wave.

This tool therefore reads the checklists (read only) and writes **one**
roster in which every unfinished box carries a number of its own:

    WS-nn-ii    the wave (nn) and the item inside the wave (ii)

The number is **derived**, never typed by hand: the same checklist always
produces the same numbers, so the roster can be regenerated and compared
after every round.  The authoritative identity of an item stays the box it
came from (`T-核.08 / B7`), and the roster prints that next to the number.

Usage::

    python build_remaining_task_roster_20261128.py [--out <path>]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
P01 = REPO / "plan" / "tasks" / "P-01-池子重构"
TASKS = P01 / "tasks"
D004 = P01 / "D-004-checklist.md"
D011 = P01 / "docs" / "D-011-check_list_20261123.md"

UNCHECKED = re.compile(r"^\s*-\s\[ \]\s*(.*)$")
BOX_ID = re.compile(r"\*\*(?:\*\*)?([A-Za-z0-9][A-Za-z0-9.\-]*)")
LANDING = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:rs|py|ps1|sql|json|md))|`([^`]{4,80})`"
)

# The waves: which task belongs to which wave, and what the wave is for.
# The order is the execution order -- the first wave is the one whose items
# unblock the writes of everything else.
WAVES: list[tuple[str, str, list[str]]] = [
    (
        "WS-01",
        "写锁根治（三套存储真正独立 · 常驻 worker 重新注册）",
        ["T-核.08", "T-核.09", "T-储接核.01"],
    ),
    (
        "WS-02",
        "复核完整性与退役语义（缺值归零 · 覆盖补齐 · ok=false 根因）",
        ["T-复.03", "T-复储.01", "T-复广.01", "T-复编.02", "T-复编储.01"],
    ),
    (
        "WS-03",
        "广播链路实盘贯通（月度库第一行 · 广播记录 · 广播门禁）",
        ["T-广.02", "T-广.03", "T-广储.01"],
    ),
    (
        "WS-04",
        "池子与作用域（加载模式不得收窄 · 候选代币 · 闭环重建）",
        ["T-池编.01", "T-池编.02", "T-编.01", "T-编.02", "T-编.03", "T-候.01", "T-候编储.01"],
    ),
    (
        "WS-05",
        "方向层与闭环层的读面（排序守卫 · 快照滞后 · 代次感知）",
        ["T-广核.01", "T-广核.02", "T-核.06", "T-核.07"],
    ),
]

WAVES.append(("WS-06", "一级特有项（跨二级的一致性 · 整体回归 · 整体读数）", []))
WAVES.append(("WS-07", "横切项（性能 · 总体停止条件 · 归档门槛）", []))


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split(chr(10))


def section(lines: list[str], start: str) -> list[str]:
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith(start)
            continue
        if inside:
            collected.append(line)
    return collected


def task_checklist(directory: Path) -> Path | None:
    for candidate in sorted(directory.glob("*-checklist.md")):
        return candidate
    return None


def landing_points(text: str) -> str:
    """The files and the tests the box itself names."""
    found: list[str] = []
    for match in LANDING.finditer(text):
        token = match.group(1) or match.group(2) or ""
        if token and token not in found:
            found.append(token)
        if len(found) == 3:
            break
    return " ／ ".join(found)


def summarize(text: str, limit: int = 96) -> str:
    plain = text.replace("**", "").replace("`", "").strip()
    plain = re.sub(r"\s+", " ", plain)
    return plain if len(plain) <= limit else plain[: limit - 1] + "…"


def collect() -> list[dict]:
    items: list[dict] = []
    wave_of: dict[str, str] = {}
    for wave, _title, tasks in WAVES:
        for name in tasks:
            wave_of[name] = wave

    for directory in sorted(TASKS.iterdir()):
        if not directory.is_dir():
            continue
        checklist = task_checklist(directory)
        if checklist is None:
            continue
        name = directory.name
        group = ""
        for line in read_lines(checklist):
            if line.startswith("## "):
                group = line[3:].strip()
            match = UNCHECKED.match(line)
            if not match:
                continue
            body = match.group(1).strip()
            identifier = BOX_ID.match(body)
            items.append(
                {
                    "wave": wave_of.get(name, "WS-04"),
                    "owner": name,
                    "group": group,
                    "box": identifier.group(1) if identifier else "—",
                    "text": body,
                    "summary": summarize(body),
                    "landing": landing_points(body),
                    "source": f"tasks/{name}/{checklist.name}",
                }
            )

    for path, label, wave in ((D004, "D-004 §2 · §5", "WS-06"), (D011, "D-011 §3 · §4 · §5", "WS-07")):
        if not path.exists():
            continue
        lines = read_lines(path)
        wanted = ("## 2.", "## 5.") if path == D004 else ("## 3.", "## 4.", "## 5.")
        for start in wanted:
            group = start
            index = 0
            for line in section(lines, start):
                match = UNCHECKED.match(line)
                if not match:
                    continue
                index += 1
                body = match.group(1).strip()
                items.append(
                    {
                        "wave": wave,
                        "owner": label,
                        "group": group,
                        "box": f"{start[3:-1]}.{index}",
                        "text": body,
                        "summary": summarize(body),
                        "landing": landing_points(body),
                        "source": path.name,
                    }
                )
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=P01 / "docs" / "D-266-附件-未完成任务清单与分解编号_20261128.md",
    )
    args = parser.parse_args()

    items = collect()
    counters: dict[str, int] = {}
    lines: list[str] = []
    lines.append("# D-266 附件 · 未完成任务清单与分解编号（2026-11-28）")
    lines.append("")
    lines.append("> **本附件由工具生成，请勿手改** —— `python plan/runtime_evidence/tools/build_remaining_task_roster_20261128.py`")
    lines.append("> 编号 `WS-nn-ii` ＝ **波次** `WS-nn` ＋ **波次内条目** `ii`；条目的**权威身份**仍是它所属的框（`归属 / 框号`）。")
    lines.append("> 口径与 `D-014` §1.3 一致：**只登记未勾选的框**（`- [ ]`），已勾选的条目不在本清单内。")
    lines.append("")

    titles = {wave: title for wave, title, _ in WAVES}
    by_wave: dict[str, list[dict]] = {}
    for item in items:
        by_wave.setdefault(item["wave"], []).append(item)

    lines.append("## 波次汇总")
    lines.append("")
    lines.append("| 波次 | 主题 | 条目数 | 归属 |")
    lines.append("|---|---|---|---|")
    for wave, _title, _tasks in WAVES:
        members = by_wave.get(wave, [])
        owners = sorted({member["owner"] for member in members})
        lines.append(
            "| `%s` | %s | **%d** | %s |"
            % (wave, titles[wave], len(members), " ／ ".join(owners) if owners else "—")
        )
    lines.append("")
    lines.append("**合计 %d 项**（二级 **%d** ＋ 一级 **%d** ＋ 横切 **%d**）"
                 % (len(items), len(by_wave.get("WS-01", [])) + len(by_wave.get("WS-02", []))
                    + len(by_wave.get("WS-03", [])) + len(by_wave.get("WS-04", []))
                    + len(by_wave.get("WS-05", [])),
                    len(by_wave.get("WS-06", [])), len(by_wave.get("WS-07", []))))
    lines.append("")

    for wave, _title, _tasks in WAVES:
        members = by_wave.get(wave, [])
        if not members:
            continue
        lines.append(f"## {wave} · {titles[wave]}")
        lines.append("")
        lines.append("| 分解编号 | 归属 | 框号 | 组 | 摘要 | 落点（框里点名的文件／测试） |")
        lines.append("|---|---|---|---|---|---|")
        for item in members:
            counters[item["wave"]] = counters.get(item["wave"], 0) + 1
            index = counters[item["wave"]]
            lines.append(
                "| `%s-%02d` | `%s` | **%s** | %s | %s | %s |"
                % (
                    item["wave"],
                    index,
                    item["owner"],
                    item["box"],
                    item["group"][:28],
                    item["summary"].replace("|", "／"),
                    item["landing"].replace("|", "／") or "—",
                )
            )
        lines.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("items=%d" % len(items))
    for wave, _title, _tasks in WAVES:
        print("%-6s %4d" % (wave, len(by_wave.get(wave, []))))
    print("-> %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
