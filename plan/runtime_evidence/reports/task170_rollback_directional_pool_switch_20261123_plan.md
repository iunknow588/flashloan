# TASK-170 rollback rehearsal - directional pool switch


| Item | Value |
|---|---|
| Mode | `plan` |
| Live library written | **no** |
| Library | `E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3` |
| Chain | 4663 |
| Generated | 2026-11-23T05:19:31+0800 |

## Rollback manifest

| Table | Target columns | Affected rows | State |
|---|---|---:|---|
| `manager_v4_closed_loop_hops` | - | 0 | no_target_columns |
| `manager_v4_closed_loops` | - | 0 | no_target_columns |

## P3 legacy column restore

| Table | Column | State | Note |
|---|---|---|---|
| `manager_dex_candidate_pool_recommendations` | `direction_key` | column_present | P3 has not degraded this column; nothing to restore |
| `manager_market_observation_yz_pairs` | `direction_key` | column_present | P3 has not degraded this column; nothing to restore |


> The live library was opened with `mode=ro` and `PRAGMA query_only`;
> the rehearsal ran on a shadow copy in a temporary directory and rolled
> its transaction back.
