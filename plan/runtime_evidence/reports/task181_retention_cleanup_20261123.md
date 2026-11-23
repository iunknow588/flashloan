# TASK-181 · 保留期清理（只删 review_record_jsonl/）

生成时间：2026-11-23T00:27:28.565253+00:00

## 实现

- 复核 JSONL 保留 **30 天**，按**日期文件夹整目录删除**
- **绝不触碰** `broadcast_transaction_db/`
- 每次删除写台账 → `record_maintenance/cleanup_<YYYY-MM>.jsonl`
- 越界路径与符号链接**一律拒绝**


## 验证

- review-manager 153 passed / 0 failed | broadcast-manager 14 passed / 0 failed | guards 10 passed
- Cargo.lock 逐字节一致（零 diff）
- 主库未打开、未写入（size=8601968640 B，mtime=2026-11-22T22:07:03.074520+00:00）
