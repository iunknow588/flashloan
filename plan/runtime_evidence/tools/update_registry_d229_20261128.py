"""Register the completion analysis and the write-lock fix in the ledger.

The ledger is written with CRLF line endings, so the row is appended by
script.  The row states what changed, not what it is meant to feel like.
"""

from pathlib import Path

TARGET = Path("plan/D-001-TASK_REGISTRY.md")

ANCHOR = "| 2026-11-28 | **活价重算 ＋ E1 根因 ＋ 状态语义修复（`T-编.02` 第八轮"

ROW = (
    "| 2026-11-28 | **任务完成度分析 ＋ 按判据勾框 ＋ 写锁争用修复（`T-编.02` 第九轮 · "
    "`M-6` ＋ `M-7` ＋ `M-13`）**：**用户口径**「**请先分析任务是否都以及完成，还有哪些任务尚未实现**」"
    "＋「**请继续。**」**★ 完成度分析（`D-229`）**：未闭合 **108** 项，按**靠什么闭合**分四类 —— "
    "**A 30**（只等一次真实链上广播）／**B 43**（现在就能做）／**C 14**（用户口径保留／刻意留占位）／"
    "**D 21**（归档收口判据），`unclassified = 0`；**真正欠实现的是 B 类的 43 项**。"
    "**★ 按判据勾掉 6 框**（`T-广.03` L265/L266/L267/L346/L361/L367）：每个框都按**它自己的措辞**核对读数后才勾，"
    "其中 L361/L367 的旧注记「触发条件未出现、不能据此说归零」**先被读数推翻再勾** —— "
    "`F-3` 之后**真的走到过**闸门的那条诊断（带 `amountInRaw`）把理由判成 `hop_2_failed:tick_range_changed`，"
    "⇒ 两个零**不是空档**。**★ 新诊断：写锁争用**：`runtime_stderr.log` 里 "
    "`closed-loop runtime state sync failed: database is locked` **50** 次，**散布**在 24 小时里（每次孤立出现，"
    "**不是**长时间持锁）；根因是写连接忙等待只有 **15 s**、重试预算 **5 × 15 s ≈ 75 s**，"
    "而 20 GB 的 WAL 库做检查点时写锁被占住 ⇒ **瞬时竞争**就把这一轮状态迁移**丢掉**；"
    "Rust 侧 `schema.rs::open_rw` **根本没有**设忙等待（SQLite 默认 **0**）⇒ 迁移第一次撞锁就失败。"
    "**★ 修法**：Python 写事务改用 **`BEGIN IMMEDIATE`**（`storage_business_io.py`）＋ 写连接忙等待 **60 s**／重试 **3** 次；"
    "Rust 写连接补 **`busy_timeout` 60 s**（`schema.rs::open_rw`）＋ `migration.rs` 的 5 s → 60 s。"
    "**一格门禁都没有动。** 文档：`plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-229-任务完成度分析_20261128.md` |\r\n"
)


def main() -> None:
    with TARGET.open("r", encoding="utf-8", newline="") as handle:
        lines = handle.read().split("\r\n")
    index = next(
        (number for number, line in enumerate(lines) if line.startswith(ANCHOR)),
        None,
    )
    if index is None:
        raise SystemExit("the anchor row was not found")
    if any(line.startswith("| 2026-11-28 | **任务完成度分析") for line in lines):
        print("the row is already registered")
        return
    lines.insert(index + 1, ROW.rstrip("\r\n"))
    with TARGET.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\r\n".join(lines))
    print("registered the change history row")


if __name__ == "__main__":
    main()
