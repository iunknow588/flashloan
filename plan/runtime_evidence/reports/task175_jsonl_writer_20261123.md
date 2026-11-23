# TASK-175 · 复核 JSONL 行模型与写入器

生成时间：2026-11-23T00:27:28.565253+00:00

## 实现

- 行模型 `ReviewRecordLine`（camelCase，**无值不写键**，金额只写字符串）
- 写入器 `JsonlWriter`：**每个方向闭环一个文件**，按 UTC 日期建文件夹
- 每行以换行结束 ⇒ 中断最多丢最后一行
- schema 标签 `review_record.v1`


## 验证

- review-manager 153 passed / 0 failed | broadcast-manager 14 passed / 0 failed | guards 10 passed
- Cargo.lock 逐字节一致（零 diff）
- 主库未打开、未写入（size=8601968640 B，mtime=2026-11-22T22:07:03.074520+00:00）
