# TASK-176 · JSONL 逐行增量与 removed 事件

生成时间：2026-11-23T00:27:28.565253+00:00

## 实现

- `diff_review_generations` 只写**差异集**（新 / 变 ⇒ `upsert`，消失 ⇒ `removed`）
- 比较字段 **11** 项（含 `closed_loop_version`）
- 复核管理器在**事务提交之后**才发布差异集


## 验证

- review-manager 153 passed / 0 failed | broadcast-manager 14 passed / 0 failed | guards 10 passed
- Cargo.lock 逐字节一致（零 diff）
- 主库未打开、未写入（size=8601968640 B，mtime=2026-11-22T22:07:03.074520+00:00）
