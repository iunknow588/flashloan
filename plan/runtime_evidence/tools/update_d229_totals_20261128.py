"""Bring D-238's totals in line with the ledger after a new task appeared.

``T-复编储.01`` (``D-234``..``D-237``) was created **during** this round,
so the ledger grew while the analysis was being written.  The counts are
recomputed by the counting tools and only then written into the document.
"""

from pathlib import Path

TARGET = Path("plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-238-任务完成度分析_20261128.md")

REPLACEMENTS: list[tuple[str, str]] = [
    (
        "## 1. 总账：**108** 项未闭合",
        "## 1. 总账：**116** 项未闭合",
    ),
    (
        "| 二级任务（`tasks/*/`*-checklist.md`） | **49** | 16 个二级任务目录 |",
        "| 二级任务（`tasks/*/`*-checklist.md`） | **57** | **17** 个二级任务目录（本轮**新增** `T-复编储.01`） |",
    ),
    (
        "| **合计** | **108** | 与上一轮持平（勾了 6 框、补了 1 份新清单） |",
        "| **合计** | **116** | 上一轮 **117** ⇒ 本轮**勾掉 6 框**、**新增** `T-复编储.01` 的 **8** 项 |",
    ),
    (
        "| **B** | **43** | **现在就能做**（工作量／读数／静态不变量） |",
        "| **B** | **50** | **现在就能做**（工作量／读数／静态不变量） |",
    ),
    (
        "| **C** | **14** | **用户口径保留** ／ **刻意留占位** |",
        "| **C** | **15** | **用户口径保留** ／ **刻意留占位** |",
    ),
    (
        "| 合计 | **108** | `unclassified = 0` |",
        "| 合计 | **116** | `unclassified = 0` |",
    ),
    (
        "⇒ **真正「欠实现」的只有 B 类的 43 项**；A 类 30 项不是缺陷，C 类 14 项是口径，\r\n"
        "D 类 21 项是收口仪式。",
        "⇒ **真正「欠实现」的只有 B 类的 50 项**；A 类 30 项不是缺陷，C 类 15 项是口径，\r\n"
        "D 类 21 项是收口仪式。\r\n"
        "\r\n"
        "> **本轮的新增项**：`T-复编储.01`（`D-234` 计划 ／ `D-235` 范围守卫 ／ `D-236` 验收清单 ／\r\n"
        "> `D-237` 根因）在本轮**进行中**新建 —— 它的 **8** 项全部进 B（7）与 C（1）：\r\n"
        "> B1–B4 是**代码项**（复核行退役判定 ＋ 迁移 `20261128_022` ＋ 统计与窗口口径分离），\r\n"
        "> C1–C3 是**回归／部署／实盘读数**；D1「目录外的复核填数即伪造」是**永久留占位**。",
    ),
    (
        "| `T-编.03`（D-231） | **3** | 0 | A 2（E1 ＋ E3）＋ B 1（E2 复核盈亏数值） |",
        "| `T-编.03`（D-231） | **3** | 0 | A 2（E1 ＋ E3）＋ B 1（E2 复核盈亏数值） |\r\n"
        "| `T-复编储.01`（D-236） | **8** | 0 | B 7（退役判定的代码项 ＋ 回归／部署／读数）＋ C 1（D1 永久留占位） |",
    ),
    (
        "## 4. B 类（**43** 项）：现在就能做",
        "## 4. B 类（**50** 项）：现在就能做",
    ),
    (
        "### 4.1 B1 · 现在就能**做**（**14** 项）",
        "### 4.1 B1 · 现在就能**做**（**18** 项）",
    ),
    (
        "### 4.2 B2 · 现在就能**读数**（**6** 项 · `D-049`）",
        "### 4.2 B2 · 现在就能**读数**（**9** 项 · `D-049` ＋ `D-236`）",
    ),
    (
        "| `D-231` L66 | **E2** 每一个复核都有明确盈亏数值（当前目录已达成；目录外历史闭环归 `M-6`） |",
        "| `D-231` L66 | **E2** 每一个复核都有明确盈亏数值（当前目录已达成；目录外历史闭环归 `M-6`） |\r\n"
        "| `D-236` B1–B4 | 复核行**退役判定**（迁移 `20261128_022` ＋ `review_status='retired'` ＋ 撤下广播请求 ＋ 库状态统计分离 ＋ 编排层窗口口径分离） |",
    ),
    (
        "healer 交叉一致、**`positive=0` 如实报告为业务结论**。",
        "healer 交叉一致、**`positive=0` 如实报告为业务结论**；\r\n"
        "以及 `D-236` 的 **C1–C3**：改动模块的**回归全绿**、**迁移 022 与新二进制同批上线**、\r\n"
        "**退役行 19 条** ＋ 窗口 `missingValue` **19 → 0** ＋ 在产目录 **8 / 8** 仍全有明确盈亏。",
    ),
    (
        "## 5. C 类（**14** 项）：用户口径保留 ／ 刻意留占位",
        "## 5. C 类（**15** 项）：用户口径保留 ／ 刻意留占位",
    ),
    (
        "| `D-018` / `D-096` / `D-216` | 3 | 测试网 ⏸（两处）＋ 保留期 **30 天**实盘未触发 |",
        "| `D-018` / `D-096` / `D-216` | 3 | 测试网 ⏸（两处）＋ 保留期 **30 天**实盘未触发 |\r\n"
        "| `D-236` D1 | 1 | 「每一个复核都有明确的盈亏数值」在**目录外不可达成**（那 19 条的跳池已从市场目录消失，**0 / 19** 可复价）⇒ **填数即伪造** ⇒ 只能给出**明确的退役结论**；**本框不代为关闭** |",
    ),
    (
        "> 明细：`plan/runtime_evidence/diagnostics/open_items_20261128.json`（**117** 条导出）、\r\n"
        "> `open_items_classified_20261128.json`（**108** 条归类，`unclassified = 0`）。",
        "> 明细：`plan/runtime_evidence/diagnostics/open_items_20261128.json`（**116** 条导出）、\r\n"
        "> `open_items_classified_20261128.json`（**116** 条归类，`unclassified = 0`）。",
    ),
]


def main() -> None:
    with TARGET.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    for old, new in REPLACEMENTS:
        if text.count(old) != 1:
            raise SystemExit(f"expected exactly one match, got {text.count(old)}: {old[:40]}")
        text = text.replace(old, new)
    with TARGET.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    print(f"updated {len(REPLACEMENTS)} places")


if __name__ == "__main__":
    main()
