"""Register T-核.05 in the task registry and the two task READMEs.

The rows are inserted with the same helper the earlier rounds used
(@mdpatch@), anchored on **unique row prefixes**.  Every edit is
idempotent: a row that is already there is left alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, insert_after, insert_before, read, replace_once, replace_row, restore, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
P01 = ROOT / "plan" / "tasks" / "P-01-池子重构"
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"
README = P01 / "README.md"
DOCS_README = P01 / "docs" / "README.md"


# ---------------------------------------------------------------- registry

REG_MODULE_ROW = (
    "| **`核`** | `M-13`（运行时内核与运行时接线） | **5** | `T-核.01`（已闭合）、"
    "`T-核.02`（另一线程，只监测）、**`T-核.03`**（代码已落地 · **16 / 16** · 部署闸门待办）、"
    "**`T-核.04`**（代码已落地 · **部署闸门读数待取**）、**`T-核.05`**（代码已落地 · **部署闸门读数待取**） | "
    "**`T-核.06`** |"
)

REG_TASK_ROW = (
    "| `T-核.05` | 流动性维护槽的 RPC 故障转移 | **2026-11-26** | `M-13`（`rs-src/src/pool_depth.rs`）＋ "
    "`M-1`（`tools/pool_manager/src/lib.rs` ＋ `tools/pool_manager/src/main.rs`） | "
    "**代码项全部闭合，剩 1 项实盘读数待取**（部署闸门）。**根因（实测 · 只读）**："
    "`chain_business_initialization` ⇒ `v4_pool_liquidity_worker` `status = error`、"
    "`refreshed_at = 2026-11-26T00:39:55Z`、`error = \"all V4 pool state RPC endpoints failed: "
    "https://[REDACTED]: endpoint probe failed: host lookup failed: 不知道这样的主机。 (os error 11001)\"`；"
    "**错误串里只有一段端点信息** ⇒ 清单长度 = **1**（该函数用 `; ` 串每个失败端点）⇒ "
    "**不是没有可用节点，是没有去试**（同期端点实测 **活 2 / 5**：publicnode **200** ／ QuickNode **200** ／ "
    "`chain.robinhood` **403** ／ ordofi **DNS 死** ／ Alchemy **429**）。**代码根因**："
    "`robinhood_v4_idle_maintenance.py:72` `_robinhood_rpc_url()` → `v4_pool_rpc.py:23` "
    "`_rpc_url_from_env_or_default()`（`return urls[0]`）→ `core/rs_bridge.py:230` → "
    "`rs-src/src/pool_depth.rs:58`（`DepthOptions.rpc_url = Some(url)`）→ "
    "`pool_manager/src/lib.rs` `refresh_depth()`（`Some(url) => vec![url]`）⇒ **清单 = 1** ⇒ "
    "`read_v4_pool_states_with_fallback()`（**机制早已就绪**）**无转移可做**。"
    "**同源缺陷**：`refresh_depth()` 的兜底 `chain::rpc_urls_for(chain_id)` 内部读的是**主网**环境变量组 ⇒ "
    "**测试网会量主网的节点**。**落地**：`pool_manager::DepthOptions` **新增** `rpc_urls`（有序清单）＋ "
    "**新增**纯函数 `resolve_depth_rpc_urls()`（优先级 `rpc_urls` ＞ `rpc_url` ＞ "
    "`chain::rpc_urls_for_chain(chain_id)`，**去重保序**）；`refresh_depth()` 改用之 ⇒ 兜底**按链**取端点；"
    "`rs-src/src/pool_depth.rs` **新增**纯函数 `liquidity_rpc_urls()`（**调用方命名的端点在前 ＋ 链上端点在后**）。"
    "**口径**：用户「**广播与复核都在 Rust 侧，Python 是遗留旧代码**」⇒ 端点组装**全在 Rust**、**Python 零改动**"
    "（遗留 Python 取 `urls[0]` 的行为**由 Rust 侧吸收**）；「**要么成功要么失败，没有降级**」⇒ **未新增**任何兜底分支、"
    "全部端点失败**照旧上抛**；「**异常路径先留占位**」⇒ 重试次数 / 退避 / 超时预算 / 广播门禁 / 利润口径**一字未改**。"
    "**回归**：`pool-manager` **172 passed / 0 failed**（基线 **169** ＋ 新 **3**）、`flashloan-rs` "
    "**248 passed / 0 failed**（基线 **245** ＋ 新 **3**）。**部署闸门（已完成 3 / 4）**：① 旧 DLL 备份 "
    "`flashloan_rs.dll.bak-20261126-he04` = **18,135,040 B**（2026-11-26 08:09:06）；② "
    "`cargo build --offline --release` ⇒ 新 `flashloan_rs.dll` = **%%BUILD_SIZE%% B**（**%%BUILD_TIME%%**）；"
    "③ 实盘 runtime 用新二进制重启（看护 **%%SUP_PID%%** ＋ runtime **%%RT_PID%%**，起于 **%%START_TIME%%**，端口 5000）"
    "⇒ **新旧二进制无中间态**；④ **实盘读数待取**（流动性槽实测节奏：启动 ⇒ 池流动性 **+6min**，槽间隔 **900s**）。"
    "文档：`plan/tasks/P-01-池子重构/tasks/T-核.05/`（`D-184` / `D-185` / `D-186` / `D-187`） |"
)

REG_DOC_ROWS = (
    "| `D-184` | PLAN | P-01 T-核.05 | `plan/tasks/P-01-池子重构/tasks/T-核.05/D-184-PLAN.md` |\n"
    "| `D-185` | SCOPE_GUARD | P-01 T-核.05 | `plan/tasks/P-01-池子重构/tasks/T-核.05/D-185-SCOPE_GUARD.md` |\n"
    "| `D-186` | checklist | P-01 T-核.05 | `plan/tasks/P-01-池子重构/tasks/T-核.05/D-186-checklist.md` |\n"
    "| `D-187` | 流动性维护槽的RPC故障转移_20261126 | P-01 T-核.05 | "
    "`plan/tasks/P-01-池子重构/tasks/T-核.05/docs/D-187-流动性维护槽的RPC故障转移_20261126.md` |"
)

REG_MIGRATION_ROW = (
    "| 2026-11-26 | **流动性维护槽的 RPC 故障转移（`T-核.05` · `M-13` ＋ `M-1`）**："
    "实盘 `v4_pool_liquidity_worker` 整槽 **error**（`refreshed_at = 2026-11-26T00:39:55Z`、"
    "`error = \"all V4 pool state RPC endpoints failed: … host lookup failed … (os error 11001)\"`）⇒ "
    "**错误串里只有一段端点信息** ⇒ 清单长度 = **1** ⇒ **不是没有可用节点，是没有去试**（同期 **活 2 / 5**）。"
    "**代码根因**：`_robinhood_rpc_url()` → `_rpc_url_from_env_or_default()`（`urls[0]`）→ "
    "`DepthOptions.rpc_url = Some(url)` → `refresh_depth()` 的 `vec![url]` ⇒ **清单 = 1** ⇒ "
    "`read_v4_pool_states_with_fallback()`（**机制早已就绪**）**无转移可做**；**同源缺陷**：兜底 `chain::rpc_urls_for(chain_id)` 读的是**主网**环境变量组 ⇒ **测试网会量主网节点**。"
    "**落地**：`DepthOptions` **新增** `rpc_urls`（有序清单）＋ 纯函数 `resolve_depth_rpc_urls()`；"
    "`refresh_depth()` 改用之 ⇒ 兜底**按链**取端点；`rs-src/src/pool_depth.rs` **新增**纯函数 "
    "`liquidity_rpc_urls()`（**调用方命名的端点在前 ＋ 链上端点在后**）。**口径**：**Python 零改动**（端点组装全在 Rust）；"
    "**未新增**任何兜底分支（全部端点失败**照旧上抛**）；重试 / 退避 / 超时 / 门禁 / 利润口径**一字未改**。"
    "**回归**：`pool-manager` **172**（基线 **169** ＋ **3**）、`flashloan-rs` **248**（基线 **245** ＋ **3**）。"
    "**部署闸门**：旧 DLL 备份 → release 重建 → **新旧二进制同批**（无中间态）⇒ 剩 **1** 项**实盘读数**。"
    "文档：`plan/tasks/P-01-池子重构/tasks/T-核.05/`（`D-184` / `D-185` / `D-186` / `D-187`） |"
)


# ------------------------------------------------------------------ README

README_TASK_ROW = (
    "| [`T-核.05`](./tasks/T-核.05/) | 流动性维护槽的 RPC 故障转移 | `AC-核.05` | `M-13` ＋ `M-1` | M-7 | "
    "``W-1`` | **1** | ⬜ **代码已闭合**（2026-11-26）：实盘 `v4_pool_liquidity_worker` 整槽 **error** ⇒ "
    "**错误串里只有一段端点信息** ⇒ 端点清单长度 = **1** ⇒ **不是没有可用节点，是没有去试**（同期 **活 2 / 5**）。"
    "**代码根因**：`_robinhood_rpc_url()` → `_rpc_url_from_env_or_default()`（`urls[0]`）→ "
    "`DepthOptions.rpc_url = Some(url)` → `refresh_depth()` 的 `vec![url]` ⇒ **清单 = 1** ⇒ "
    "`read_v4_pool_states_with_fallback()`（**机制早已就绪**）**无转移可做**；**同源缺陷**：兜底 "
    "`chain::rpc_urls_for(chain_id)` 读的是**主网**环境变量组 ⇒ **测试网会量主网节点**。**落地**："
    "`DepthOptions` **新增** `rpc_urls` ＋ 纯函数 `resolve_depth_rpc_urls()`；`refresh_depth()` 改用之 ⇒ "
    "兜底**按链**取端点；`rs-src/src/pool_depth.rs` **新增**纯函数 `liquidity_rpc_urls()`。**Python 零改动**；"
    "「**要么成功要么失败，没有降级**」⇒ **未新增**任何兜底分支。**回归**：`pool-manager` **172**（＋3）、"
    "`flashloan-rs` **248**（＋3）。**部署闸门已完成 3 / 4**（旧 DLL 已备份、release 已重建、runtime 已用新二进制重启 ⇒ "
    "**新旧二进制无中间态**）；剩 **1** 项＝**实盘读数** |"
)

README_COUNTS = (
    "**合计未完成验收项**：**44**（二级任务 · **逐目录实测**，2026-11-26）＋ **8**（一级特有）＋ **0**（实盘读数指针 · "
    "**4 项已闭合**）＋ **51**（横切）＝ **103**"
)

README_NOTES = (
    "> **本轮（2026-11-26 · 流动性维护槽的 RPC 故障转移 ＋ 部署批次）**：\n"
    "> ① 新登记 **1** 个二级任务（`T-核.05`）⇒ 登记二级任务 **32 → 33**、在盘 **16 → 17**；\n"
    "> ② 其 checklist **18** 框中 **17** 框已闭合、**1** 框未勾（**实盘读数**）⇒ **未勾选框读数 102 → 103**"
    "（复算见 [`D-014`](./docs/D-014-未完成任务清单_20261123.md) `1.3`）；\n"
    "> ③ **本轮新根因（`T-核.05`）**：实盘 `v4_pool_liquidity_worker` 整槽 **error**（`refreshed_at = "
    "2026-11-26T00:39:55Z`）⇒ 端点清单**只有 1 个**（错误串用 `; ` 串每个失败端点，**只有一段**）⇒ "
    "**不是没有可用节点，是没有去试**（同期 **活 2 / 5**）⇒ 同链的**两条**读链入口**口径不一致**（复核定价路径**有**故障转移、"
    "流动性刷新路径**没有**）⇒ 已按链接入端点全集（**Python 零改动**）；\n"
    "> ④ **连带效应**：流动性槽 `mode = error` ⇒ `worker_complete()` 判它**不是**完整上游 ⇒ "
    "**候选 YC/ZC 槽**（停在 **23:53:15Z**）与**方向层槽**（停在 **00:04:31Z**）**全部被闸住** ⇒ "
    "闭环层停在 **00:50:40Z**、复核层停在 **2026-11-25T23:07:30Z** ⇒ **一个节点坏了 = 整条链停更**；\n"
    "> ⑤ **部署批次**：停机 → release 重建 → **新旧二进制同批**上线（**无中间态**）⇒ 停机窗口内**没有**新旧二进制混跑；\n"
    "> ⑥ **诚实边界**：`T-核.05` 的**实盘读数尚未取到**（流动性槽间隔 **900s**、启动到首跑约 **+6min**）⇒ "
    "**不代任何其它任务关闭验收项**；`T-核.04` 的**实盘复核读数**同样待取。\n"
)


# ----------------------------------------------------------- docs README

DOCS_DOC_ROW = (
    "| [`流动性维护槽的RPC故障转移_20261126.md`](../tasks/T-核.05/docs/D-187-流动性维护槽的RPC故障转移_20261126.md) | "
    "**流动性维护槽的 RPC 故障转移**（`T-核.05`）：实盘整槽 **error** 的只读读数 ＋ "
    "**「清单长度 = 1」的读数判据**（错误串用 `; ` 串每个失败端点）＋ 端点实测（**活 2 / 5**）＋ "
    "**逐跳根因链**（`urls[0]` → `Some(url)` → `vec![url]`）＋ **跨链缺陷**（测试网会读主网节点）＋ 裁定"
    "（**Python 零改动** ／ 故障转移**不是降级**）＋ 部署闸门读数（2026-11-26） |"
)

DOCS_TASK_ROW = (
    "| [`T-核.05`](../tasks/T-核.05/) | 流动性维护槽的 RPC 故障转移 | `AC-核.05` | `M-13` ＋ `M-1` | M-7 | ``W-1`` | "
    "**1** | ⬜ 实盘 `v4_pool_liquidity_worker` 整槽 **error** ⇒ **端点清单只有 1 个** ⇒ **不是没有可用节点，"
    "是没有去试**（同期 **活 2 / 5**）⇒ 同链的**两条**读链入口**口径不一致**（复核定价路径**有**故障转移、"
    "流动性刷新路径**没有**）。**落地**：`DepthOptions` **新增** `rpc_urls` ＋ 纯函数 `resolve_depth_rpc_urls()`；"
    "`refresh_depth()` 改用之 ⇒ 兜底**按链**取端点；`rs-src/src/pool_depth.rs` **新增**纯函数 "
    "`liquidity_rpc_urls()`。**Python 零改动**；「**要么成功要么失败，没有降级**」⇒ **未新增**任何兜底分支。"
    "**回归**：`pool-manager` **172**（＋3）、`flashloan-rs` **248**（＋3）。**部署闸门已完成 3 / 4**；剩 **1** 项＝"
    "**实盘读数** |"
)


def main() -> None:
    readings = {
        "%%BUILD_SIZE%%": "18,134,528",
        "%%BUILD_TIME%%": "2026-11-26 09:13:57",
        "%%SUP_PID%%": "34548",
        "%%RT_PID%%": "29224",
        "%%START_TIME%%": "09:14:52",
    }

    def fill(text: str) -> str:
        for key, value in readings.items():
            text = text.replace(key, value)
        return text

    text = read(REGISTRY)
    if restore("| `T-核.05` | 流动性维护槽的 RPC 故障转移 |") not in text:
        text = replace_row(text, "| **`核`** |", REG_MODULE_ROW, "module row")
        text = insert_after(
            text,
            "| `T-核.04` | 复核定价路径的 RPC 故障转移 |",
            fill(REG_TASK_ROW),
            "task row",
        )
    if restore("| `D-187` | 流动性维护槽的RPC故障转移_20261126 |") not in text:
        text = insert_after(
            text,
            "| `D-183` | 复核定价路径的RPC故障转移_20261126 |",
            REG_DOC_ROWS,
            "document rows",
        )
    if "**流动性维护槽的 RPC 故障转移（`T-核.05`" not in text:
        text = insert_after(
            text,
            "| 2026-11-26 | **复核定价路径的 RPC 故障转移（`T-核.04`",
            REG_MIGRATION_ROW,
            "migration row",
        )
    write(REGISTRY, text)

    text = read(README)
    if restore("| [`T-核.05`](./tasks/T-核.05/)") not in text:
        text = insert_after(
            text,
            "| [`T-核.04`](./tasks/T-核.04/)",
            README_TASK_ROW,
            "readme task row",
        )
    text = replace_row(
        text,
        "**合计未完成验收项**",
        README_COUNTS,
        "readme counts",
    )
    if "**本轮（2026-11-26 · 流动性维护槽的 RPC 故障转移 ＋ 部署批次）**" not in text:
        text = insert_before(
            text,
            "> **本轮（2026-11-26 · Rust 侧广播记录贯通）**：",
            README_NOTES,
            "readme notes",
        )
    write(README, text)

    text = read(DOCS_README)
    if restore("[`流动性维护槽的RPC故障转移_20261126.md`]") not in text:
        text = insert_after(
            text,
            "| [`复核定价路径的RPC故障转移_20261126.md`]",
            DOCS_DOC_ROW,
            "docs doc row",
        )
    if restore("| [`T-核.05`](../tasks/T-核.05/)") not in text:
        text = insert_after(
            text,
            "| [`T-核.04`](../tasks/T-核.04/)",
            DOCS_TASK_ROW,
            "docs task row",
        )
    write(DOCS_README, text)
    print("update_registry_he05: ok")


if __name__ == "__main__":
    main()
