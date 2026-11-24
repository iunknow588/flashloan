# 方向层主序 vs 旧口径主序 · 影子比对（只读）

> 生成工具：`plan/runtime_evidence/tools/compare_directional_primary_order_20261125.py`

> 库：`E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3`（mode=ro + PRAGMA query_only = ON，全程零写入）

> 执行时刻：`2026-11-24T20:24:34.805588+00:00`

## 1. 结论摘要

| 观测 | 读数 |
|---|---|
| 方向池行数 | **692**（有 pair_direction_rank 的 **342**） |
| 已发布闭环跳 | **522**（可比 **522**） |
| 跳维度换池率 | **23.7548 %**（124 / 522） |
| 受影响闭环 | **99** / **174** |
| 候选边主序（first）换池率 | **12.037 %**（13 / 108） |
| 候选边主序（third）换池率 | **12.037 %**（13 / 108） |
| 中间跳主序换池率 | **22.7979 %**（88 / 386） |

## 2. 未测量项（不得用零冒充）

- **折叠容量差异**：**未测量** —— 需要内核按新主序重折 tick 区间（写侧测量）。
- **利润差异**：**未测量** —— 依赖上面的容量复算。
- **闭环集合差异**：按构造为 **0** —— 闭环身份是代币三元组，与选中的池无关。

## 3. 分跳明细

| 跳 | 跳数 | 主序跳数 | 可比 | 换池 | 主序换池 | 换池率 % |
|---|---|---|---|---|---|---|
| y-z | 174 | 174 | 174 | 44 | 44 | 25.2874 |
| yc | 174 | 174 | 174 | 41 | 41 | 23.5632 |
| zc | 174 | 168 | 174 | 39 | 33 | 22.4138 |

## 4. 当前选中池的方向序位分布

| directional_pool_rank | 跳数 |
|---|---|
| 1 | 404 |
| 2 | 61 |
| 3 | 51 |
| None | 6 |

## 5. 选池依据分布（pool_selection_basis）

| 依据 | 跳数 |
|---|---|
| `direction_single_tick_exact_output_then_tick_fee_pool_id` | 348 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0x0bd7d308f8e1639fab988df18a8011f41eacad73` | 2 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 5 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3` | 2 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea` | 1 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 9 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | 4 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5` | 1 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0xec262a75e413fafd0df80480274532c79d42da09` | 1 |
| `observation_direction_optimal_pool:0x0000000000000000000000000000000000000000:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | 3 |
| `observation_direction_optimal_pool:0x02a0bacc642d501422af804a462f98335b2bbf09:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830` | 1 |
| `observation_direction_optimal_pool:0x0bd7d308f8e1639fab988df18a8011f41eacad73:0x0000000000000000000000000000000000000000` | 2 |
| `observation_direction_optimal_pool:0x0bd7d308f8e1639fab988df18a8011f41eacad73:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 2 |
| `observation_direction_optimal_pool:0x0bd7d308f8e1639fab988df18a8011f41eacad73:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | 2 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x0000000000000000000000000000000000000000` | 5 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3` | 2 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x322f0929c4625ed5bad873c95208d54e1c003b2d` | 1 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 8 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x6330d8c3178a418788df01a47479c0ce7ccf450b` | 1 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | 2 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9` | 1 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xc0d6457c16cc70d6790dd43521c899c87ce02f35` | 2 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | 4 |
| `observation_direction_optimal_pool:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | 3 |
| `observation_direction_optimal_pool:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3:0x0000000000000000000000000000000000000000` | 2 |
| `observation_direction_optimal_pool:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 2 |
| `observation_direction_optimal_pool:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 2 |
| `observation_direction_optimal_pool:0x322f0929c4625ed5bad873c95208d54e1c003b2d:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 1 |
| `observation_direction_optimal_pool:0x322f0929c4625ed5bad873c95208d54e1c003b2d:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea` | 1 |
| `observation_direction_optimal_pool:0x322f0929c4625ed5bad873c95208d54e1c003b2d:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 2 |
| `observation_direction_optimal_pool:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:0x0000000000000000000000000000000000000000` | 1 |
| `observation_direction_optimal_pool:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:0x322f0929c4625ed5bad873c95208d54e1c003b2d` | 1 |
| `observation_direction_optimal_pool:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x0000000000000000000000000000000000000000` | 9 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x0bd7d308f8e1639fab988df18a8011f41eacad73` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 8 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x322f0929c4625ed5bad873c95208d54e1c003b2d` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x7066a64c24e4206cd62e83bf198c1e7eb361f51e` | 1 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9` | 1 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xc0d6457c16cc70d6790dd43521c899c87ce02f35` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | 5 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5` | 2 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xec262a75e413fafd0df80480274532c79d42da09` | 1 |
| `observation_direction_optimal_pool:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | 3 |
| `observation_direction_optimal_pool:0x6330d8c3178a418788df01a47479c0ce7ccf450b:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 1 |
| `observation_direction_optimal_pool:0x7066a64c24e4206cd62e83bf198c1e7eb361f51e:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 1 |
| `observation_direction_optimal_pool:0x7066a64c24e4206cd62e83bf198c1e7eb361f51e:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5` | 1 |
| `observation_direction_optimal_pool:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 2 |
| `observation_direction_optimal_pool:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 2 |
| `observation_direction_optimal_pool:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | 2 |
| `observation_direction_optimal_pool:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 1 |
| `observation_direction_optimal_pool:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 1 |
| `observation_direction_optimal_pool:0xc0d6457c16cc70d6790dd43521c899c87ce02f35:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 2 |
| `observation_direction_optimal_pool:0xc0d6457c16cc70d6790dd43521c899c87ce02f35:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 2 |
| `observation_direction_optimal_pool:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x0000000000000000000000000000000000000000` | 4 |
| `observation_direction_optimal_pool:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x0bd7d308f8e1639fab988df18a8011f41eacad73` | 2 |
| `observation_direction_optimal_pool:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 4 |
| `observation_direction_optimal_pool:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 5 |
| `observation_direction_optimal_pool:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | 2 |
| `observation_direction_optimal_pool:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | 3 |
| `observation_direction_optimal_pool:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830:0x02a0bacc642d501422af804a462f98335b2bbf09` | 1 |
| `observation_direction_optimal_pool:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5:0x0000000000000000000000000000000000000000` | 1 |
| `observation_direction_optimal_pool:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 2 |
| `observation_direction_optimal_pool:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5:0x7066a64c24e4206cd62e83bf198c1e7eb361f51e` | 1 |
| `observation_direction_optimal_pool:0xec262a75e413fafd0df80480274532c79d42da09:0x0000000000000000000000000000000000000000` | 1 |
| `observation_direction_optimal_pool:0xec262a75e413fafd0df80480274532c79d42da09:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 1 |
| `observation_direction_optimal_pool:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x0000000000000000000000000000000000000000` | 3 |
| `observation_direction_optimal_pool:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | 3 |
| `observation_direction_optimal_pool:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 3 |
| `observation_direction_optimal_pool:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | 3 |

## 6. 受影响闭环（前 20 条）

| 闭环 | 跳 | 现用池 | 方向层主序池 | 主序 |
|---|---|---|---|---|
| `0x000000000000000000000000…` | 3 | `0x71f9660f9535…` | `0xedaf5b20d883…` | 是
| `0x000000000000000000000000…` | 2 | `0x6d0f67069596…` | `0x2bca43d9d8c7…` | 是
| `0x000000000000000000000000…` | 2 | `0x8dc0387af606…` | `0xe6b713cf6808…` | 是
| `0x000000000000000000000000…` | 3 | `0x71f9660f9535…` | `0xedaf5b20d883…` | 是
| `0x000000000000000000000000…` | 2 | `0xcb6ffbcc8435…` | `0x8567e70dbf63…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 2 | `0xe5923c8a8be4…` | `0xf840a23a8841…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 2 | `0xcb6ffbcc8435…` | `0x8567e70dbf63…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 2 | `0x5875d407a429…` | `0xc58bb68060bc…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 3 | `0x71f9660f9535…` | `0xedaf5b20d883…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 1 | `0x24107d152f14…` | `0x387bf619da4d…` | 是
| `0x000000000000000000000000…` | 2 | `0x5875d407a429…` | `0xc58bb68060bc…` | 是
| `0x000000000000000000000000…` | 1 | `0x6d9af55c4cb0…` | `0x10906975ba92…` | 是
| `0x000000000000000000000000…` | 2 | `0x628c98ab436e…` | `0x447ede3c1eb7…` | 是
| `0x000000000000000000000000…` | 2 | `0x319bac87e616…` | `0xc105b8300ff6…` | 是
| `0x000000000000000000000000…` | 3 | `0x71f9660f9535…` | `0xedaf5b20d883…` | 是
| `0x0bd7d308f8e1639fab988df1…` | 3 | `0x6ba18d461bfe…` | `0x84bd4e2d8be1…` | 是
| `0x0bd7d308f8e1639fab988df1…` | 2 | `0x387bf619da4d…` | `0x24107d152f14…` | 是

## 7. 旧口径表的最后写入时间

| 表 | 行数 | 最后写入 |
|---|---|---|
| `manager_dex_candidate_pool_recommendations` | 340 | 2026-11-24T08:41:55.837653+00:00 |
| `manager_market_observation_yz_pairs` | 1420 | 2026-11-22T12:33:58.783619100+00:00 |
| `manager_market_observation_intermediate_pools` | 348 | 2026-11-22T12:33:58.783619100+00:00 |
| `manager_v4_directional_exchange_pools` | 692 | 2026-11-23T22:46:22.591455600+00:00 |
| `manager_v4_closed_loop_hops` | 522 | 2026-11-24T01:06:24.995144+00:00 |
