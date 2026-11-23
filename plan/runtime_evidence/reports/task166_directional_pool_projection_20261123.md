# TASK-166 directional-pool shadow comparison

Read-only. The store was opened with `mode=ro`; the script holds no write statement.

| item | value |
|---|---|
| generated at | 2026-11-23T05:21:40.627231+00:00 |
| store | `flashloan/db/local_store_4663.sqlite3` |
| store size | 8601968640 bytes |
| store sha256 | `c90a70a0960bd167005eaec2656b2f560ad1b4560b20ee33cb6bb9b197541049` |
| network / chain | robinhood / 4663 |
| conclusion digest | `622cb86e0fe41cabdf667a14142105af456aff75b68d11281ae8046ffcbd58eb` |

## Oracles

| oracle | compared | matched | rate |
|---|---|---|---|
| the ported tick bound against the Rust-written snapshot bound | 444 | 122 | 0.2748 |
| the ported order against the Rust-written recommendations | 340 | 340 | 1.0000 |

### The tick bound oracle, by the writer that wrote the row last

| the row was written last by | compared | matched | rate |
|---|---|---|---|
| otherLastWriter | 356 | 34 | 0.0955 |
| rustLastWriter | 88 | 88 | 1.0000 |

The Rust pool manager writes the price columns and the tick bounds in one statement, so the rows it wrote last are self-consistent and validate the port. The Python evidence upsert overwrites the price columns and never rewrites the tick bounds, so the rows it wrote last carry a bound that belongs to an older price; those rows measure the writer drift, not the port.

## Step 2 - the base pool census

| item | value |
|---|---|
| base pool rows (`v4_exchange_pools_core4`) | 488 |
| base pool status | {"invalid": 84, "active": 86, "blocked": 318} |
| usable base pools | 86 |
| candidate-exchange rows | 494 |
| candidate-exchange status | {"invalid": 84, "active": 92, "blocked": 318} |
| candidate-exchange usable pools | 92 |
| depth snapshot rows | 2486 |

## Step 3b - the admission convention the directional pool inherits

The admission convention the pool manager already applies: a fixed fee in the candidate range, no special fee, no hook and an active base pool. The directional row is a projection of the base pool, so it may not widen the universe.

| item | value |
|---|---|
| projected rows checked | 172 |
| violations | 0 |
| the convention holds | True |
| fee range of the projected pools | 29 .. 30000 |

## Step 7 - the middle hop pool change table

| item | value |
|---|---|
| pairs | 510 |
| anchor triples | 1420 |
| changed, same universe (order only) | 15 (0.0294) |
| changed, target universe | 14 (0.0275) |
| anchor-independence violations | 0 |

## Step 8 - the closed loop set change table

| item | value |
|---|---|
| old loops | 168 |
| new loops | 168 |
| retained | 164 |
| added | 4 |
| removed | 4 |
| retained with a changed middle pool | 26 |

### Added loops

| anchor | Y | Z |
|---|---|---|
| robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c | robinhood:4663:0x0000000000000000000000000000000000000000 | robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35 |
| robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c | robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35 | robinhood:4663:0x0000000000000000000000000000000000000000 |
| robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168 | robinhood:4663:0x0000000000000000000000000000000000000000 | robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35 |
| robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168 | robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35 | robinhood:4663:0x0000000000000000000000000000000000000000 |

### Removed loops

| anchor | Y | Z |
|---|---|---|
| robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168 | robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c | robinhood:4663:0x6330d8c3178a418788df01a47479c0ce7ccf450b |
| robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168 | robinhood:4663:0x6330d8c3178a418788df01a47479c0ce7ccf450b | robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c |
| robinhood:4663:0xa8cf7c51eee671646ba440ffa180f970902fd2fb | robinhood:4663:0x02a0bacc642d501422af804a462f98335b2bbf09 | robinhood:4663:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830 |
| robinhood:4663:0xa8cf7c51eee671646ba440ffa180f970902fd2fb | robinhood:4663:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830 | robinhood:4663:0x02a0bacc642d501422af804a462f98335b2bbf09 |

## Step 9 - the capacity and profit distributions

| item | value |
|---|---|
| loops compared, both sides folded with this wave's tick state | 164 |
| capacity delta median | 0 |
| capacity delta max | 30200 |
| capacity delta min | -833265862658908099 |
| capacity ratio median | 1 |
| loops whose middle pool changed | 26 |
| capacity missing on the old side | 0 |
| capacity missing on the new side | 0 |
| measured zero on both sides | 0 |

### The materialisation drift, reported apart

The stored capacity of the closed-loop catalog against the same path folded with the tick state of this wave. The difference measures the drift between the materialisation and this wave; it is **not** a caliber difference and it is not counted as one.

| item | value |
|---|---|
| loops compared | 164 |
| stored capacity missing | 0 |
| unchanged since the materialisation | 8 |
| drift delta median | -8731697 |
| drift ratio median | 0.874269 |

A non-zero drift is expected: the catalog was folded from the tick state of the materialisation, and the pools move between then and now.

### The profit

| old profit, measured positive | 0 |
| old profit, measured zero | 0 |
| old profit, measured negative | 14 |
| old profit, unmeasured | 154 |
| new profit | unmeasured |

## Step 9b - the change attribution

Attribution of the middle-hop change: which direction groups carry it, how concentrated it is, what the new pools have in common, and which closed loops have no fallback pool at all.

| item | value |
|---|---|
| (pair, direction) groups | 1020 |
| groups with a middle-pool change | 28 |
| groups without a change | 992 |
| change rate over the groups | 0.0275 |
| direction 0: groups / changed / events | 510 / 14 / 41 |
| direction 1: groups / changed / events | 510 / 14 / 41 |
| change events | 82 |
| top-10 groups' share of the events | 0.5122 |
| distinct new pools | 12 (of 86 usable) |
| closed loops without a middle-pool backup | 73 / 168 |

| fate of the loops without a backup | loops |
|---|---|
| removedByNewCaliber | 4 |
| retainedUnchanged | 69 |

| the loops the new caliber drops | count |
|---|---|
| dropped | 4 |
| dropped by noMiddlePool | 4 |

| dropped loop | had a backup | rejected by | new middle pool |
|---|---|---|---|
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x6330d8c3178a418788df01a47479c0ce7ccf450b` | no | noMiddlePool | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x6330d8c3178a418788df01a47479c0ce7ccf450b:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | no | noMiddlePool | - |
| `0xa8cf7c51eee671646ba440ffa180f970902fd2fb:0x02a0bacc642d501422af804a462f98335b2bbf09:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830` | no | noMiddlePool | - |
| `0xa8cf7c51eee671646ba440ffa180f970902fd2fb:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830:0x02a0bacc642d501422af804a462f98335b2bbf09` | no | noMiddlePool | - |

### Why the dropped loops drop

| middle pool | in core4 | in candidate exchange | dropped loops |
|---|---|---|---|
| `0x982af6ff6a2169c91a78634d3d10b3e1fe1e6a7e76387de04167234a41d50e4c` | absent | active | 2 |
| `0xe992d4e9041b58b363a83c1863a664a7c07e46728ee6788a38f789102bff83a0` | absent | active | 2 |

| middle-hop candidate count | loops |
|---|---|
| 1 | 73 |
| 2 | 53 |
| 3 | 6 |
| 4 | 36 |

## The readings

These replace the three former thresholds.  A reading is measured and explained; it is no longer compared against a fixed value to decide whether the change may proceed.

| reading | measured | numerator / denominator | role |
|---|---|---|---|
| midChangeRate | 0.0275 | 28 / 1020 | where the change lands (blast radius) |
| midChangePoolCount | 0.1395 | 12 / 86 | how far the pool universe itself moved |
| loopNetRate | 0.0000 | 0 / 168 | production loss |
| profitDrop | n/a | not measured | economic cost; must be measured once in a live wave |
| loopsWithoutBackup | 0.4345 | 73 / 168 | where the change has no fallback at all |
| changeConcentration | 0.5122 | 42 / 82 | a concentrated change is a local phenomenon; a spread one questions the main order itself |

## Step 10 - the caliber verdict

**Decision: PENDING_HUMAN_CONFIRMATION.** The script does not decide.

| evidence | measured | role |
|---|---|---|
| midChangeRate | 0.027451 | where the change lands (blast radius) |
| loopNetRate | 0 | production loss |
| profitDrop | n/a | economic cost; must be measured once in a live wave |
| loopsWithoutBackup | 0.434524 | where the change has no fallback at all |
| changeConcentration | 0.512195 | a concentrated change is a local phenomenon; a spread one questions the main order itself |

The script collects the readings and the attribution and refuses to decide. The former thresholds are now readings: they describe where the change lands, they no longer gate anything. The verdict stays with a human.

## Scenario checks

| scenario | passed | detail |
|---|---|---|
| T-S-1 | yes | 86 usable pools, 172 projected rows, 0 admission violations |
| T-S-2 | yes | 110 groups checked |
| T-S-3 | yes | 86 pools keep the mirrored tick rank across the two directions |
| T-S-4 | yes | 510 pairs, 1420 anchor triples, 82 changed |
| T-S-5 | yes | old 168, new 168, retained 164, added 4, removed 4 |
| T-S-6 | yes | 164 loops compared on one tick state, 0 missing old, 0 missing new, 0 measured zero on both sides; the materialisation drift is reported apart (8 unchanged) |
| T-S-7 | yes | old profit: {"measuredPositive": 0, "measuredZero": 0, "measuredNegative": 14, "unmeasured": 154}; new profit: unmeasured |
| T-S-8 | yes | PENDING_HUMAN_CONFIRMATION |

## Read-only proof

The row counts of every table the script reads are identical before and after the run: **yes**.

| table | rows before | rows after |
|---|---|---|
| v4_exchange_pools_core4 | 488 | 488 |
| v4_exchange_pools_candidate_exchange | 494 | 494 |
| v4_exchange_pool_liquidity_snapshots | 2486 | 2486 |
| manager_dex_candidate_pool_recommendations | 340 | 340 |
| manager_dex_candidate_pool_pairs | 108 | 108 |
| manager_dex_candidate_anchor_tokens | 108 | 108 |
| manager_market_observation_yz_pairs | 1420 | 1420 |
| manager_market_observation_intermediate_pools | 348 | 348 |
| manager_v4_closed_loops | 168 | 168 |
| manager_v4_closed_loop_hops | 504 | 504 |
| manager_v4_reviews | 164 | 164 |
| token_library_catalog | 361 | 361 |

