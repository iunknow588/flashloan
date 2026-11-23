# TASK-166 middle-hop change attribution

Read-only.  The store was opened with `mode=ro`; the script holds no write statement.

| item | value |
|---|---|
| generated at | 2026-11-23T05:21:40.627231+00:00 |
| store | `flashloan/db/local_store_4663.sqlite3` |
| network / chain | robinhood / 4663 |
| source report digest | `622cb86e0fe41cabdf667a14142105af456aff75b68d11281ae8046ffcbd58eb` |

## The readings

| reading | measured | numerator / denominator | role |
|---|---|---|---|
| midChangeRate | 0.0275 | 28 / 1020 | where the change lands (blast radius) |
| midChangePoolCount | 0.1395 | 12 / 86 | how far the pool universe itself moved |
| loopNetRate | 0.0000 | 0 / 168 | production loss |
| profitDrop | n/a | not measured | economic cost; must be measured once in a live wave |
| loopsWithoutBackup | 0.4345 | 73 / 168 | where the change has no fallback at all |
| changeConcentration | 0.5122 | 42 / 82 | a concentrated change is a local phenomenon; a spread one questions the main order itself |

| caliber of each reading |
|---|---|
| midChangeRate | distinct (pair, direction) groups whose middle-hop pool changed, over all (pair, direction) groups of the market observation |
| midChangePoolCount | the distinct pools the middle hop moves to, over the usable base pools |
| loopNetRate | (new closed loops - old closed loops) / old closed loops |
| profitDrop | the median drop of the effective profit over the affected closed loops |
| loopsWithoutBackup | closed loops whose middle hop has no backup candidate; a pool change on such a loop cannot fall back |
| changeConcentration | share of all middle-hop change events that fall into the ten (pair, direction) groups with the most events |

## Where the change lands

Attribution of the middle-hop change: which direction groups carry it, how concentrated it is, what the new pools have in common, and which closed loops have no fallback pool at all.

| item | value |
|---|---|
| (pair, direction) groups | 1020 |
| groups with a middle-pool change | 28 |
| groups without a change | 992 |
| change rate | 0.0275 |

| direction | groups | changed groups | change events |
|---|---|---|---|
| 0 | 510 | 14 | 41 |
| 1 | 510 | 14 | 41 |

### The ten groups that carry the change

| pair | direction | changed triples |
|---|---|---|
| `robinhood:4663:0x0000000000000000000000000000000000000000:robinhood:4663:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | 0 | 5 |
| `robinhood:4663:0x0000000000000000000000000000000000000000:robinhood:4663:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | 1 | 5 |
| `robinhood:4663:0x0000000000000000000000000000000000000000:robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35` | 0 | 4 |
| `robinhood:4663:0x0000000000000000000000000000000000000000:robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35` | 1 | 4 |
| `robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:robinhood:4663:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3` | 0 | 4 |
| `robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:robinhood:4663:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3` | 1 | 4 |
| `robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35` | 0 | 4 |
| `robinhood:4663:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:robinhood:4663:0xc0d6457c16cc70d6790dd43521c899c87ce02f35` | 1 | 4 |
| `robinhood:4663:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 0 | 4 |
| `robinhood:4663:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:robinhood:4663:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | 1 | 4 |

The ten groups carry **42 of 82** change events (**0.5122**).

### The pools the middle hop moves to

| pool | changed triples | fee | tick spacing | depth | capacity |
|---|---|---|---|---|---|
| `0x0fef17ec9ef628d47aa90990e96aac90f355145cfa601b8b73c4e5d52b166237` | 10 | 1800 | 18 | medium | 86830935518727479 |
| `0x2bca43d9d8c75399e3c6ba14e9dc88f44ca8968bb4694a8be4f80bd5a550df2e` | 8 | 500 | 5 | medium | 46817996505225304 |
| `0x8567e70dbf639a618ba5eaf9402743452b93ca45c9bf8b97462407f07d7b7448` | 8 | 3000 | 60 | deep | 160973405393 |
| `0xc58bb68060bcb7b3ea0f6d4a4afef1e57f628722e808db12afb6d1733552872f` | 8 | 310 | 3 | medium | 46387798173018242 |
| `0xe6b713cf680868a40aafca45810b0c894eb5c696f5a2230d4816e6695e90e681` | 8 | 500 | 5 | medium | 115401315515787413 |
| `0xf89d70906c1464e5f0853fbb0601c111bfb48ef54537c0527694a559b33dd801` | 8 | 2100 | 21 | medium | 14502478104796787 |
| `0x10906975ba92bce9209431984d3e3e4b0eac706db274e8d1a29358331a555987` | 6 | 2500 | 25 | medium | 2011404467614383240 |
| `0xa821b3724b451b22476c5347471b2a96d185b86af8760f226273e34ab4b2b8a0` | 6 | 500 | 5 | medium | 485394941027511658 |
| `0x056dbf9e55b6480a8b8134a318c96e59424a6533c935e36ee04d8a6a751a91c1` | 4 | 5500 | 55 | medium | 3513386 |
| `0x659e64f089e5d9ea30c4c50d01d365008cfd336b396c05853ea7700af3eb78f5` | 4 | 2120 | 42 | medium | 2286424 |
| `0x93ccaa0dbab753beaef789704d622d05f619eb547039a0220625693ea12a333e` | 4 | 2523 | 25 | medium | 1416495 |
| `0xa7a475ddd5b32b74b7cd23d715270cd9b3ba65d1d25f1a19b8427c28fd76d566` | 4 | 29 | 1 | deep | 6223793 |

| feature | distribution over the new pools |
|---|---|
| fee | {"1800": 1, "2100": 1, "2120": 1, "2500": 1, "2523": 1, "29": 1, "3000": 1, "310": 1, "500": 3, "5500": 1} |
| tickSpacing | {"1": 1, "18": 1, "21": 1, "25": 2, "3": 1, "42": 1, "5": 3, "55": 1, "60": 1} |
| depthStatus | {"deep": 2, "medium": 10} |
| poolStatus | {"active": 12} |
| dynamicFee | {"0": 12} |
| specialFee | {"0": 12} |

### The loops that have no fallback pool

| item | value |
|---|---|
| closed loops | 168 |
| loops without a middle-pool backup | 73 |
| share | 0.4345 |

| middle-hop candidate count | loops |
|---|---|
| 1 | 73 |
| 2 | 53 |
| 3 | 6 |
| 4 | 36 |

| fate | loops |
|---|---|
| removedByNewCaliber | 4 |
| retainedUnchanged | 69 |

### The loops the new caliber drops

| dropped loop | had a backup | rejected by | new middle pool |
|---|---|---|---|
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x6330d8c3178a418788df01a47479c0ce7ccf450b` | no | noMiddlePool | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x6330d8c3178a418788df01a47479c0ce7ccf450b:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | no | noMiddlePool | - |
| `0xa8cf7c51eee671646ba440ffa180f970902fd2fb:0x02a0bacc642d501422af804a462f98335b2bbf09:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830` | no | noMiddlePool | - |
| `0xa8cf7c51eee671646ba440ffa180f970902fd2fb:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830:0x02a0bacc642d501422af804a462f98335b2bbf09` | no | noMiddlePool | - |

| middle pool of the dropped loops | in core4 | in candidate exchange | dropped loops |
|---|---|---|---|
| `0x982af6ff6a2169c91a78634d3d10b3e1fe1e6a7e76387de04167234a41d50e4c` | absent | active | 2 |
| `0xe992d4e9041b58b363a83c1863a664a7c07e46728ee6788a38f789102bff83a0` | absent | active | 2 |

| loop | path | middle candidates | fate | new middle pool |
|---|---|---|---|---|
| `0x0000000000000000000000000000000000000000:0x0bd7d308f8e1639fab988df18a8011f41eacad73:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | ETH -> WETH -> NVDA -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | ETH -> SPY -> NVDA -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | ETH -> SPY -> MU -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | ETH -> USDG -> MU -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x0bd7d308f8e1639fab988df18a8011f41eacad73` | ETH -> NVDA -> WETH -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | ETH -> NVDA -> SPY -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | ETH -> NVDA -> MU -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | ETH -> MU -> SPY -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | ETH -> MU -> USDG -> ETH | 1 | retainedUnchanged | - |
| `0x0000000000000000000000000000000000000000:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | ETH -> MU -> NVDA -> ETH | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x0000000000000000000000000000000000000000:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3` | SPY -> ETH -> GOOGL -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3:0x0000000000000000000000000000000000000000` | SPY -> GOOGL -> ETH -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x322f0929c4625ed5bad873c95208d54e1c003b2d:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | SPY -> TSLA -> USDG -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x322f0929c4625ed5bad873c95208d54e1c003b2d` | SPY -> USDG -> TSLA -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | SPY -> USDG -> AMD -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9` | SPY -> USDG -> AAPL -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | SPY -> USDG -> MU -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | SPY -> AMD -> USDG -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | SPY -> AMD -> NVDA -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | SPY -> AAPL -> USDG -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | SPY -> NVDA -> AMD -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | SPY -> NVDA -> MU -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | SPY -> MU -> USDG -> SPY | 1 | retainedUnchanged | - |
| `0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | SPY -> MU -> NVDA -> SPY | 1 | retainedUnchanged | - |
| `0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3:0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | GOOGL -> ETH -> SPY -> GOOGL | 1 | retainedUnchanged | - |
| `0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x0000000000000000000000000000000000000000` | GOOGL -> SPY -> ETH -> GOOGL | 1 | retainedUnchanged | - |
| `0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:0x322f0929c4625ed5bad873c95208d54e1c003b2d:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | SPCX -> TSLA -> USDG -> SPCX | 1 | retainedUnchanged | - |
| `0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x322f0929c4625ed5bad873c95208d54e1c003b2d` | SPCX -> USDG -> TSLA -> SPCX | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x0000000000000000000000000000000000000000:0x0bd7d308f8e1639fab988df18a8011f41eacad73` | USDG -> ETH -> WETH -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | USDG -> ETH -> SPY -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x0000000000000000000000000000000000000000:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3` | USDG -> ETH -> GOOGL -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x0bd7d308f8e1639fab988df18a8011f41eacad73:0x0000000000000000000000000000000000000000` | USDG -> WETH -> ETH -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x0000000000000000000000000000000000000000` | USDG -> SPY -> ETH -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x6330d8c3178a418788df01a47479c0ce7ccf450b` | USDG -> SPY -> COIN -> USDG | 1 | removedByNewCaliber | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | USDG -> SPY -> AMD -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9` | USDG -> SPY -> AAPL -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | USDG -> SPY -> MU -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x2e0847e8910a9732eb3fb1bb4b70a580adad4fe3:0x0000000000000000000000000000000000000000` | USDG -> GOOGL -> ETH -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x322f0929c4625ed5bad873c95208d54e1c003b2d:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea` | USDG -> TSLA -> SPCX -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x4a0e65a3eccec6dbe60ae065f2e7bb85fae35eea:0x322f0929c4625ed5bad873c95208d54e1c003b2d` | USDG -> SPCX -> TSLA -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x6330d8c3178a418788df01a47479c0ce7ccf450b:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | USDG -> COIN -> SPY -> USDG | 1 | removedByNewCaliber | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x7066a64c24e4206cd62e83bf198c1e7eb361f51e:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5` | USDG -> PFE -> CRCL -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | USDG -> AMD -> SPY -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xaf3d76f1834a1d425780943c99ea8a608f8a93f9:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | USDG -> AAPL -> SPY -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x0bd7d308f8e1639fab988df18a8011f41eacad73` | USDG -> NVDA -> WETH -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | USDG -> NVDA -> SPY -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | USDG -> NVDA -> AMD -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | USDG -> NVDA -> MU -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xdf0992e440dd0be65bd8439b609d6d4366bf1cb5:0x7066a64c24e4206cd62e83bf198c1e7eb361f51e` | USDG -> CRCL -> PFE -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xec262a75e413fafd0df80480274532c79d42da09:0x0000000000000000000000000000000000000000` | USDG -> MSTR -> ETH -> USDG | 1 | retainedUnchanged | - |
| `0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | USDG -> MU -> SPY -> USDG | 1 | retainedUnchanged | - |
| `0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | AMD -> SPY -> NVDA -> AMD | 1 | retainedUnchanged | - |
| `0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | AMD -> NVDA -> SPY -> AMD | 1 | retainedUnchanged | - |
| `0xa8cf7c51eee671646ba440ffa180f970902fd2fb:0x02a0bacc642d501422af804a462f98335b2bbf09:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830` | TZ -> TB -> TA -> TZ | 1 | removedByNewCaliber | - |
| `0xa8cf7c51eee671646ba440ffa180f970902fd2fb:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830:0x02a0bacc642d501422af804a462f98335b2bbf09` | TZ -> TA -> TB -> TZ | 1 | removedByNewCaliber | - |
| `0xc0d6457c16cc70d6790dd43521c899c87ce02f35:0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | META -> ETH -> SPY -> META | 1 | retainedUnchanged | - |
| `0xc0d6457c16cc70d6790dd43521c899c87ce02f35:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x0000000000000000000000000000000000000000` | META -> SPY -> ETH -> META | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x0000000000000000000000000000000000000000:0x0bd7d308f8e1639fab988df18a8011f41eacad73` | NVDA -> ETH -> WETH -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | NVDA -> ETH -> SPY -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x0bd7d308f8e1639fab988df18a8011f41eacad73:0x0000000000000000000000000000000000000000` | NVDA -> WETH -> ETH -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x0000000000000000000000000000000000000000` | NVDA -> SPY -> ETH -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | NVDA -> SPY -> AMD -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | NVDA -> SPY -> MU -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc` | NVDA -> USDG -> AMD -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x5fc5360d0400a0fd4f2af552add042d716f1d168:0xff080c8ce2e5feadaca0da81314ae59d232d4afd` | NVDA -> USDG -> MU -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | NVDA -> AMD -> SPY -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x86923f96303d656e4aa86d9d42d1e57ad2023fdc:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | NVDA -> AMD -> USDG -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | NVDA -> MU -> SPY -> NVDA | 1 | retainedUnchanged | - |
| `0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x5fc5360d0400a0fd4f2af552add042d716f1d168` | NVDA -> MU -> USDG -> NVDA | 1 | retainedUnchanged | - |
| `0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x0000000000000000000000000000000000000000:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | MU -> ETH -> SPY -> MU | 1 | retainedUnchanged | - |
| `0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0x0000000000000000000000000000000000000000` | MU -> SPY -> ETH -> MU | 1 | retainedUnchanged | - |
| `0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0x117cc2133c37b721f49de2a7a74833232b3b4c0c:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec` | MU -> SPY -> NVDA -> MU | 1 | retainedUnchanged | - |
| `0xff080c8ce2e5feadaca0da81314ae59d232d4afd:0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec:0x117cc2133c37b721f49de2a7a74833232b3b4c0c` | MU -> NVDA -> SPY -> MU | 1 | retainedUnchanged | - |

## The verdict stays with a human

**Decision: PENDING_HUMAN_CONFIRMATION.**

The script collects the readings and the attribution and refuses to decide. The former thresholds are now readings: they describe where the change lands, they no longer gate anything. The verdict stays with a human.

