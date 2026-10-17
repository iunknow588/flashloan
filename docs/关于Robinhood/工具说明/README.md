# Robinhood V4 工具说明

更新时间：2026-10-17

当前仓库的产品边界是 **Uniswap V4 单协议**。Robinhood 主网 `4663` 与测试网
`46630` 的链配置、SQLite 池索引、Rust/PyO3 执行原语和 Rust-first 报价/广播/
回执路径已经接通。主网保持 fail-closed，外部 evidence 未齐之前不允许开放广播。
Morpho 等借贷协议下一版本再支持，本轮只保留只读调研。

## 先读这里

所有 Python 工具都放在 `flashloan/src/dex/tools/`，普通命令建议从仓库根目录
执行，并使用完整相对路径。只有 benchmark 明确要求切到 `flashloan/src` 下再跑。

最常用的一组命令：

```powershell
# 测试网 V4 全量重建 + 方向候选回填
python flashloan/src/dex/tools/refresh_robinhood_v4_live.py `
  --network robinhood-testnet --chain-id 46630 --candidate-backfill

# 只重建 SQLite 业务表（不读 JSON、不打 RPC）
python flashloan/src/dex/tools/refresh_robinhood_v4_sqlite_data.py `
  --network robinhood-testnet --chain-id 46630

# 完整性报告
python flashloan/src/dex/tools/build_v4_integrity_report.py `
  --database flashloan/src/runtime/local_store.sqlite3 `
  --network robinhood-testnet --chain-id 46630

# 单元测试与编译
python -m pytest flashloan/app_runtime/tests/unit -q
python -m compileall -q flashloan/src flashloan/app_runtime

# Rust 执行原语测试
cargo test --manifest-path flashloan/rs-src/Cargo.toml
```

## 文档目录

- [01 工具清单与常用命令](./01_工具清单与常用命令.md)
- [02 测试与验收](./02_测试与验收.md)
- [03 环境变量与 RPC](./03_环境变量与RPC.md)
- [04 数据文件与证据管理](./04_数据文件与证据管理.md)
- [05 注意事项与经验](./05_注意事项与经验.md)

## 最小排障顺序

1. 先看 RPC 是否可用：直接请求 `eth_blockNumber`，失败通常是 HTTP 429 或
   TLS 握手超时，不是代码问题。
2. 再跑 `build_v4_integrity_report.py`，确认 SQLite 不是脏数据。
3. 再跑针对性单测，例如
   `python -m pytest flashloan/app_runtime/tests/unit/test_robinhood_mainnet_v4_index_refresh.py -q`。
4. 如果怀疑执行层：跑 Rust 测试和 `test_rs_execution_bridge.py`。
5. 不要在测试网 RPC 不稳定时无限重试重扫描；把阻塞项写进 evidence/checklist，
   等 RPC 稳定后重跑。
