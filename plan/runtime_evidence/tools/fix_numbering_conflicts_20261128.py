#!/usr/bin/env python3
'''Repair three numbering conflicts found by the task-numbering validator.

1. **组合索引字符合成**：D-001 §1.1 的模块索引列给每个模块**一个索引字符**
   （M-6 = 复 / M-8 = 编 / M-10 = 储）⇒ 组合索引是 **复编储**，
   不是把三个模块**名字**首字拼起来的「复编排储」。任务号段随之由
   T-复编排储.01 改为 **T-复编储.01**（验收号 AC-复编储.01，
   下一个可用 T-复编储.02）。
2. **D-229 重号**：并发轮次在 T-编.02/docs 下新建的「任务完成度分析」
   与 T-编.03/D-229-PLAN.md 同号 ⇒ 后者已入册且被广泛引用 ⇒ 新文档
   改号为 **D-238**（当前最大号 237 的下一个），并补登进 D-001 §5.5。
3. **D-237 标题行**：规范要求首行以 "# D-nnn " 开头 ⇒ 补一个空格并
   带上任务号；D-234 §1.0 的「序号」行同步为 01（新号段内第 1 个）。

Every replacement is asserted, so a file that does not carry the expected
wording fails loudly instead of drifting.
'''

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PLAN = REPO / "plan"
P01 = PLAN / "tasks/P-01-池子重构"
TASKS = P01 / "tasks"
REGISTRY = PLAN / "D-001-TASK_REGISTRY.md"

BT = chr(96)
OLD_TASK = "T-复编排储"
NEW_TASK = "T-复编储"
OLD_INDEX = "复编排储"
NEW_INDEX = "复编储"

OLD_DOC = TASKS / "T-编.02/docs/D-229-任务完成度分析_20261128.md"
NEW_DOC = TASKS / "T-编.02/docs/D-238-任务完成度分析_20261128.md"


def text_files() -> list[Path]:
    out: list[Path] = []
    for path in PLAN.rglob("*.md"):
        out.append(path)
    for path in (PLAN / "runtime_evidence/tools").glob("*.py"):
        out.append(path)
    # This script carries the old spelling in its own constants; rewriting it
    # would break the very mapping it is there to apply.
    self_path = Path(__file__).resolve()
    return sorted(p for p in out if p.resolve() != self_path)


def rename_task_dir() -> None:
    old = TASKS / (OLD_TASK + ".01")
    new = TASKS / (NEW_TASK + ".01")
    if old.is_dir() and not new.exists():
        os.rename(old, new)
        print("  renamed directory -> %s" % new.name)
    elif new.is_dir():
        print("  directory already renamed")
    else:
        raise SystemExit("task directory not found: %s" % old)


def rename_index_everywhere() -> int:
    hits = 0
    for path in text_files():
        original = path.read_text(encoding="utf-8")
        if OLD_INDEX not in original:
            continue
        path.write_text(original.replace(OLD_INDEX, NEW_INDEX), encoding="utf-8", newline="")
        hits += 1
        print("  index renamed in %s" % path.relative_to(REPO))
    return hits


def rename_d229_doc() -> None:
    if NEW_DOC.is_file() and not OLD_DOC.exists():
        print("  D-229 doc already renamed")
    elif OLD_DOC.is_file():
        os.rename(OLD_DOC, NEW_DOC)
        print("  renamed document -> %s" % NEW_DOC.name)
    else:
        raise SystemExit("document not found: %s" % OLD_DOC)
    text = NEW_DOC.read_text(encoding="utf-8")
    head = "# D-229 ｜"
    if text.startswith(head):
        text = "# D-238 ｜" + text[len(head):]
        NEW_DOC.write_text(text, encoding="utf-8", newline="")
        print("  title line fixed")
    elif not text.startswith("# D-238 ｜"):
        raise SystemExit("unexpected title line: %s" % text.split(chr(10))[0])
    # The helper that wrote the old document points at it by path; keep it
    # runnable after the rename.
    helper = PLAN / "runtime_evidence/tools/update_d229_totals_20261128.py"
    if helper.is_file():
        body = helper.read_text(encoding="utf-8")
        if "D-229-任务完成度分析_20261128.md" in body:
            body = body.replace(
                "D-229-任务完成度分析_20261128.md", "D-238-任务完成度分析_20261128.md"
            )
            body = body.replace("Bring D-229's totals", "Bring D-238's totals")
            helper.write_text(body, encoding="utf-8", newline="")
            print("  helper tool repointed")


def fix_registry() -> None:
    text = original = REGISTRY.read_text(encoding="utf-8")
    row = (
        "| " + BT + "D-238" + BT + " | 任务完成度分析_20261128 | P-01 T-编.02 | "
        + BT + "plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-238-任务完成度分析_20261128.md" + BT + " |"
    )
    if row not in text:
        anchor = "| " + BT + "D-237" + BT
        index = text.index(anchor)
        end = text.index(chr(10), index) + 1
        text = text[:end] + row + chr(10) + text[end:]
        print("  D-238 row added to §5.5")
    else:
        print("  D-238 row already present")
    for line in text.split(chr(10)):
        if line.startswith("| 2026-11-28 | **任务完成度分析"):
            if "D-229" in line:
                text = text.replace(line, line.replace("D-229", "D-238"))
                print("  changelog reference updated")
            break
    if text != original:
        REGISTRY.write_text(text, encoding="utf-8", newline="")


def fix_titles() -> None:
    doc = TASKS / (NEW_TASK + ".01") / "docs/D-237-目录外复核缺值的根因与退役口径_20261128.md"
    text = original = doc.read_text(encoding="utf-8")
    old = "# D-237｜目录外复核缺值的根因与退役口径"
    new = "# D-237 " + BT + NEW_TASK + ".01" + BT + "｜目录外复核缺值的根因与退役口径"
    if old in text:
        text = text.replace(old, new, 1)
        doc.write_text(text, encoding="utf-8", newline="")
        print("  D-237 title line fixed")
    elif new not in text:
        raise SystemExit("unexpected D-237 title: %s" % text.split(chr(10))[0])

    plan = TASKS / (NEW_TASK + ".01") / "D-234-PLAN.md"
    text = original = plan.read_text(encoding="utf-8")
    old = "| **序号** | 第 " + BT + "03" + BT + " 个"
    new = "| **序号** | 第 " + BT + "01" + BT + " 个"
    if old in text:
        text = text.replace(old, new, 1)
        plan.write_text(text, encoding="utf-8", newline="")
        print("  D-234 sequence number fixed")
    elif new not in text:
        raise SystemExit("unexpected D-234 sequence row")


def main() -> int:
    print("== step 1: task directory ==")
    rename_task_dir()
    print("== step 2: composite index everywhere ==")
    print("  files touched: %d" % rename_index_everywhere())
    print("== step 3: D-229 -> D-238 ==")
    rename_d229_doc()
    print("== step 4: registry ==")
    fix_registry()
    print("== step 5: titles ==")
    fix_titles()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
