# ADR: P0-9 主网数据库启动基线与历史迁移一致性

- 日期：2026-11-14
- 状态：审计与自愈代码完成；生产数据收敛待显式授权
- 范围：只读代码、单元测试与计划文档；生产库写入 0，Authority 切换 0，广播 0
- 机器证据：`logs/plan/runtime_evidence/reports/p0_9_database_baseline_20261114.json`

## 背景

主网编排器启动时必须先证明「代币 → 池 → 候选 → 推荐 → 观察 → 闭环」这条链上的基础数据可用，才能允许进入复核与广播。历史资料库（`chain_token_rows` / `stock_registry` / 池 currency 引用 / 池流动性证据）在迁移到核心库、股票库、扩展库、测试库时存在三类风险：

1. **锚定缺口**：扩展库代币没有对应的核心库/股票库直连池，无法参与主路径闭环，但库内仍标记为可用。
2. **scope 口径漂移**：库内持久化的 scope 成员列表与版本 1 契约（主路径只允许核心库 + 股票库）不一致。
3. **迁移滞后**：schema migration ledger 中存在已定义但未应用的迁移，导致后续读取口径与代码不一致。

此前审计只报告「未锚定代币数量」，无法定位到具体 token；`materialize-libraries` 也没有输出源→目标的迁移回执，历史资料库到目标库的可追溯性缺失。

## 决策

1. **扩展库锚定按实际直连池判定**：`extension` 库的代币只有在核心库或股票库中存在直连可交换池（swap pool）时才算锚定。未锚定数由逐 token 判定聚合得出，不再用 `total - anchored` 的减法近似（该减法在 admission 语义变化时会失真）。
2. **审计必须具名**：baseline-audit 输出 `extension.unanchoredTokens[]`，每项包含 `tokenKeyId`、`tokenAddress`、`tokenSymbol`、`v4CurrencyKey`、`admissionStatus`，使修复动作可被精确路由到 token 管理工具。
3. **空表聚合不得返回 NULL**：所有 `SUM(CASE WHEN …)` 聚合必须包裹 `COALESCE(..., 0)`。空表时 `SUM` 返回 NULL，会导致 `Invalid column type Null` 的类型错误，掩盖真正的空库结论。
4. **scope 策略单一来源**：版本 1 主路径 scope 契约固定为 `core4 = [core, stocks]` 与 `candidate_exchange = [core, stocks]`；扩展库与测试库不得进入候选 exchange。审计以该契约为期望值比较持久化策略，漂移即报 issue。
5. **迁移回执必须可复现**：`materialize-libraries` 输出 `migrationReceipt`，包含源侧行数（chain token rows / stock registry rows / pool currency references / pool liquidity evidence rows / normalized accepted / rejected）、每个目标库的 `planned` / `currentTarget` / `projectedTarget` 三态快照，以及每个目标库的 `tokenCount`、`metadataChecksum`、`statusDistribution`、`missingTokenKeyIds`、`unexpectedTokenKeyIds`、`identityMismatchTokenKeyIds` 与 `projectedConsistentWithSource`。
6. **checksum 必须与物化结果一致**：planned write checksum 由与物化写入完全相同的规范行形态按 `token_key_id` 排序计算，因此 dry-run checksum 可直接与物化后 `library_metadata_checksum` 逐位比较，无需二次物化才能验证。
7. **状态分布显式化**：每个库的 `statusDistribution` 必须区分 `admitted` / `candidate_only` / `blocked` / `pending_review`，使「候选库只做观察、不进主路径」成为可核对事实。
8. **授权边界**：审计、dry-run、checksum 计算全部只读；生产 `manager-db migrate`、token 库重物化、scope 重建与消费者重建必须等待显式授权并具备备份与回滚点。

## 主网发现（只读，2026-11-14）

### 阻塞项（4）

| issue | 事实 | 修复路径 |
|---|---|---|
| `extension_library_contains_unanchored_tokens` | 扩展库 162 个，锚定 160，未锚定 2 | token 管理工具补齐/修正锚定关系 |
| `core_scope_library_policy_stale` | 持久化 `core4 = ["core"]` | 重建为 `["core","stocks"]` |
| `candidate_scope_library_policy_stale` | 持久化 `candidate_exchange = ["core","stocks","extension","test"]` | 重建为 `["core","stocks"]` |
| `latest_manager_migration_not_applied` | 待应用 `20261112_007_usdg_valuation_schema` | `manager-db migrate` |

未锚定扩展代币具名清单：

| tokenKeyId | 地址 | 符号 | 准入状态 |
|---|---|---|---|
| `robinhood:4663:0x02a0bacc642d501422af804a462f98335b2bbf09` | `0x02a0bacc642d501422af804a462f98335b2bbf09` | TB | `candidate_only` |
| `robinhood:4663:0xd1b532ea8dbda84e6937fedabb2fd5c33a049830` | `0xd1b532ea8dbda84e6937fedabb2fd5c33a049830` | TA | `candidate_only` |

### 健康项

- 核心库 4/4、股票库 194/194，元数据（地址/符号/位数/名称）完整，`stateMatchesRows=true`。
- 候选对 42，TOP-1 方向覆盖 84/84，缺 TOP-1 方向 0。
- 候选 distinct pool 93；候选 scope 活跃池 108；活跃池缺失状态 0；动态费率/非法 hook 计数 0。
- 观察池 93 个 distinct pool，非法引用 0；528 个无直连方向按可观测空结果处理，不判为数据错误。
- 三跳闭环 106/106 完整，318 hops，失效 hop 池引用 0。
- migration ledger 已应用 7，未知迁移 0。

## 修复顺序

1. **P1-1 备份**：对生产库及 WAL/SHM 做一致性备份，记录 SHA-256、row count、schema checksum 并完成恢复演练。
2. **manager-db migrate**：应用 `20261112_007_usdg_valuation_schema`，确保 USDG 估值 TTL 在生产生效。
3. **token 库修复**：用 token 管理工具处理 TB / TA 的锚定关系（补齐直连池或转为 `blocked`/`pending_review`），不得靠放宽锚定判定来消除告警。
4. **scope 重建**：`core4 = [core, stocks]`，`candidate_exchange = [core, stocks]`；扩展库与测试库保留在库中但不进入主路径。
5. **消费者重建**：候选 → 推荐 → 观察 → 闭环按依赖顺序重物化，逐个记录 row count、checksum、版本、耗时。
6. **三方 scope checksum 一致性**：候选/推荐/观察/闭环引用同一 scope checksum。
7. **重跑 baseline-audit**：除需显式授权的生产项外不得出现其他 issue。
8. **回滚点**：每步前保留库快照；任一步 checksum 不一致、orphan 非零或回归新增失败即回滚到上一快照。

## 后果

- 未锚定扩展代币从「数量告警」变为「可精确修复的具名清单」，修复动作可直接路由。
- 历史资料库 → 目标库的可追溯性由 `migrationReceipt` 固化，dry-run 与 apply 的差异可用 checksum 提前暴露。
- 空表不再触发类型错误，baseline-audit 在全新库/空库上的结论可信。
- scope 契约在审计层强制，扩展库污染候选集的问题从「靠约定」变为「可被自动检出」。
- 生产收敛仍未完成；本 ADR 不构成生产写入授权。

## 验证

- `cargo test -p pool-manager baseline_audit::tests`：2 passed（扩展锚定判定、空表/非法目标）。
- `cargo test -p manager-api pool_baseline_audit`：1 passed（具名未锚定扩展代币）。
- `cargo test -p token-manager store::tests`：19 passed（含 planned-write checksum == materialized library checksum）。
- `cargo test -p token-manager -p pool-manager --no-run`：编译通过。
- `cargo fmt --all`：无差异。
- `scripts/verify_pool_manager_binary_matches_source.py`：`valid=true`，`sourceDigest=2925baa9a6076e61b56bcc28ad16497773a470169a14a007251c1c64246a9572`。
- `pool-manager baseline-audit --store flashloan/db/local_store_4663.sqlite3 --chain-id 4663`：只读退出码 0，输出见机器证据 JSON。

## 后续

1. 获得显式授权后执行 P1-1 备份与 `manager-db migrate`。
2. 用 token 管理工具修复 TB / TA 锚定关系。
3. 重建 `core4` / `candidate_exchange` scope 与四个消费者。
4. 重跑 baseline-audit，产出零 issue（除显式豁免项）的收敛报告。