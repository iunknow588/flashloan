# D-053 `T-复.02`｜主网复核闭环身份读数 · 测试目标 check list

- [ ] 身份随 `T-复编.02` 的物化**落库**（迁移 `20261121_014` 已就位，**随重新物化写入，不历史回填**）。

- [ ] **重取**分布读数（**不得**沿用落库前读数）：

      `closedLoopIdentityStatus` / `closedLoopPoolIdentityStatus` /

      `identity_mismatch` / `closed_loop_repair_target`。

- [ ] **三态分开报**：`matched` / `mismatch` / `unmeasured`

- [ ] **缺失 ≠ 不匹配**（`unmeasured` **不得**计入 `mismatch`）

- [ ] 诊断给出**可执行的修复目标**（`closed_loop_repair_target`）。

- [ ] **不历史回填**；落库随 `T-复编.02` 的物化**同时**发生



> **落库前基线（第 60 轮，只读）**：`matched=0 / mismatch=0 / unmeasured=44`，池身份同为 `unmeasured`。

> **该读数仅作对照**，重取后**不得**沿用。