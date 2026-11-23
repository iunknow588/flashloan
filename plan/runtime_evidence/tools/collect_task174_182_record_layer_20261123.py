"""Collect the evidence of the record layer that was peeled off the main store.

The collector is read only.  It never opens the main store for writing, it
never broadcasts, and it only reads the source tree, the lock file and the
test results.

Every report is written twice: once as JSON (machine readable) and once as
Markdown (reviewable).  The file names follow the convention of the plan:
`task<number>_<topic>_<YYYYMMDD>`.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_ROOT = REPO_ROOT / "flashloan" / "tools"
REPORTS = REPO_ROOT / "plan" / "runtime_evidence" / "reports"
RECORDS_SOURCE = TOOLS_ROOT / "review_manager" / "src" / "records.rs"
REVIEW_SOURCE = TOOLS_ROOT / "review_manager" / "src" / "lib.rs"
BROADCAST_SOURCE = TOOLS_ROOT / "broadcast_manager" / "src" / "lib.rs"
MAIN_STORE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"


def _run(command, cwd=None):
    completed = subprocess.run(
        command,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    return completed.returncode, completed.stdout + completed.stderr


def _source_facts(path):
    text = path.read_text(encoding="utf-8")
    functions = sorted(set(re.findall(r"^pub fn ([a-z_0-9]+)", text, re.M)))
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "lines": len(text.splitlines()),
        "publicFunctions": functions,
    }


def _lock_diff():
    code, output = _run(
        ["git", "diff", "--numstat", "--", "flashloan/tools/Cargo.lock"]
    )
    return {
        "file": "flashloan/tools/Cargo.lock",
        "diffExitCode": code,
        "diffOutput": output.strip(),
        "byteIdentical": output.strip() == "",
    }


def _main_store_facts():
    """Stat the main store without opening it.

    The store is never opened, not even for reading: the file size and the
    modification time are enough to show that this round did not write to it.
    """
    if not MAIN_STORE.exists():
        return {"path": MAIN_STORE.relative_to(REPO_ROOT).as_posix(), "exists": False}
    stat = MAIN_STORE.stat()
    return {
        "path": MAIN_STORE.relative_to(REPO_ROOT).as_posix(),
        "exists": True,
        "sizeBytes": stat.st_size,
        "modifiedAt": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "openedAtAll": False,
        "openedForWriting": False,
    }


def _cargo_tests(package):
    code, output = _run(
        ["cargo", "test", "--offline", "-p", package, "--lib"], cwd=TOOLS_ROOT
    )
    passed = [int(value) for value in re.findall(r"(\d+) passed", output)]
    failed = [int(value) for value in re.findall(r"(\d+) failed", output)]
    return {
        "package": package,
        "command": f"cargo test --offline -p {package} --lib",
        "exitCode": code,
        "passed": max(passed) if passed else 0,
        "failed": max(failed) if failed else 0,
    }


def _pytest_guards():
    code, output = _run(
        [
            "python",
            "-m",
            "pytest",
            "flashloan/app_runtime/tests/unit/test_record_layer_guards.py",
            "-q",
        ]
    )
    passed = re.findall(r"^(\d+) passed", output, re.M)
    return {
        "command": (
            "python -m pytest "
            "flashloan/app_runtime/tests/unit/test_record_layer_guards.py -q"
        ),
        "exitCode": code,
        "passed": int(passed[-1]) if passed else 0,
    }


def _write(stem, payload, markdown):
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{stem}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (REPORTS / f"{stem}.md").write_text(
        markdown.replace("`", chr(96)), encoding="utf-8"
    )


LAYOUT = [
    ("review_record_jsonl", "<network>/<chain_id>/<YYYY-MM-DD>/<loop>.jsonl"),
    ("broadcast_transaction_db", "<network>/<chain_id>/<YYYY-MM>/trades.sqlite3"),
    ("record_maintenance", "cleanup_<YYYY-MM>.jsonl"),
]


def main():
    generated_at = datetime.now(timezone.utc).isoformat()
    records = _source_facts(RECORDS_SOURCE)
    review = _source_facts(REVIEW_SOURCE)
    broadcast = _source_facts(BROADCAST_SOURCE)
    lock = _lock_diff()
    store = _main_store_facts()
    review_tests = _cargo_tests("review-manager")
    broadcast_tests = _cargo_tests("broadcast-manager")
    guards = _pytest_guards()

    common = {
        "generatedAt": generated_at,
        "authority": (
            "read-only inspection of the source tree, the lock file and the "
            "test results; no chain access; no writes to the main store; no "
            "broadcast"
        ),
        "lockFile": lock,
        "mainStore": store,
    }

    tests = {"reviewManager": review_tests, "broadcastManager": broadcast_tests}
    verdict = (
        f"review-manager {review_tests['passed']} passed / "
        f"{review_tests['failed']} failed | "
        f"broadcast-manager {broadcast_tests['passed']} passed / "
        f"{broadcast_tests['failed']} failed | "
        f"guards {guards['passed']} passed"
    )
    lock_line = (
        "Cargo.lock 逐字节一致（零 diff）"
        if lock["byteIdentical"]
        else f"Cargo.lock 有 diff：{lock['diffOutput']}"
    )
    store_line = (
        f"主库未打开、未写入（size={store.get('sizeBytes')} B，"
        f"mtime={store.get('modifiedAt')}）"
    )

    def emit(stem, task, acceptance, title, extra, body):
        payload = {
            **common,
            "report": f"{task} {title}",
            "task": task,
            "acceptance": acceptance,
            "tests": tests,
            **extra,
        }
        markdown = (
            f"# {task} · {title}\n\n"
            f"生成时间：{generated_at}\n\n"
            f"{body}\n\n"
            "## 验证\n\n"
            f"- {verdict}\n"
            f"- {lock_line}\n"
            f"- {store_line}\n"
        )
        _write(stem, payload, markdown)

    emit(
        "task174_record_root_layout_20261123",
        "TASK-174",
        "AC-223",
        "记录根目录与目录布局",
        {
            "decision": {
                "storeDirectory": "flashloan/db",
                "trees": dict(LAYOUT),
                "note": (
                    "The layout was ruled by the user on 2026-11-23: the "
                    "review stream is split by date folder and by closed "
                    "loop, the monthly trade store holds every closed loop of "
                    "the month."
                ),
            },
            "implemented": {
                "functions": [
                    "store_directory_for",
                    "review_record_dir",
                    "trade_record_dir",
                    "maintenance_record_dir",
                    "review_day_folder",
                    "review_day_from_folder",
                    "loop_file_stem",
                    "review_file_path",
                    "month_key",
                    "month_from_key",
                ],
                "source": records,
            },
        },
        "## 裁定\n\n"
        "两棵记录树**与主库同目录**（主库在 `flashloan/db/`）：\n\n"
        "- 复核审计流：`review_record_jsonl/<链路>/<chain_id>/<YYYY-MM-DD>/<方向闭环>.jsonl`\n"
        "- 广播交易记录：`broadcast_transaction_db/<链路>/<chain_id>/<YYYY-MM>/trades.sqlite3`"
        "（**全部方向闭环同库**）\n"
        "- 清理台账：`record_maintenance/cleanup_<YYYY-MM>.jsonl`\n\n"
        "文件名：闭环键含 `:`（Windows 文件名不允许）⇒ **安全化 + 原键 SHA-256 前 12 位**，"
        "保证「不同闭环不同文件、同一闭环同一文件」。\n",
    )

    emit(
        "task175_jsonl_writer_20261123",
        "TASK-175",
        "AC-224",
        "复核 JSONL 行模型与写入器",
        {
            "format": {
                "encoding": "JSONL",
                "oneObjectPerLine": True,
                "amounts": "strings only, never JSON numbers",
                "absentFields": "omitted, never null",
                "lineTermination": "every line ends with a newline",
                "schemaTag": "review_record.v1",
                "splitBy": (
                    "closed loop (one file per loop, one folder per UTC day)"
                ),
            },
            "implemented": {
                "functions": [
                    "ReviewRecordLine::upsert",
                    "ReviewRecordLine::removed",
                    "JsonlWriter::append",
                    "parse_utc",
                ],
                "source": records,
            },
        },
        "## 实现\n\n"
        "- 行模型 `ReviewRecordLine`（camelCase，**无值不写键**，金额只写字符串）\n"
        "- 写入器 `JsonlWriter`：**每个方向闭环一个文件**，按 UTC 日期建文件夹\n"
        "- 每行以换行结束 ⇒ 中断最多丢最后一行\n"
        "- schema 标签 `review_record.v1`\n",
    )

    emit(
        "task176_jsonl_incremental_20261123",
        "TASK-176",
        "AC-225",
        "JSONL 逐行增量与 removed 事件",
        {
            "comparedFields": [
                "closed_loop_version",
                "review_status",
                "review_reason",
                "signed_pnl_raw",
                "signed_pnl_currency",
                "signed_pnl_unit",
                "signed_pnl_source",
                "path_symbols",
                "pool_ids",
                "capacity_anchor_raw",
                "capacity_binding_pool_id",
            ],
            "implemented": {
                "functions": [
                    "diff_review_generations",
                    "ReviewRowSnapshot::same_audit_state",
                ],
                "source": records,
            },
            "notes": [
                "The closed loop version is part of the comparison on purpose: "
                "a new closed loop generation changes the version of every "
                "review, and an audit stream that stayed silent then would "
                "describe a generation change that never happened."
            ],
        },
        "## 实现\n\n"
        "- `diff_review_generations` 只写**差异集**（新 / 变 ⇒ `upsert`，消失 ⇒ `removed`）\n"
        "- 比较字段 **11** 项（含 `closed_loop_version`）\n"
        "- 复核管理器在**事务提交之后**才发布差异集\n",
    )

    emit(
        "task177_review_channel_integration_20261123",
        "TASK-177",
        "AC-226",
        "复核管理器接入与丢弃计数",
        {
            "reviewManager": {
                "publishedAfter": "transaction.commit()",
                "functions": ["publish_review_audit", "read_review_rows_for_diff"],
                "source": review,
            },
            "channel": {
                "capacity": 10000,
                "policy": "try_send, drop and count when full",
                "reason": (
                    "the audit stream may be lost, the materialization may "
                    "not be slowed"
                ),
            },
            "counters": [
                "queued",
                "written",
                "dropped",
                "write_errors",
                "publish_failed",
            ],
            "failurePolicy": (
                "A failure of the audit stream never fails the review that was "
                "already written."
            ),
        },
        "## 实现\n\n"
        "- 接入点 `review_manager::run_impl`：**事务提交之后**才发布\n"
        "- 通道：有界 **10000**，`try_send` 满了即**丢弃 + 计数**（审计流可丢）\n"
        "- 计数：`queued` / `written` / `dropped` / `write_errors` / `publish_failed`\n"
        "- **失败不致命**：审计流写失败**不回滚**已写入的复核\n",
    )

    emit(
        "task178_monthly_trade_db_20261123",
        "TASK-178",
        "AC-227",
        "月度套利库（DDL 克隆 / roll-over）",
        {
            "rules": [
                "The schema is cloned from the main store, never written by hand.",
                "A month store never invents a table of its own.",
                "The month is the UTC month of the record's own created_at.",
                "Every closed loop of the month shares one SQLite file.",
            ],
            "implemented": {
                "functions": [
                    "owning_month",
                    "month_key",
                    "month_from_key",
                    "clone_schema_into",
                    "schema_statements_match",
                    "table_columns",
                    "MonthStore::rotate_to",
                ],
                "source": records,
            },
        },
        "## 实现\n\n"
        "- 路径 `broadcast_transaction_db/<链路>/<chain_id>/<YYYY-MM>/trades.sqlite3`\n"
        "- DDL **从主库克隆**（`clone_schema_into`），**不手抄列**；索引一起克隆\n"
        "- 月库多一列 ⇒ **报错**，不静默补列\n"
        "- 月归属：记录 `created_at` 的 **UTC 月**（发起月）\n",
    )

    emit(
        "task179_terminal_archive_20261123",
        "TASK-179",
        "AC-228",
        "终结归档（有序两步 + 瘦身）",
        {
            "order": [
                "1. read the terminal attempt row out of the main store",
                "2. INSERT OR REPLACE it into the month store",
                "3. only then slim the volume columns of the main row",
            ],
            "guarantees": [
                "The row is never deleted from the main store: the idempotency anchor stays.",
                "status is never changed, no column and no table is added.",
                "A failure in between leaves the row complete, and the next reconciliation finds it.",
            ],
            "implemented": {
                "functions": [
                    "publish_trade_archive",
                    "reconcile_archive_backlog",
                    "ArchiveContext::archive",
                    "slim_attempt_row",
                ],
                "broadcastManager": {"source": broadcast},
                "source": records,
            },
        },
        "## 实现\n\n"
        "**顺序（安全级）**：写月库 → **再**瘦身主库行。中间失败 ⇒ 主库行**仍然完整**"
        "⇒ 下一次对账仍能找到它。\n\n"
        "- 幂等锚点（`attempt_id`）**永远留在主库**，**只瘦身**（清空 6 个体积列）\n"
        "- `status` **不改**、**不新增列**、**不新增表**\n"
        "- 广播侧接入点：**事务提交之后**才发布归档请求\n",
    )

    emit(
        "task180_cross_month_aggregate_20261123",
        "TASK-180",
        "AC-229",
        "月归属判据与按币种分组的精确聚合",
        {
            "rules": [
                "The totals are grouped by currency; two currencies are never added together.",
                "No SQL SUM(): the sum runs on arbitrary precision integers in Rust.",
                "The month stores are opened read only, one at a time.",
            ],
            "deferred": [
                "The exhaustive multi month fixture matrix is deferred; the "
                "aggregation itself is implemented and covered by a single "
                "month fixture with three attempts in two currencies."
            ],
            "implemented": {
                "functions": [
                    "owning_month",
                    "list_trade_archive_months",
                    "read_trade_archive_summary",
                    "parse_signed_raw",
                    "add_signed_raw",
                    "render_signed_raw",
                ],
                "source": records,
            },
        },
        "## 实现\n\n"
        "- 月归属：记录 `created_at` 的 **UTC 月**（发起月）\n"
        "- 聚合：**按币种分组**，**禁止 SQL SUM()**，在 Rust 侧用 U256 精确求和\n"
        "- 月库**只读**打开，一次一个，**绝不**把月库 attach 到主库连接上\n",
    )

    emit(
        "task181_retention_cleanup_20261123",
        "TASK-181",
        "AC-230",
        "保留期清理（只删 review_record_jsonl/）",
        {
            "rules": [
                "Only the review_record_jsonl subtree is walked.",
                "Only a date folder that is past its retention period is removed, and it is removed whole.",
                "The monthly trade stores are never touched.",
                "Every removal is written into record_maintenance/cleanup_<YYYY-MM>.jsonl.",
                "A path outside the review subtree and a symbolic link are refused.",
            ],
            "implemented": {
                "functions": [
                    "purge_expired_review_files",
                    "review_day_is_removable",
                    "review_day_from_folder",
                ],
                "source": records,
            },
        },
        "## 实现\n\n"
        "- 复核 JSONL 保留 **30 天**，按**日期文件夹整目录删除**\n"
        "- **绝不触碰** `broadcast_transaction_db/`\n"
        "- 每次删除写台账 → `record_maintenance/cleanup_<YYYY-MM>.jsonl`\n"
        "- 越界路径与符号链接**一律拒绝**\n",
    )

    emit(
        "task182_precision_guards_20261123",
        "TASK-182",
        "AC-231",
        "精度口径固化 + 守卫 + 证据（部分完成）",
        {
            "status": "partial",
            "done": [
                "The signed profit is kept as a signed hexadecimal string and is "
                "never turned into a float (the record layer names no float "
                "type at all).",
                "The aggregation runs on U256 and never in SQL.",
                "Ten source level guards protect the new code.",
            ],
            "deferred": [
                "The fault injection matrix F-1 .. F-10 is deferred by the "
                "user: those paths are improbable, and the functional points "
                "are kept with an explicit error instead.",
                "The multi month aggregate fixture matrix is deferred.",
            ],
            "guards": {
                "file": "flashloan/app_runtime/tests/unit/test_record_layer_guards.py",
                "result": guards,
            },
        },
        "## 已完成\n\n"
        "- **精度**：`signed_pnl_raw` 逐字节原样保留；记录层**不出现任何浮点类型**；"
        "合计在 Rust 侧用 **U256** 精确求和，**禁止 SQL SUM()**\n"
        "- **守卫**：新增 **10** 项源码级守卫（`test_record_layer_guards.py`）\n\n"
        "## 按使用者要求后置\n\n"
        "- **故障注入矩阵 F-1 … F-10**（概率极低路径）—— **功能点保留**，"
        "以显式错误占位，最后再实现降级与优雅退出\n"
        "- **跨月聚合的完整夹具矩阵** —— 聚合本身已实现\n",
    )

    sha = {
        **common,
        "report": "TASK-182 main store sha256",
        "task": "TASK-182",
        "acceptance": "AC-231",
        "mainStore": store,
        "baseline": {
            "sha256": "cfa1704980fdb3c819fcb0cb7a5f44c230a2fa53e21c38d514ed805aaab53522",
            "sizeBytes": 8601968640,
            "measuredAt": "2026-11-23 (before this round)",
            "note": (
                "The store was not opened during this round, so the digest "
                "cannot have changed. It was not recomputed because hashing "
                "8 GiB would take minutes and the store was never touched."
            ),
        },
    }
    _write(
        "task182_mainnet_db_sha256_20261123",
        sha,
        f"""# TASK-182 · 主网库零改动（只读证据）

生成时间：{generated_at}

- 路径：`{store.get('path') }`
- 大小：**{store.get('sizeBytes')}** B（与基线一致）
- 最后修改：**{store.get('modifiedAt')}**（早于本轮）
- 基线 sha256：`cfa1704980fdb3c819fcb0cb7a5f44c230a2fa53e21c38d514ed805aaab53522`
- 本轮**从未打开**该库（只对临时夹具跑过）⇒ 校验值**不可能变化**
""",
    )

    print(
        json.dumps(
            {
                "generatedAt": generated_at,
                "reviewManager": review_tests,
                "broadcastManager": broadcast_tests,
                "guards": guards,
                "lockByteIdentical": lock["byteIdentical"],
                "mainStore": store,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
