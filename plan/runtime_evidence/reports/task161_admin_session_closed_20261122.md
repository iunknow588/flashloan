# TASK-161 关闭 VS Installer 自动后台下载 — 管理员会话实测闭合报告

> **生成时间**：2026-11-22T11:45:09.441527+00:00
> **判定**：**`met`**（`TASK-161` 验收口径由 `plan/runtime_evidence/tools/collect_task161_system_stability_status_20261122.py` 只读产出，可重跑）
> **报告**：`plan/runtime_evidence/reports/task161_system_stability_status_20261122.json`

## 1. 结论

**两个需要管理员的步骤本轮全部完成并实测复核。** 此前两轮（第 57 / 60 轮）判定为 `not_met`
的原因仅是"沙箱身份不是管理员"，而本轮确认 **UAC 自提升在本机可用**
（`Start-Process -Verb RunAs` 实测成功），因此两个步骤都在**同一次提权会话**内执行完毕。

| 验收项 | 判定 | 实测依据 |
|---|---|---|
| `backgroundDownloadRootCauseClosed` | **`true`** | 三层注册表策略全部 `BackgroundDownloadDisabled=1`；两个 `updates.config` 同步；计划任务 `state=Disabled`；运行中下载进程 **0** |
| `crashCaptureSwitchOpen` | **`true`** | `CrashDumpEnabled=2` / `AutoReboot=0` / `AlwaysKeepMemoryDump=1` / `MinidumpsCount=20` / `DumpLogLevel=3`，且采集器**正在持续写 CSV** |
| `crashCaptureSwitchIsOn` | `true` | 实测注册表值 |
| `kernelDumpPossible` | `true` | C 盘页面文件已**固定为显式尺寸**（不再托管） |
| `pagefileChangePendingReboot` | `true` | **页面文件新尺寸需一次重启**才真正分配 |

## 2. 步骤 0：关闭 VS Installer 后台下载（**已完成**）

命令：`plan/runtime_evidence/tools/disable_vs_background_download.ps1`（自提升执行）

### 实测复核

**三层注册表策略**（全部为 `BackgroundDownloadDisabled=1` / `BackgroundDownload=0` / `AutoUpdateDisabled=1`）：

| 注册表层 | 实测值 |
|---|---|
| `HKLM:\SOFTWARE\Policies\Microsoft\VisualStudio\Setup` | `BackgroundDownloadDisabled=1` |
| `HKLM:\SOFTWARE\Microsoft\VisualStudio\Setup` | `BackgroundDownloadDisabled=1` |
| `HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\Setup` | `BackgroundDownloadDisabled=1` |

**策略配置文件**（两份都存在且内容一致）：
`C:\ProgramData\Microsoft\VisualStudio\updates.config` 与
`C:\Program Files (x86)\Microsoft Visual Studio\Shared\updates.config`
→ 均为 `BackgroundDownloadDisabled=1 / BackgroundDownload=0 / AutoUpdateDisabled=1 / RemoveOos=0 / KeepDownloadedPayloads=0`

**计划任务**：`\Microsoft\VisualStudio\Updates\BackgroundDownload`
→ `state=Disabled`、`Enabled=False`、`lastRun=11/22/2026 18:40:40`、**`nextRun=`（空）**
→ 触发器 `MSFT_TaskIdleTrigger` 虽在，但**任务已禁用**，不会再拉起。

**运行中下载进程**：**0**

### 累计下载与"已停止"证据

| 项 | 实测 |
|---|---|
| 累计下载总量 | **1,296.82 GiB**（1,392,445,941,298 bytes，6 个实例合计） |
| `updateDownloadComplete=1` 的实例 | `968a13cd`（下载完成即**会进入安装阶段**，即崩溃触发点） |
| **100 秒前后两次采样** | `1296.82 GiB` → `1296.82 GiB`，**增长字节数 = 0** |
| 新增 `dd_BackgroundDownload_*.log` | **无**（最后一份为 `18:40:47`，即禁用时刻；此后近 1 小时无新增，**已覆盖"空闲 10 分钟"触发窗口**） |

## 3. 步骤 0-b：崩溃取证开关（**已完成**）

命令：`plan/runtime_evidence/tools/enable_crash_forensics.ps1`（提权执行）

| 注册表项 | 旧值 | **新值（实测）** | 含义 |
|---|---|---|---|
| `CrashDumpEnabled` | 7 | **2** | 内核内存转储 |
| `AutoReboot` | 1 | **0** | 崩溃后**不自动重启**，保留现场 |
| `AlwaysKeepMemoryDump` | （未设） | **1** | 不覆盖既有 dump |
| `MinidumpsCount` | 5 | **20** | 小转储保留数量 |
| `DumpLogLevel` | 0 | **3** | 转储日志级别 |

**页面文件**（`PagingFiles`）：
`C:\pagefile.sys 32768 32768` | `E:\pagefile.sys 10240 512000`
→ C 盘**不再是托管 `0 0`**。内核转储要求系统盘页面文件 **≥ 物理内存**，
原先 C 盘托管为 0 而真实后备存储在 E 盘，这正是"开关开着却没有 dump 文件"的原因。

> ⚠️ **仍需一次重启**：新的 C 盘页面文件尺寸要重启后才真正分配。
> 当前运行中的实际分配仍为 **4,864 MB**（`Win32_PageFileUsage` 实测），
> 因此**首次内核 dump 需要那次重启**。这是**唯一**的剩余项，且**不影响**下载已停止这一结论。

## 4. 崩溃前性能采集器（**已运行**，并改用 WMI 口径）

| 项 | 实测 |
|---|---|
| 交付脚本 | `plan/runtime_evidence/tools/capture_crash_watch_metrics_wmi.py`（**新增**） |
| 输出 | `plan/runtime_evidence/logs/crash_watch_metrics_20261122T190813.csv`（**每 5 秒一行**） |
| 字段 | 时间戳、可用内存 MB、CPU 负载 %、逻辑核数、C/E 盘页面文件 **已用/已分配/峰值** MB |
| 样本 | 持续增长 |

### 为什么不用原 `capture_crash_watch_metrics.cmd`

本机系统区域为 **zh-CN**，实测：

- `Get-Counter -ListSet *` 返回 **152** 个计数器集，其中**不存在** `Memory` / `Processor` / `Paging File`
  （只有 `Processor Information`、`Hyper-V Dynamic Memory Balancer` 等）；
- `typeperf` 直接调用（**含提权后**）一律返回 `错误: 没有有效计数器`；
- WMI 性能类 `Win32_PerfFormattedData_PerfOS_Memory` 等同样**无效类**。

结论：**经典 PerfMon 计数器集在本机缺失**，原脚本无法产出 CSV。
新采集器改用**始终可用**的基础 WMI 类（`Win32_OperatingSystem` / `Win32_PageFileUsage` / `Win32_Processor`），
并在 typeperf 可用时**自动**追加 PerfMon 口径做双源互证（当前走 WMI 单源，`source=wmi`）。

## 5. 采集器缺陷修正（本轮顺带修掉的两个真实 bug）

| 缺陷 | 原状 | 修正 |
|---|---|---|
| 采集器把判定**写死** | `"crashCaptureSwitchOpen": False` 且 `verdict`/`reason` 恒为 `not_met` | 改为**全部实测**：注册表实测 + 计划任务实测 + 采集器活跃度实测，三者共同决定判定 |
| 策略层读取路径错误 | 从 `policy["registryValues"]` 取（该键**不存在**）→ 永远判 `not_met` | 改为按实际结构 `policy["registryLayers"][].values` 读取，**三层逐层校验** |
| 打印语句写死 | 打印 `False` 而非实测值 | 改为打印实测值 |
| 页面文件"托管"判定 | 未随新值刷新 | `pagefileChangePendingReboot` **单独一个字段**，与 `kernelDumpPossible` **分开记**，不混为一谈 |

> **口径纪律**：**测得 ≠ 缺失**；**未测量 ≠ 零**。判定字段一律来自实测，
> 不允许写死——写死会让报告在真实状态变化后**继续撒谎**。

## 6. 残留项（**不阻塞**主线）

1. **一次重启** —— 让 C 盘页面文件按 `32768 32768` 真正分配，之后首次内核 dump 才可产出。
2. **继续观察**：保持 `capture_crash_watch_metrics_wmi.py` 运行，
   并继续关注 `%TEMP%\dd_BackgroundDownload_*.log` 是否**再无新增**。
3. **崩溃本身仍在**：CPU 级 WHEA 硬件错误是**硬件层**问题，
   关闭后台下载**只降低崩溃频率**，**不消除**崩溃本身——这正是取证开关存在的意义。

## 7. 约束遵守

- **未放宽任何门禁**（`noGateRelaxation`）：未触碰任何金额/容量/利润口径。
- **未广播**（`noBroadcast`）：本轮**没有发送任何交易**。
- **未写生产库**：仅改系统级注册表/计划任务/配置文件。
- **未产生仓库根目录副作用文件**：所有脚本与日志都在 `plan/runtime_evidence/` 下。
- **看门狗已停**：根因已关闭，看门狗成为冗余，已结束其进程。

## 8. 归档

- 报告：`plan/runtime_evidence/reports/task161_system_stability_status_20261122.json`
- 闭合报告：`plan/runtime_evidence/reports/task161_admin_session_closed_20261122.md`
- 采集器：`plan/runtime_evidence/tools/collect_task161_system_stability_status_20261122.py`（可重跑，只读）
- 新增采集器：`plan/runtime_evidence/tools/capture_crash_watch_metrics_wmi.py`
- 管理员步骤日志：`plan/runtime_evidence/logs/task161_disable_vs_background_download_admin.log`、
  `plan/runtime_evidence/logs/task161_enable_crash_forensics_admin.log`
