# TASK-178 · 月度套利库（DDL 克隆 / roll-over）

生成时间：2026-11-23T00:27:28.565253+00:00

## 实现

- 路径 `broadcast_transaction_db/<链路>/<chain_id>/<YYYY-MM>/trades.sqlite3`
- DDL **从主库克隆**（`clone_schema_into`），**不手抄列**；索引一起克隆
- 月库多一列 ⇒ **报错**，不静默补列
- 月归属：记录 `created_at` 的 **UTC 月**（发起月）


## 验证

- review-manager 153 passed / 0 failed | broadcast-manager 14 passed / 0 failed | guards 10 passed
- Cargo.lock 逐字节一致（零 diff）
- 主库未打开、未写入（size=8601968640 B，mtime=2026-11-22T22:07:03.074520+00:00）
