# TASK-170 rollback rehearsal - directional pool switch


| Item | Value |
|---|---|
| Mode | `rehearse` |
| Live library written | **no** |
| Library | `E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3` |
| Chain | 4663 |
| Generated | 2026-11-23T05:11:25+0800 |
| Passed | **yes** |

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

## Shadow manifest (what the rehearsal actually rolled back)

| Table | Target columns | Affected rows | State |
|---|---|---:|---|
| `manager_v4_closed_loop_hops` | `directional_pool_id`, `hop_direction`, `directional_input_token_key_id`, `directional_output_token_key_id` | 504 | ready |
| `manager_v4_closed_loops` | `directional_pool_path_key` | 168 | ready |

## Rehearsal checks

| Check | Result |
|---|---|
| closedLoopSetUnchanged | PASS |
| allDirectionalColumnsNulled | PASS |
| secondPassChangedNothing | PASS |
| transactionRolledBack | PASS |
| libraryFileUnchanged | PASS |
| noWalContentWritten | PASS |
| liveLibraryQuiescent | PASS |
| walContentAttributableToRehearsal | PASS |
| libraryFileUnchangedOrExternalWriter | PASS |

## Live library watcher

| Field | Value |
|---|---|
| Sample interval | 0.25 s |
| Samples taken | 316 |
| Live file changed during the window | no |
| Sibling -wal/-shm changed during the window | no |
| WAL carried content during the window | no |


| Fingerprint | Before | After |
|---|---|---|
| sizeBytes | 8601968640 | 8601968640 |
| mtimeNs | 1795380761685225700 | 1795380761685225700 |
| sha256 | ad3e56c7bacc50181d22f548668e312d55dfc0e47601b84024c4112441cd1f0f | ad3e56c7bacc50181d22f548668e312d55dfc0e47601b84024c4112441cd1f0f |

| Table | Rows before | Rows after | Equal |
|---|---:|---:|---|
| `manager_v4_closed_loops` | 168 | 168 | yes |
| `manager_v4_closed_loop_hops` | 504 | 504 | yes |


> The live library was opened with `mode=ro` and `PRAGMA query_only`;
> the rehearsal ran on a shadow copy in a temporary directory and rolled
> its transaction back.
>
> SQLite creates the shared-memory index and the write-ahead log next to a WAL library when it is opened, read-only included. They carry no library data and the log stays zero bytes, so they are not a write to the library; the library file itself is compared byte for byte.
