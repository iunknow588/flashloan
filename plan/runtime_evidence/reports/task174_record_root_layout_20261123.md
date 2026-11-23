# TASK-174 · 记录根目录与目录布局

生成时间：2026-11-23T00:27:28.565253+00:00

## 裁定

两棵记录树**与主库同目录**（主库在 `flashloan/db/`）：

- 复核审计流：`review_record_jsonl/<链路>/<chain_id>/<YYYY-MM-DD>/<方向闭环>.jsonl`
- 广播交易记录：`broadcast_transaction_db/<链路>/<chain_id>/<YYYY-MM>/trades.sqlite3`（**全部方向闭环同库**）
- 清理台账：`record_maintenance/cleanup_<YYYY-MM>.jsonl`

文件名：闭环键含 `:`（Windows 文件名不允许）⇒ **安全化 + 原键 SHA-256 前 12 位**，保证「不同闭环不同文件、同一闭环同一文件」。


## 验证

- review-manager 153 passed / 0 failed | broadcast-manager 14 passed / 0 failed | guards 10 passed
- Cargo.lock 逐字节一致（零 diff）
- 主库未打开、未写入（size=8601968640 B，mtime=2026-11-22T22:07:03.074520+00:00）
