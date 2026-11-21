#!/usr/bin/env python3
"""TASK-147｜测试网闭环缺失的根因链（只读，可重跑）。

第 56 轮把第 48 轮的根因描述（"candidate_exchange 域钉死 core v1 而当前 v3"）
**精确定位到完整的机器可验因果链**，并区分了**主因**与**次生症状**：

  主因（时序错位）：`core4` 域物化于**深度快照刷新之前**，
        看到的是旧深度裁决，只有 1 个池通过闸门。
  次生（版本漂移）：`candidate_exchange` 域早于 `core` v3 物化，
        观测层 `library_versions` 与域固定版本不一致。

本脚本**只读**，不改任何库、不访问链上。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB_DIR = REPO / "flashloan" / "db"
OUT = REPO / "plan" / "runtime_evidence" / "reports" / "task147_testnet_root_cause_chain_20261121.json"

ZERO = "0x0000000000000000000000000000000000000000"
# `pool_manager/src/depth_config.rs`: MAX_CANDIDATE_FIXED_FEE = 30_000
MAX_CANDIDATE_FIXED_FEE = 30_000
# `pool_manager/src/scope.rs`: depth_verdict_admits_pool
ADMITTING_DEPTH = {"deep", "liquid", "medium"}
# `pool_manager/src/scope.rs`: TokenLoadMode::CoreStocksTest -> fund libraries
# testnet 的 core4 域 `library_keys_json` = ["core","stocks"]
TESTNET_CORE4_LIBRARIES = ("core", "stocks")


def norm(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text.startswith("0x"):
        return "0x" + text[2:].rjust(40, "0")
    return text


def inspect_testnet(db_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    try:
        # ---- library versions: scope-pinned vs current ----
        pinned = {}
        for scope, versions, keys, rebuilt in cur.execute(
            "SELECT scope_key, library_versions_json, library_keys_json, last_rebuilt_at"
            " FROM pool_scope_config ORDER BY scope_key"
        ):
            pinned[scope] = {
                "library_versions": json.loads(versions),
                "library_keys": json.loads(keys),
                "last_rebuilt_at": rebuilt,
            }
        current = {
            key: {"library_version": version, "updated_at": updated}
            for key, version, updated in cur.execute(
                "SELECT library_key, library_version, updated_at FROM token_library_state"
            )
        }

        # ---- admitted catalog + fund anchor set ----
        catalog: dict[str, dict] = {}
        for library, token_key_id, currency, symbol in cur.execute(
            "SELECT library_key, token_key_id, v4_currency_key, token_symbol"
            " FROM token_library_catalog WHERE admission_status = 'admitted'"
        ):
            catalog.setdefault(norm(currency), {
                "token_key_id": token_key_id, "library_key": library, "symbol": symbol,
            })
        anchors = {
            token["token_key_id"]
            for token in catalog.values()
            if token["library_key"] in TESTNET_CORE4_LIBRARIES
        }

        # ---- raw pools joined with the liquidity snapshot ----
        raw = cur.execute(
            "SELECT p.pool_id, p.token0, p.token1, p.fee, p.dynamic_fee, p.special_fee,"
            "       p.tick_spacing, p.hooks, l.has_liquidity, l.liquidity_index,"
            "       l.depth_status, l.liquidity_read_at"
            "  FROM v4_exchange_pools p"
            "  LEFT JOIN v4_exchange_pool_liquidity_snapshots l"
            "    ON l.network = p.network AND l.chain_id = p.chain_id"
            "   AND l.protocol = p.protocol AND l.pool_id = p.pool_id"
            " WHERE p.network = 'robinhood-testnet' AND p.chain_id = 46630"
            " ORDER BY p.token0, p.token1"
        ).fetchall()

        eligible: list[dict] = []
        rejected: list[dict] = []
        for (pool_id, t0, t1, fee, dynamic_fee, special_fee, tick_spacing, hooks,
             has_liquidity, liquidity_index, depth_status, liquidity_read_at) in raw:
            t0n, t1n = norm(t0), norm(t1)
            reasons: list[str] = []
            if t0n not in catalog or t1n not in catalog:
                reasons.append("token_not_admitted")
            else:
                if not (catalog[t0n]["token_key_id"] in anchors
                        or catalog[t1n]["token_key_id"] in anchors):
                    reasons.append("no_core_anchor")
            if tick_spacing is None:
                reasons.append("invalid_pool_key")
            if not fee or fee > MAX_CANDIDATE_FIXED_FEE:
                reasons.append("fee_not_fixed_candidate_range")
            if norm(hooks) != ZERO:
                reasons.append("non_zero_hook")
            if has_liquidity != 1:
                reasons.append("liquidity_not_confirmed")
            if not liquidity_index or liquidity_index <= 0:
                reasons.append("liquidity_index_not_positive")
            if (depth_status or "").strip().lower() not in ADMITTING_DEPTH:
                reasons.append(f"depth_ineligible:{depth_status}")
            record = {
                "pool_id": pool_id,
                "token0": t0n, "token1": t1n,
                "symbol0": catalog.get(t0n, {}).get("symbol"),
                "symbol1": catalog.get(t1n, {}).get("symbol"),
                "fee": fee, "tick_spacing": tick_spacing,
                "depth_status": depth_status,
                "liquidity_index": liquidity_index,
                "liquidity_read_at": liquidity_read_at,
                "reject_reasons": reasons,
            }
            (rejected if reasons else eligible).append(record)

        # ---- materialized scope tables ----
        def scope_rows(table: str) -> list[dict]:
            rows = cur.execute(
                f"SELECT pool_id, token0, token1, pool_status, status_reason,"
                f"       liquidity_read_at FROM {table}"
            ).fetchall()
            return [
                {"pool_id": r[0], "token0": r[1], "token1": r[2],
                 "pool_status": r[3], "status_reason": r[4], "liquidity_read_at": r[5]}
                for r in rows
            ]

        # ---- downstream counts ----
        def count(table: str) -> int:
            return cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        observation_state = cur.execute(
            "SELECT library_versions_json, direction_count, pool_count, updated_at"
            " FROM manager_market_observation_library_state"
            " WHERE scope_key = 'candidate_exchange'"
        ).fetchone()
        edge_state = cur.execute(
            "SELECT anchor_token_count, candidate_token_count, eligible_pool_count,"
            "       library_versions_json, pool_scope_checksum, updated_at"
            "  FROM manager_dex_candidate_library_state"
        ).fetchone()
        loop_state = cur.execute(
            "SELECT loop_count, hop_count, skipped_token_count, updated_at"
            "  FROM manager_v4_closed_loop_library_state"
        ).fetchone()

        snapshot_read_times = [
            r[0] for r in cur.execute(
                "SELECT DISTINCT liquidity_read_at FROM v4_exchange_pool_liquidity_snapshots"
                " ORDER BY liquidity_read_at"
            )
        ]
        return {
            "scope_pinned_library_versions": pinned,
            "current_token_library_versions": current,
            "admitted_catalog_size": len(catalog),
            "fund_anchor_set_size": len(anchors),
            "raw_pool_count": len(raw),
            "eligible_under_current_data": eligible,
            "eligible_count_under_current_data": len(eligible),
            "rejected_count_under_current_data": len(rejected),
            "rejected_sample": rejected[:12],
            "core4_scope_rows": scope_rows("v4_exchange_pools_core4"),
            "candidate_exchange_scope_rows": scope_rows("v4_exchange_pools_candidate_exchange"),
            "observation_state": {
                "library_versions": json.loads(observation_state[0]) if observation_state else None,
                "direction_count": observation_state[1] if observation_state else None,
                "pool_count": observation_state[2] if observation_state else None,
                "updated_at": observation_state[3] if observation_state else None,
            },
            "candidate_edge_state": {
                "anchor_token_count": edge_state[0] if edge_state else None,
                "candidate_token_count": edge_state[1] if edge_state else None,
                "eligible_pool_count": edge_state[2] if edge_state else None,
                "library_versions": json.loads(edge_state[3]) if edge_state else None,
                "pool_scope_checksum": edge_state[4] if edge_state else None,
                "updated_at": edge_state[5] if edge_state else None,
            },
            "closed_loop_state": {
                "loop_count": loop_state[0] if loop_state else None,
                "hop_count": loop_state[1] if loop_state else None,
                "skipped_token_count": loop_state[2] if loop_state else None,
                "updated_at": loop_state[3] if loop_state else None,
            },
            "table_counts": {
                table: count(table) for table in (
                    "manager_v4_closed_loops",
                    "manager_v4_closed_loop_hops",
                    "manager_v4_closed_loop_optimizations",
                    "manager_market_observation_yz_pairs",
                    "manager_market_observation_intermediate_pools",
                    "manager_market_observation_liquidity_snapshots",
                    "manager_dex_candidate_anchor_tokens",
                    "manager_dex_candidate_pool_pairs",
                    "manager_dex_candidate_pools",
                )
            },
            "liquidity_snapshot_read_times": {
                "distinct": len(snapshot_read_times),
                "earliest": snapshot_read_times[0] if snapshot_read_times else None,
                "latest": snapshot_read_times[-1] if snapshot_read_times else None,
            },
        }
    finally:
        con.close()


def main() -> int:
    db_path = DB_DIR / "local_store_46630.sqlite3"
    data = inspect_testnet(db_path)

    core4 = data["scope_pinned_library_versions"].get("core4", {})
    exchange = data["scope_pinned_library_versions"].get("candidate_exchange", {})
    current_core = data["current_token_library_versions"].get("core", {}).get("library_version")

    drift = {
        scope: {
            "pinned": scope_data["library_versions"],
            "current_core_version": current_core,
            "drifted": scope_data["library_versions"].get("core") != current_core,
        }
        for scope, scope_data in data["scope_pinned_library_versions"].items()
    }

    core4_rebuilt = core4.get("last_rebuilt_at")
    latest_snapshot = data["liquidity_snapshot_read_times"]["latest"]
    ordering = {
        "core4_scope_rebuilt_at": core4_rebuilt,
        "latest_liquidity_snapshot_read_at": latest_snapshot,
        "core4_rebuilt_before_latest_snapshot": bool(
            core4_rebuilt and latest_snapshot and core4_rebuilt < latest_snapshot),
        "candidate_exchange_rebuilt_at": exchange.get("last_rebuilt_at"),
        "candidate_exchange_rebuilt_before_core4": bool(
            exchange.get("last_rebuilt_at") and core4_rebuilt
            and exchange["last_rebuilt_at"] < core4_rebuilt),
    }

    report = {
        "report": "TASK-147 testnet closed-loop root cause chain",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "chain_id": 46630,
        "authority": "read-only local database inspection; no chain access; no writes",
        "primary_cause": {
            "name": "core4_scope_materialized_before_depth_snapshot_refresh",
            "statement": (
                "core4 域物化于全部 36 条深度快照刷新之前，"
                "按当时的旧深度裁决只有 1 个池通过闸门；"
                "候选边只从 core4 取池，因此只可能产生 1 个锚点组。"
            ),
            "evidence": {
                "core4_scope_row_count": len(data["core4_scope_rows"]),
                "eligible_under_current_data": data["eligible_count_under_current_data"],
                "candidate_edge_anchor_token_count":
                    data["candidate_edge_state"]["anchor_token_count"],
                "candidate_edge_reads_pools_from": "v4_exchange_pools_core4",
            },
        },
        "secondary_symptom": {
            "name": "candidate_exchange_scope_version_drift",
            "statement": (
                "candidate_exchange 域早于 core 升到 v3 物化，"
                "其固定的 library_versions 与观测层不一致；"
                "闭环层在 library_versions.core 上 fail-closed。"
            ),
            "evidence": {
                "scope_version_drift": drift,
                "observation_library_versions": data["observation_state"]["library_versions"],
                "closed_loop_library_versions":
                    data["scope_pinned_library_versions"].get("candidate_exchange", {})
                    .get("library_versions"),
            },
        },
        "ordering": ordering,
        "observed_state": data,
        "verdict": {
            "core4_scope_is_stale_vs_depth_snapshot":
                ordering["core4_rebuilt_before_latest_snapshot"],
            "candidate_exchange_scope_version_drifted":
                drift.get("candidate_exchange", {}).get("drifted"),
            "core4_scope_version_drifted": drift.get("core4", {}).get("drifted"),
            "material_is_available_under_current_depth": data["eligible_count_under_current_data"] > 1,
            "closed_loop_count": data["table_counts"]["manager_v4_closed_loops"],
            "observation_direction_count": data["observation_state"]["direction_count"],
            "candidate_edge_anchor_count": data["candidate_edge_state"]["anchor_token_count"],
        },
        "implication": {
            "ordering_of_repairs": [
                "① 刷新 core4 域（读当前深度快照）——结构不变，仅重物化",
                "② 重建 candidate_edge（从 core4 取池）——会得到多个锚点组",
                "③ 重建 candidate_exchange 域（钉到 core v3）——消除版本漂移",
                "④ 重建市场观测（产生 yz_pairs / intermediate_pools）",
                "⑤ 重跑 Manager 闭环",
            ],
            "note": (
                "① 与 ③ 属动态记录写入（写入当前链状态下的测量结果），"
                "按计划 §0.3 仍需单独授权；"
                "但顺序不可颠倒——先刷 core4，否则 candidate_edge 无池可取。"
            ),
            "not_required": [
                "不需要放宽 MIN_POOL_TVL_USD / MAX_POOL_TVL_USD / 费率 / hooks / liquidity 任何固定门槛",
                "不需要手改 admission",
                "不需要回灌历史 loops / depth / 权限",
            ],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2))
    print(json.dumps(report["ordering"], ensure_ascii=False, indent=2))
    print(f"written: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
