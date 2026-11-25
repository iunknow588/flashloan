

"""Add the D-014 v3.1 status classification and refresh the reconciliation block."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpatch

NL = chr(10)

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "plan" / "tasks" / "P-01-池子重构" / "docs" / "D-014-未完成任务清单_20261123.md"


RECON = [
    "**对账**（2026-11-25 11:45 +08 本轮重测，**含 5 个已归档任务归零 ＋ `T-池复.01` 入册 ＋ 本轮闭合 `D-004` 8 项**）：",
    "二级任务归属 **36** ＋ 一级特有 **8** ＋ §1.2.9 指针 **0** ＋ 横切 **55** = **99** ✔",
    "",
    "> **二级任务归属 36** = **8** 个在盘任务目录逐目录数 `checklist.md` 的 `- [ ]` 之和",
    "> （`T-池复.01` **3** ＋ `T-方储.01` **5** ＋ `T-储.02` **6** ＋ `T-复广.01` **7** ＋",
    "> `T-复编.02` **7** ＋ `T-编.01` **3** ＋ `T-候.01` **1** ＋ `T-候编储.01` **4** = **36**）。",
    "> **与 §1.2 逐行合计相等（36 = 36）** —— 这是本轮对该列的**口径订正**：",
    "> v3.0 的 **75** 与其逐行合计 **48** **本就不相等**（差 **27**），本轮统一为**唯一口径**。",
    ">",
    "> **本轮增量（逐条可复算）**：",
    "> ① **归档归零 −14**：`T-方.01`(1) / `T-闭.01`(1) / `T-证候储.01`(2) / `T-复.02`(6) / `T-广.01`(4)",
    "> —— 归档件内对应条目**均已勾选** ⇒ 属**旧读数订正**，不是新增闭合；",
    "> ② **`T-复编.02` 8 → 7**（复核代次读数刷新）；",
    "> ③ **新增 `T-池复.01` ＋3**（代码已落地，待实盘复测）；",
    "> ④ **一级特有 16 → 8**（本轮闭合 `D-004` §1 **5** 项 ＋ §3 **3** 项，见 `D-136` §1.1）；",
    "> ⑤ **文档断链 4 → 0**（`check_markdown_links_20261125.py` ⇒ checked 298 / broken 0）。",
    ">",
    "> **复算命令**：",
    "> - 逐任务：遍历 `plan/tasks/P-01-池子重构/tasks/*/D-0*-checklist.md` 数 `- [ ]`；",
    "> - 横切：`plan/tasks/P-01-池子重构/docs/D-011-check_list_20261123.md` 的 §3 / §4 / §5；",
    "> - 一级：`plan/tasks/P-01-池子重构/D-004-checklist.md`；",
    "> - 结构：`python plan/runtime_evidence/tools/check_task_dir_structure_20261125.py`；",
    "> - 分解表：`python plan/runtime_evidence/tools/check_decomposition_table_20261125.py`；",
    "> - 链接：`python plan/runtime_evidence/tools/check_markdown_links_20261125.py`；",
    "> - 旧口径（单文件时代）：`python plan/runtime_evidence/tools/survey_open_tasks_20261123.py`。",
]


CLASS_BLOCK = [
    "### 1.4 剩余任务的状态分类（**2026-11-25 裁定**）",
    "",
    "> **本节为本轮新增** —— 把 §1.2 的 **36** 项按「**能不能现在做完**」分四类，",
    "> 避免把「等市场条件」「等别人施工」「已裁定后置」混在一起当待办。",
    "> 完整判读、解锁条件与动作序列见",
    "> [`D-136`](./D-136-剩余任务清单总结与分类裁定_20261125.md)。",
    "",
    "| 类 | 名称 | 项数 | 判据 | 本轮处置 |",
    "|---|---|---:|---|---|",
    "| **B** | **待做**（无外部阻塞） | **5** | 依赖项**全部**已满足，缺的只是执行 | `T-方储.01` 阶段 2（施工清单 `D-131` 已就绪） |",
    "| **C** | **阻塞**（外部条件） | **25** | 缺的是**实盘代次 / 市场 / 测试网** | 登记阻塞源 ＋ 解锁条件，**不代为勾框** |",
    "| **D** | **裁定后置**（使用者已裁定） | **6** | 使用者明确「先留占位、最后再实现」 | `T-储.02` 故障注入 `F-1`…`F-10` ⇒ **不计入待办** |",
    "| **E** | **常设守卫**（不是待办） | **63** | 横切 **55** ＋ 一级 §2 **4** ＋ §5 **4** | 随主线推进，**不是剩余工作量** |",
    "",
    "> **B + C + D + E = 5 + 25 + 6 + 63 = 99** ✔ 与 §1 总读数（本轮闭合后）自洽。",
    "",
    "**C 类的共同阻塞源（本轮新发现 · 最高优先级）**",
    "",
    "> 复核代次**停在 `review_version = 115`**（生成于 2026-11-25 10:35:31 +08）⇒ 至 11:45 已 **70 分钟**无新代次。",
    "> 运行时自 **11:00:50** 起持续报 `pool_manager_refresh_required` / `refreshCause = candidate_pool_key_missing`（至 11:38 共 **764** 条）",
    "> ⇒ `T-池复.01`(**3**) / `T-复编.02`(**7**) / `T-编.01`(**3**) 共 **13** 项同时被它挡住。",
    "> 根因证据与作用域口径分歧见",
    "> [`D-136`](./D-136-剩余任务清单总结与分类裁定_20261125.md) §3.3。",
]


def main() -> None:
    text = mdpatch.read(DOC)
    lines = text.split(NL)

    start = None
    end = None
    for i, line in enumerate(lines):
        if start is None and line.startswith("**对账**"):
            start = i
        if start is not None and "**复算命令不变**" in line:
            end = i
            break
    if start is None or end is None:
        raise SystemExit("update_d014_class: reconciliation block not found")
    lines[start:end + 1] = RECON
    text = NL.join(lines)

    text = mdpatch.insert_before(
        text,
        "## 2. 模块级执行顺序",
        NL.join(CLASS_BLOCK) + NL,
        "class block",
    )

    mdpatch.write(DOC, text)
    print("update_d014_class: ok")


if __name__ == "__main__":
    main()
