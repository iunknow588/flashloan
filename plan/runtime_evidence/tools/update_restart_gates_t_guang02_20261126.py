'''Record the two deployment gates the restart closed for T-广.02.

`T-广.02` had two deployment boxes that were waiting for the same window as
`T-闭.03`: migration 018 applied, and the new Rust extension live on top of
it.  Both happened.  The third box (a real broadcast attempt in the store)
is a market condition and stays open.
'''

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_restart_gates_20261126 import check_box  # noqa: E402


def main() -> int:
    check_box(
        'T-广.02/D-174-checklist.md',
        '实盘迁移 **018** 已应用',
        [
            '- [x] 实盘迁移 **018** 已应用（库现停在 `20261125_017`）',
            '',
            '      > **读数（2026-11-26 14:3x · 只读）**：`schema_migrations` ⇒',
            '      > `20261125_018_closed_loop_directional_only` **已应用**',
            '      > （`applied_at = 2026-11-25T23:43:20.599Z`）⇒ **实盘库已过 018**。',
            '      > 框目标题里的「库现停在 `20261125_017`」**已过时**（那是本框留空时的读数）。',
            '      > 探针：`plan/runtime_evidence/tools/verify_restart_gates_20261126.py`。',
        ],
    )

    check_box(
        'T-广.02/D-174-checklist.md',
        '新 Rust 扩展与迁移 **018** **同批**上线',
        [
            '- [x] 新 Rust 扩展与迁移 **018** **同批**上线',
            '',
            '      > **读数（2026-11-26 14:3x）**：新扩展 `flashloan/rs-src/target/release/flashloan_rs.dll`',
            '      > = **18,379,264 B**（构建于 **13:59:08**）已上线；',
            '      > 实盘 runtime **pid 26328**（看护 **26980**）自 **13:59:28** 起跑新二进制、端口 **5000** 在听',
            '      > ⇒ **新旧二进制无中间态**。',
            '      > **时序**：迁移 **018** 应用于 **2026-11-25T23:43:20.599Z**，**早于**每一次扩展构建',
            '      > （**08:09:06** ／ **12:11:21** ／ **13:59:08**）⇒ **新扩展从未对着 017 跑过**。',
            '      > **回滚办法**：把 `plan/runtime_evidence/backups/flashloan_rs_pre_D196_20261126T1400.dll`',
            '      > 复制回 `flashloan/rs-src/target/release/` 并重启 runtime。',
            '      > **诚实边界**：本框只覆盖「扩展与迁移同批上线」；**广播尝试落库**是**下一框**，属**市场条件**。',
        ],
    )

    print('T-广.02 gates recorded')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
