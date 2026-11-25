"""Rewrite D-004 with the readings measured on 2026-11-25."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpatch

NL = chr(10)
ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "plan" / "tasks" / "P-01-池子重构" / "D-004-checklist.md"

BODY = "# D-004 P-01-池子重构`｜验收清单（**一级特有项**）\n\n> **本文件只放一级特有的验收项** —— 即**跨二级**的一致性、整体读数与整体回归。\n> **各二级任务自己的验收项**在 `tasks/<T-nn>/checklist.md` 里，**各自独立**。\n> 每条勾选**必须**有实测读数支撑。\n\n---\n\n## 1. 分解完整性\n\n- [x] §2.1 二级任务分解表覆盖**全部**已识别改动点（**无遗漏、无重复**）\n\n      > **读数（2026-11-25）**：`python plan/runtime_evidence/tools/check_decomposition_table_20261125.py`\n      > ⇒ item1 **无遗漏 / 无多余**，分解表 **22** 行（`T-方授储.01` … `T-池复.01`）。\n\n- [x] 每个二级任务的**改动模块集合**互不矛盾（同一模块的改动归属**唯一**）\n\n      > **读数（2026-11-25）**：同上脚本 item2 ⇒ **冲突 none** ／ 模块号越界 **none**\n      > （逐任务比对 `D-002` §2.1 与 `D-001` §4.2.1 的模块集合，**22/22 一致**）。\n\n- [x] 每个二级任务有**自己的**目录与**四部分固定文件**\n\n      > **读数（2026-11-25）**：`python plan/runtime_evidence/tools/check_task_dir_structure_20261125.py`\n      > item1 ⇒ **PASS**（在盘 **8** 个任务目录，均含 `PLAN.md` / `SCOPE_GUARD.md` / `checklist.md` / `README.md`）。\n\n- [x] 每个二级任务有**自己的**回归命令（**可逐条复制执行**）\n\n      > **读数（2026-11-25）**：同上脚本 item2 ⇒ **PASS**。\n\n- [x] 每个二级任务的 `checklist.md` **不引用**其它二级的验收内容\n\n      > **读数（2026-11-25）**：同上脚本 item3 ⇒ **PASS**。\n      > 残留的**任务号级引用**只出现在「前置说明」里（不引用他任务的验收项）；\n      > 口径提示见 `D-014` §0.1 R-2 —— 前置宜写**模块号**而非任务号。\n\n---\n\n## 2. 跨二级一致性（**全部二级闭合后**才可验）\n\n- [ ] 方向层与闭环层的**方向键口径一致**（无第二套解释）\n- [ ] 广播层消费的字段**全部**由复核层产出（无隐式补算）\n- [ ] 存储层**只有一个**写入方（写入路径收口成立）\n- [ ] 授权维度**无复用**（每个写端点有**自己**的授权维度）\n\n> **本节保持未勾（2026-11-25 裁定）**：在盘还有 **8** 个二级任务未闭合 ⇒\n> 「全部二级闭合」的前置**不成立**，勾掉就是假的。\n> 部分证据已具备：闭环 **522** 跳全部指向方向池、方向键口径单一（`D-136` §3.2）；\n> 但**广播层 / 存储层 / 授权维度**三项须等在盘任务闭合后才可整体验。\n\n---\n\n## 3. 整体回归（**一次**，见 `SCOPE_GUARD.md` §2）\n\n- [x] `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` 全绿\n\n      > **读数（2026-11-25 11:38）**：**80 passed / 0 failed**（120.70 s）\n      > ⇒ `plan/runtime_evidence/py_arch_guards_only_20261125.log`。\n      > 同跑记录层守卫 `test_record_layer_guards.py` ⇒ **90 passed / 0 failed**（125.61 s）\n      > ⇒ `plan/runtime_evidence/py_arch_guards_20261125b.log`。\n\n- [x] `cargo build --offline --workspace` 成功\n\n      > **读数（2026-11-25 11:38）**：**exit 0** ——\n      > `Finished 'dev' profile [unoptimized + debuginfo] target(s) in 3m 54s`\n      > ⇒ `plan/runtime_evidence/tools_build_20261125c.log`。\n      > **注**：本轮早前一次构建因**并行施工中**的 `candidate_exchange_pool_manager` 缺构造字段而红；\n      > 该改动已由**并行进程**补齐 ⇒ **红例不属本轮改动**，已复核。\n\n- [x] 组合后**无新增**架构守卫失败项\n\n      > **读数（2026-11-25）**：守卫失败项 **0**（基线 **80** ／ 记录层 **90**，全绿），\n      > 与改动前基线**持平**（只增不减）；Rust 侧\n      > `cargo test --offline -p pool-manager -p review-manager` ⇒ **167 passed** ＋ **161 passed**，\n      > `rs-src` ⇒ **220 passed**\n      > ⇒ `plan/runtime_evidence/tools_m1m6_test_20261125.log`。\n\n---\n\n## 4. 整体读数（**实测 · 可复算**）\n\n| 项 | 读数 | 来源 | 备注 |\n|---|---|---|---|\n| 二级任务闭合数 | **14 / 22** | `plan/tasks/P-01-池子重构/tasks/` ＋ `plan/archive/README.md` §1 | 在盘 **8** 个未闭合；已归档 **14** 个 `T-*`（另 **6** 个历史 `TASK-*` **不在**登记的 22 内） |\n| 未完成验收项总数 | **99** | 逐目录实测（**36** ＋ **8** ＋ **55**） | 本轮开始 **107**，本轮闭合 **8** |\n| 失效章节引用 | **0** | `check_markdown_links_20261125.py` | checked **298** / broken **0**（本轮修掉 **4** 条） |\n| 架构守卫失败项 | **0** | `pytest .../test_architecture_guards.py` | 基线 **80** ／ 记录层 **90**，全绿 |\n| 分解表覆盖 | **22 / 22** | `check_decomposition_table_20261125.py` | 无遗漏 / 无多余 / 冲突 none |\n| 任务目录结构 | **PASS** | `check_task_dir_structure_20261125.py` | item1 / item2 / item3 全 PASS |\n| 实盘复核代次 | **115**（停在 **10:35:31 +08**） | `/v1/pipeline/status?chainId=4663` | 发布被 `candidate_pool_key_missing` 挡住 ⇒ 见 `D-136` §3.3 |\n\n---\n\n## 5. 归档前确认\n\n- [ ] **全部**二级任务**已闭合并归档**（`tasks/` 目录**为空**）\n- [ ] 本文件**全部** `- [ ]` 已变为 `- [x]`\n- [ ] `SCOPE_GUARD.md` §2 总回归**全绿**\n- [ ] 已登记到 `plan/archive/README.md` §1\n\n> **本节保持未勾（2026-11-25 裁定）**：\n> ① 在盘 **8** 个二级任务 ⇒ `tasks/` **非空**；\n> ② §2 **4** 项 ＋ §5 前三项**依赖**①；\n> ③ 本文件 §3 总回归**已全绿**（`cargo build` exit 0 ＋ 守卫 80/90 全绿），\n>    但「`SCOPE_GUARD.md` §2 总回归」**以全部二级闭合后的最终组合为准** ⇒ 最后一轮再勾。\n"


def main() -> None:
    mdpatch.write(DOC, BODY.replace(NL, NL))
    print("update_d004: ok")


if __name__ == "__main__":
    main()

