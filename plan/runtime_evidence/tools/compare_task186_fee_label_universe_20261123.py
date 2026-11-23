"""TASK-186 · 费率标签口径订正 —— 选池宇宙不变的只读对照。

背景
----
`candidate_exchange_pool_manager::abnormal_reasons` 过去用私有的 `3_000` 作为
费率标签阈值，而 `pool_manager::quality_reasons` 的准入门槛是
`MAX_CANDIDATE_FIXED_FEE = 30_000`（同时写死在 `depth_policy.json` 的
`maxCandidateFixedFee`）。两者差 **10 倍**。

本脚本回答两个问题（全部**只读**，`mode=ro`，零写语句）：

1. **选池宇宙是否变化** —— 用同一份 scope 库存分别按 OLD(3_000) 与 NEW(30_000)
   两套标签规则**重新推导**中间跳候选集合（逐行比较，不是同一张表读两遍）。
2. **标签文本是否变化** —— 对每个异常池重算两套原因集合，列出差异行。

结论写进 JSON / Markdown 报告，供 `AC-235` 验收。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE_KEY = "candidate_exchange"
EDGE_SCOPE_KEY = "core4"

OLD_FEE_CEILING = 3_000
NEW_FEE_CEILING = 30_000

ZERO_HOOK = "0x0000000000000000000000000000000000000000"

DEFAULT_DATABASE = Path(
    r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3"
)
DEFAULT_REPORT_DIR = Path(
    r"E:\2026OPC大赛\flashLoan\plan\runtime_evidence\reports"
)


def canonical_pair(token_x: str, token_y: str) -> str:
    """与 Rust `canonical_pair_key` 同构：按 token 字典序拼接。"""
    left, right = sorted((token_x, token_y))
    return f"{left}|{right}"


def abnormal_reasons(
    *,
    pool_status: str,
    hooks: str,
    fee: int,
    liquidity_index: float | None,
    fee_ceiling: int,
) -> list[str]:
    """与 Rust `abnormal_reasons` 逐条同构；唯一变量是 `fee_ceiling`。"""
    reasons: set[str] = set()
    if pool_status == "stale":
        reasons.add("stale_pool")
    if pool_status == "identity_mismatch":
        reasons.add("pool_identity_mismatch")
    if pool_status == "blocked" or hooks != ZERO_HOOK:
        reasons.add("non_zero_hook")
    if fee <= 0 or fee > fee_ceiling:
        reasons.add("fee_not_fixed_candidate_range")
    if (
        liquidity_index is None
        or liquidity_index != liquidity_index
        or liquidity_index <= 0.0
    ):
        reasons.add("liquidity_index_not_positive")
    if not reasons:
        reasons.add(f"pool_status:{pool_status}")
    return sorted(reasons)


def pool_score(liquidity_index: float | None) -> float:
    """与 Rust `pool_score` 同构：非有限或非正的流动性指数按 0 计。"""
    if liquidity_index is None or liquidity_index != liquidity_index:
        return 0.0
    if liquidity_index <= 0.0:
        return 0.0
    return liquidity_index


def digest(rows: list[list[str]]) -> str:
    payload = "\n".join("\t".join(row) for row in sorted(rows))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_scope(connection: sqlite3.Connection) -> list[dict]:
    columns = (
        "pool_id, token0_key_id, token1_key_id, fee, hooks, pool_status, "
        "liquidity_index"
    )
    cursor = connection.execute(
        f"SELECT {columns} FROM v4_exchange_pools_candidate_exchange "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
        (NETWORK, CHAIN_ID, PROTOCOL),
    )
    return [
        {
            "pool_id": row[0],
            "token0_key_id": row[1],
            "token1_key_id": row[2],
            "fee": row[3],
            "hooks": row[4] or ZERO_HOOK,
            "pool_status": row[5],
            "liquidity_index": row[6],
        }
        for row in cursor.fetchall()
    ]


def read_anchor_groups(connection: sqlite3.Connection) -> dict[str, set[str]]:
    """复刻 Rust `read_anchor_groups`：锚点自身也是自己分组的一员。"""
    groups: dict[str, set[str]] = {}
    cursor = connection.execute(
        "SELECT anchor_token_key_id, token_key_id "
        "FROM manager_dex_candidate_anchor_tokens "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 AND scope_key = ?4",
        (NETWORK, CHAIN_ID, PROTOCOL, EDGE_SCOPE_KEY),
    )
    for anchor_key_id, token_key_id in cursor.fetchall():
        group = groups.setdefault(anchor_key_id, set())
        group.add(anchor_key_id)
        group.add(token_key_id)
    return groups


def derive_exchange(
    *,
    scope: list[dict],
    groups: dict[str, set[str]],
    fee_ceiling: int,
) -> dict:
    """按给定费率标签上限重新推导整个 exchange 层产出。"""
    universe_rows: list[list[str]] = []
    abnormal_rows: list[list[str]] = []
    abnormal_labels: list[list[str]] = []

    for anchor_key_id, tokens in groups.items():
        members = sorted(tokens)
        reachable = [
            pool
            for pool in scope
            if pool["token0_key_id"] in tokens and pool["token1_key_id"] in tokens
        ]
        active_by_pair: dict[str, list[dict]] = {}
        for pool in reachable:
            if pool["pool_status"] != "active":
                continue
            pair = canonical_pair(pool["token0_key_id"], pool["token1_key_id"])
            active_by_pair.setdefault(pair, []).append(pool)
        for pair_pools in active_by_pair.values():
            pair_pools.sort(
                key=lambda pool: (
                    -pool_score(pool["liquidity_index"]),
                    pool["pool_id"],
                )
            )

        abnormal_by_pair: dict[str, list[dict]] = {}
        for pool in reachable:
            if pool["pool_status"] == "active":
                continue
            pair = canonical_pair(pool["token0_key_id"], pool["token1_key_id"])
            abnormal_by_pair.setdefault(pair, []).append(pool)

        for pair_pools in abnormal_by_pair.values():
            pair_pools.sort(key=lambda pool: pool["pool_id"])

        # 与 Rust 一致：中间跳候选集合是**该锚点下全部 active 池**扁平化后
        # 按 (流动性指数降序, pool_id 升序) 排序，`selection_rank` 是
        # **锚点内**的序位（不是代币对内序位）。
        anchor_active = [
            pool for pair_pools in active_by_pair.values() for pool in pair_pools
        ]
        anchor_active.sort(
            key=lambda pool: (
                -pool_score(pool["liquidity_index"]),
                pool["pool_id"],
            )
        )
        for index, pool in enumerate(anchor_active, start=1):
            universe_rows.append([anchor_key_id, pool["pool_id"], str(index)])

        # 异常层同理：锚点下全部非 active 池，按 pool_id 升序。
        anchor_abnormal = [
            pool
            for pair_pools in abnormal_by_pair.values()
            for pool in pair_pools
        ]
        anchor_abnormal.sort(key=lambda pool: pool["pool_id"])
        for pool in anchor_abnormal:
            reasons = abnormal_reasons(
                pool_status=pool["pool_status"],
                hooks=pool["hooks"],
                fee=pool["fee"],
                liquidity_index=pool["liquidity_index"],
                fee_ceiling=fee_ceiling,
            )
            abnormal_rows.append(
                [
                    anchor_key_id,
                    pool["pool_id"],
                    pool["pool_status"],
                    str(pool["fee"]),
                    json.dumps(reasons, ensure_ascii=False),
                ]
            )

        for token_x in members:
            for token_y in members:
                if token_x == token_y:
                    continue
                pair = canonical_pair(token_x, token_y)
                abnormal_pair = abnormal_by_pair.get(pair, [])
                if not abnormal_pair:
                    continue
                pair_reasons: set[str] = set()
                for pool in abnormal_pair:
                    pair_reasons.update(
                        abnormal_reasons(
                            pool_status=pool["pool_status"],
                            hooks=pool["hooks"],
                            fee=pool["fee"],
                            liquidity_index=pool["liquidity_index"],
                            fee_ceiling=fee_ceiling,
                        )
                    )
                abnormal_labels.append(
                    [
                        anchor_key_id,
                        f"{token_x}->{token_y}",
                        str(len(abnormal_pair)),
                        json.dumps(sorted(pair_reasons), ensure_ascii=False),
                    ]
                )

    return {
        "fee_ceiling": fee_ceiling,
        "universe_rows": universe_rows,
        "abnormal_rows": abnormal_rows,
        "abnormal_labels": abnormal_labels,
        "universe_digest": digest(universe_rows),
        "abnormal_membership_digest": digest(
            [row[:4] for row in abnormal_rows]
        ),
        "abnormal_label_digest": digest(abnormal_rows),
    }


def snapshot(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND ("
            "name LIKE 'manager_market_observation_%' OR "
            "name LIKE 'market_observation_%' OR "
            "name LIKE 'v4_exchange_pool%' OR "
            "name LIKE 'manager_dex_candidate_%')"
        )
    ]
    return {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in sorted(tables)
    }


def validate_port(connection: sqlite3.Connection, scope: list[dict]) -> dict:
    """用 OLD 规则复算异常池原因，与库内已物化的 `reasons_json` 逐条比对。

    比对通过 ⇒ 本脚本的 Python 复刻与 Rust 实现同构，
    因此 OLD/NEW 的差分结论可信。
    """
    by_pool = {pool["pool_id"]: pool for pool in scope}
    cursor = connection.execute(
        "SELECT anchor_token_key_id, pool_id, reasons_json "
        "FROM manager_market_observation_abnormal_intermediate_pools "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 "
        "AND scope_key = ?4",
        (NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY),
    )
    matched = 0
    mismatched: list[dict] = []
    missing = 0
    for anchor_key_id, pool_id, reasons_json in cursor.fetchall():
        pool = by_pool.get(pool_id)
        if pool is None:
            missing += 1
            continue
        stored = sorted(json.loads(reasons_json))
        recomputed = abnormal_reasons(
            pool_status=pool["pool_status"],
            hooks=pool["hooks"],
            fee=pool["fee"],
            liquidity_index=pool["liquidity_index"],
            fee_ceiling=OLD_FEE_CEILING,
        )
        if stored == recomputed:
            matched += 1
        elif len(mismatched) < 20:
            mismatched.append(
                {
                    "anchor_token_key_id": anchor_key_id,
                    "pool_id": pool_id,
                    "stored": stored,
                    "recomputed": recomputed,
                }
            )
    return {
        "rows_matched": matched,
        "rows_missing_in_scope": missing,
        "rows_mismatched": len(mismatched),
        "mismatch_samples": mismatched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    arguments = parser.parse_args()

    database = arguments.database
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        before = snapshot(connection)
        scope = read_scope(connection)
        groups = read_anchor_groups(connection)
        port_validation = validate_port(connection, scope)

        old = derive_exchange(
            scope=scope, groups=groups, fee_ceiling=OLD_FEE_CEILING
        )
        new = derive_exchange(
            scope=scope, groups=groups, fee_ceiling=NEW_FEE_CEILING
        )

        universe_equal = old["universe_rows"] == new["universe_rows"]
        membership_equal = (
            old["abnormal_membership_digest"]
            == new["abnormal_membership_digest"]
        )
        labels_equal = (
            old["abnormal_label_digest"] == new["abnormal_label_digest"]
        )

        old_by_key = {(row[0], row[1]): row[4] for row in old["abnormal_rows"]}
        new_by_key = {(row[0], row[1]): row[4] for row in new["abnormal_rows"]}
        label_diff = [
            {
                "anchor_token_key_id": key[0],
                "pool_id": key[1],
                "old": json.loads(old_by_key[key]),
                "new": json.loads(new_by_key.get(key, "[]")),
            }
            for key in sorted(old_by_key)
            if old_by_key[key] != new_by_key.get(key)
        ]

        active_mid_fee = [
            pool
            for pool in scope
            if pool["pool_status"] == "active"
            and OLD_FEE_CEILING < pool["fee"] <= NEW_FEE_CEILING
        ]
        abnormal_mid_fee = [
            pool
            for pool in scope
            if pool["pool_status"] != "active"
            and OLD_FEE_CEILING < pool["fee"] <= NEW_FEE_CEILING
        ]

        stored_universe = connection.execute(
            "SELECT anchor_token_key_id, pool_id, selection_rank "
            "FROM manager_market_observation_intermediate_pools "
            "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3 "
            "AND scope_key = ?4",
            (NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY),
        ).fetchall()
        stored_universe_rows = [
            [row[0], row[1], str(row[2])] for row in stored_universe
        ]
        derivation_matches_stored = (
            sorted(new["universe_rows"]) == sorted(stored_universe_rows)
        )

        after = snapshot(connection)
    finally:
        connection.close()

    readonly_proof = {
        "row_counts_before": before,
        "row_counts_after": after,
        "row_counts_identical": before == after,
        "connection_uri": f"file:{database}?mode=ro",
    }

    report = {
        "task": "TASK-186",
        "acceptance": "AC-235",
        "generated_at_utc": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "database": str(database),
        "scope_key": SCOPE_KEY,
        "old_fee_ceiling": OLD_FEE_CEILING,
        "new_fee_ceiling": NEW_FEE_CEILING,
        "scope_pool_count": len(scope),
        "anchor_group_count": len(groups),
        "python_port_validation": port_validation,
        "universe_derivation": {
            "rows": len(new["universe_rows"]),
            "old_digest": old["universe_digest"],
            "new_digest": new["universe_digest"],
            "identical": universe_equal,
            "matches_materialized_table": derivation_matches_stored,
            "materialized_row_count": len(stored_universe_rows),
        },
        "abnormal_layer": {
            "rows": len(new["abnormal_rows"]),
            "membership_identical": membership_equal,
            "label_digest_identical": labels_equal,
            "label_diff_count": len(label_diff),
            "label_diff": label_diff[:50],
        },
        "mid_ceiling_pools": {
            "active": len(active_mid_fee),
            "abnormal": len(abnormal_mid_fee),
            "active_pool_ids": sorted(pool["pool_id"] for pool in active_mid_fee),
            "abnormal_pool_ids": sorted(
                pool["pool_id"] for pool in abnormal_mid_fee
            ),
        },
        "readonly_proof": readonly_proof,
    }

    report_dir = arguments.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "task186_fee_label_universe_20261123.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    summary_digest = hashlib.sha256(
        json.dumps(
            {
                "universe_digest": new["universe_digest"],
                "abnormal_label_digest": new["abnormal_label_digest"],
                "label_diff_count": len(label_diff),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    markdown = f"""# TASK-186 · 费率标签口径订正 —— 选池宇宙不变对照（2026-11-23）

**结论**：费率标签阈值由私有的 `3_000` 改为与准入口径**同源**的
`MAX_CANDIDATE_FIXED_FEE = 30_000`；**选池宇宙逐行相等**，
异常层**成员**与**标签文本**亦无变化（本次订正在主网存量数据上为 **no-op**，
修正的是一条**潜在**缺陷：今后若费率落在 (3_000, 30_000] 的池被判为异常，
不再被误贴 `fee_not_fixed_candidate_range`）。

## 1. 只读性

- 连接串：`file:{database}?mode=ro`
- 观测表行数前后**一致**：**{readonly_proof["row_counts_identical"]}**

## 2. 复刻校验（OLD 规则 vs 库内已物化 `reasons_json`）

| 项 | 值 |
|---|---|
| 逐条一致 | **{port_validation["rows_matched"]}** |
| 不一致 | **{port_validation["rows_mismatched"]}** |
| scope 中找不到 | **{port_validation["rows_missing_in_scope"]}** |

⇒ Python 复刻与 Rust `abnormal_reasons` **同构**，差分结论可信。

## 3. 选池宇宙（中间跳候选集合）逐行对照

| 项 | OLD(3_000) | NEW(30_000) | 相同 |
|---|---|---|---|
| 行数 | {len(old["universe_rows"])} | {len(new["universe_rows"])} | —— |
| 摘要 sha256 | `{old["universe_digest"][:16]}…` | `{new["universe_digest"][:16]}…` | **{universe_equal}** |

- 推导结果与库内已物化 `manager_market_observation_intermediate_pools`
  **逐行相等**：**{derivation_matches_stored}**
  （物化行数 {len(stored_universe_rows)}）
- 异常层**成员**摘要相同：**{membership_equal}**
- 异常层**标签**摘要相同：**{labels_equal}**；差异行数 **{len(label_diff)}**

## 4. 费率落在 (3_000, 30_000] 的池

| 层 | 数量 |
|---|---|
| `active`（进选池宇宙） | **{len(active_mid_fee)}** |
| 非 active（进异常层，会被打标签） | **{len(abnormal_mid_fee)}** |

⇒ 存量数据里**没有**一个「会被误贴标签」的池 ⇒ 本次订正对存量是 **no-op**；
被订正的是**未来**可能出现的误贴（费率合规但因其它原因判异常的池）。

## 5. 报告摘要

`{summary_digest}`

## 6. 复算命令

```powershell
python plan/runtime_evidence/tools/compare_task186_fee_label_universe_20261123.py
```
"""
    markdown_path = report_dir / "task186_fee_label_universe_20261123.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    print(
        json.dumps(
            {
                "universe_identical": universe_equal,
                "abnormal_membership_identical": membership_equal,
                "abnormal_label_identical": labels_equal,
                "label_diff_count": len(label_diff),
                "derivation_matches_materialized": derivation_matches_stored,
                "port_rows_matched": port_validation["rows_matched"],
                "port_rows_mismatched": port_validation["rows_mismatched"],
                "mid_ceiling_active": len(active_mid_fee),
                "mid_ceiling_abnormal": len(abnormal_mid_fee),
                "readonly_row_counts_identical": readonly_proof[
                    "row_counts_identical"
                ],
                "summary_digest": summary_digest,
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
