# P0-1.0 主网 Web 运行态阻塞报告

## 结论

截至 `2026-11-07T07:26:20Z`，未确认到 Robinhood Mainnet `4663` 的
Web 运行态。`http://127.0.0.1:5000` 未监听，且未发现由
`flashloan/app_runtime/run.py` 启动的 Python 进程。因此 `P0-1.1` 至
`P0-1.3` 不能进入验收，必须先完成 `P0-1.0`。

本结论不把 `8080` 上的 Docker/WSL 监听视为应用服务。该端口当前映射到
`sub2api` 容器，不能用于主网 Web/API 证据。

## 观测范围

| 项目 | 值 |
|---|---|
| 任务 | `P0-1.0` |
| 网络 | `robinhood` |
| Chain ID | `4663` |
| Protocol | `uniswap_v4` |
| Market | `dex-arbitrage-4663-uniswap-v4` |
| Base URL | `http://127.0.0.1:5000` |
| 结论 | `blocked` |

## 事实

1. `5000` 没有监听进程，`/healthz`、`/api/status` 和提交记录 API 均不可达。
2. 严格探测共采集 4 个并发 `/api/status` 样本，成功率 `0`，错误率 `1.0`；
   此时 p95 是连接失败的耗时，不能作为服务延迟基线。
3. 当前 `8080` 的两个宿主监听分别属于 Docker/WSL relay；容器清单显示
   `sub2api`、`sub2api-redis`、`sub2api-postgres`，没有 FlashLoan Web 服务。
4. 未解析到可校验的主网 Web PID，因此未采集 RSS 和线程数据；`P0-1.3`
   继续阻塞，不能用空样本或静态测试替代。
5. 已有控制面板状态 API 契约测试通过：`14 passed`。
6. 新增采样器单元测试通过：`3 passed`。

## 复现命令

```powershell
python -m pytest -q flashloan/app_runtime/tests/functional/test_control_panel_status_api.py
python -m pytest -q flashloan/app_runtime/tests/unit/test_measure_robinhood_api_status.py
```

```powershell
python scripts/measure_robinhood_api_status.py `
  --base-url http://127.0.0.1:5000 `
  --chain-id 4663 --network robinhood --protocol uniswap_v4 `
  --market-id dex-arbitrage-4663-uniswap-v4 `
  --status-samples 4 --concurrency 4 --timeout 1 `
  --output logs/plan/runtime_evidence/reports/robinhood_api_status_20261107T072620Z_blocked.json `
  --strict
```

采样器退出码为 `2`，与严格门禁失败一致。

## 证据

- JSON：
  `robinhood_api_status_20261107T072620Z_blocked.json`
- JSON SHA-256：
  `39F85A5DE32A0EF7560C2443213189CCF99D29C18392B01BC8E781B196AB7B21`

## 恢复条件

恢复必须由 `P0-1.0` 完成并重新采集证据，至少满足：

1. 仅有一个可识别的 `flashloan/app_runtime/run.py` 主网实例监听 `5000`。
2. 证据记录 PID、完整命令行、端口 owner、启动时间、scope 和 authority。
3. `8080` 上非 FlashLoan 的 Docker/WSL 监听不得计入主网服务。
4. `/healthz` 返回 `200` 且 `status=ok`。
5. `/api/status` 返回 `200`，包含 `running` 和 `control_status`。
6. 提交 API 返回预期成功响应；失败关闭状态必须单独解释，不能直接判定通过。

只有上述条件满足后，才重新执行 `P0-1.1`、`P0-1.2` 和完整 30 分钟
`P0-1.3` 采样。
