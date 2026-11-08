# P0-1.0 非 DEX 后台隔离阻塞记录

- 任务：`P0-1.0`
- scope：`robinhood / 4663 / uniswap_v4 / dex-arbitrage-4663-uniswap-v4`
- 观察时间：2026-11-07T09:18:59Z
- 运行证据：
  `p0_1_runtime_scope_partial_20261107T091859Z.json`
- 运行证据 SHA-256：
  `3B867D7FA821FDEEE1B0D63CE2BD48AB0D2401ED5E33090E73ED0E72CEC3F6BB`

## 已观察事实

1. `flashloan/app_runtime/run.py` PID `22664` 是 `127.0.0.1:5000` 的端口
   owner，命令行匹配预期入口；wrapper PID 为 `8992`。
2. `flashloan/src/runtime/logs/server_stdout.log` 输出
   `CoW realtime quote: enabled=true cooldown=0.25s max_inflight=2`。
3. 通用启动函数仍调用 `initialize_liquidation_runtime()` 和
   `initialize_cow_arbitrage_runtime()`，后台初始化线程名为
   `liquidation-runtime-init`。
4. `run.py` 在通用路径调用 `ensure_bnb_shared_collector_running()`；
   该函数本身未检查 `FLASHLOAN_BNB_SHARED_DATABASE=0`，因此仅设置该环境变量
   不能证明 BNB 采集器被关闭。
5. 当前运行进程的 `/healthz` 和 `/api/status` 可访问，但同一短窗口报告仍记录
   `status_response_contract_failed` 和 `submission_read_probe_failed`。

## 结论

主网 Web “无法启动”问题已经解除，但当前进程不是纯 V4 DEX 运行态，P0-1.0
不能标记完成。下一步应按 `FLASHLOAN_RUNTIME_PROFILE=v4_dex` 契约：

- 强制关闭 CoW 实时报价和订单提交。
- 不调用 CoW 与清算初始化入口。
- 强制关闭 BNB Venus watcher 和 BNB 共享采集器。
- 让 BNB 采集器自身遵守 `FLASHLOAN_BNB_SHARED_DATABASE=0`。
- 重启后重新保存 PID、命令行、端口 owner、最终开关状态和连续 5 分钟健康证据。

本记录只描述当前运行边界，不新增 CoW 或清算任务，也不修改历史阻塞证据。
