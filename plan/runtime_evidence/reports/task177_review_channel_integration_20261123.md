# TASK-177 · 复核管理器接入与丢弃计数

生成时间：2026-11-23T00:27:28.565253+00:00

## 实现

- 接入点 `review_manager::run_impl`：**事务提交之后**才发布
- 通道：有界 **10000**，`try_send` 满了即**丢弃 + 计数**（审计流可丢）
- 计数：`queued` / `written` / `dropped` / `write_errors` / `publish_failed`
- **失败不致命**：审计流写失败**不回滚**已写入的复核


## 验证

- review-manager 153 passed / 0 failed | broadcast-manager 14 passed / 0 failed | guards 10 passed
- Cargo.lock 逐字节一致（零 diff）
- 主库未打开、未写入（size=8601968640 B，mtime=2026-11-22T22:07:03.074520+00:00）
