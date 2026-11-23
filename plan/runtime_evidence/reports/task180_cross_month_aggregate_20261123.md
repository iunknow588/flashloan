# TASK-180 · 月归属判据与按币种分组的精确聚合

生成时间：2026-11-23T00:27:28.565253+00:00

## 实现

- 月归属：记录 `created_at` 的 **UTC 月**（发起月）
- 聚合：**按币种分组**，**禁止 SQL SUM()**，在 Rust 侧用 U256 精确求和
- 月库**只读**打开，一次一个，**绝不**把月库 attach 到主库连接上


## 验证

- review-manager 153 passed / 0 failed | broadcast-manager 14 passed / 0 failed | guards 10 passed
- Cargo.lock 逐字节一致（零 diff）
- 主库未打开、未写入（size=8601968640 B，mtime=2026-11-22T22:07:03.074520+00:00）
