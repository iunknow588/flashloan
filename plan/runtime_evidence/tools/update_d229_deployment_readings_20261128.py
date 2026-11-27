"""Add the deployment readings of the write-lock fix to D-229.

The document is written with CRLF line endings, so the section is inserted by
script.  The insertion point is the last section of §7, and the text states
the measured numbers only.
"""

from pathlib import Path

TARGET = Path("plan/tasks/P-01-池子重构/tasks/T-编.02/docs/D-229-任务完成度分析_20261128.md")

ANCHOR = "## 8. 本轮勾掉的框（**6** 个）"

SECTION = """### 7.6 写锁修复的**部署读数**

| 读数 | 值 |
|---|---|
| 新 DLL | `flashloan/rs-src/target/release/flashloan_rs.dll` **18 674 688 B**，构建 **04:29:09**（旧 **18 667 008 B**） |
| 编排器 | PID **25452**（`--mode supervise --apply --interval 60 --pnl-interval-seconds 300 --observation-hours 6 --leave-runtime-running`） |
| 运行时 | PID **19592**，`/healthz` ＝ `{"status":"ok"}`，就绪 **04:30:23Z** |
| 新运行时 stderr 的 `database is locked` | **0**（旧实例 **24** 小时内 **50** 次） |
| 新运行时 stderr 的 `closed-loop runtime state sync failed` | **0** |
| Rust 侧 `direction migration SQL failed` | **0** |
| `flashloan-rs --lib` | **281 passed / 0 failed**（与基线持平） |
| `scripts/tests/test_mainnet_arbitrage_healer.py` | **121 passed / 0 failed**（与基线持平） |
| 新增回归 `test_storage_business_io_write_lock.py` | **3 passed / 0 failed**（等锁而不是当场失败 ／ 写提交 ／ 事务提交） |

> 部署顺序上有一个**必须记住的事实**：**编排器就是运行时的看护** ——
> 运行时一停，编排器立刻把它拉回来，于是 DLL 在编译期间被占用、
> 编译以「拒绝访问」失败。⇒ **换扩展的顺序必须是：先停编排器，再停运行时，
> 然后编译，最后起编排器**（它会自己把运行时带起来）。

"""


def main() -> None:
    with TARGET.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    if "### 7.6 写锁修复的**部署读数**" in text:
        print("the section is already there")
        return
    if text.count(ANCHOR) != 1:
        raise SystemExit(f"the anchor was not found exactly once: {text.count(ANCHOR)}")
    section = SECTION.replace("\n", "\r\n")
    text = text.replace(ANCHOR, section + ANCHOR)
    with TARGET.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    print("added the deployment readings")


if __name__ == "__main__":
    main()
