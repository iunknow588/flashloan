# 崩溃取证结论纠正报告（P3 崩溃取证开关修正）

- 日期：2026-11-22
- 归属任务：`TASK-161`（P0 系统稳定性）/ 原计划「P3 — 下次崩溃能取证」段
- 主机：QIAN / Dell Precision M4700 / Intel Core i7-3840QM (Family 6 Model 58 Stepping 9) / 31.94 GB 物理内存
- 证据文件：
  - `reports/whea_cper_cpu_errors_20261122.json`（3427 条 WHEA 原始记录已解码）
  - `reports/whea_crash_correlation_20261122.json`（逐次崩溃配对）
  - `reports/whea_crash_correlation_summary_20261122.json`（统计摘要）
  - `tools/decode_whea_cper.py`、`tools/extract_whea_raw.py`、`tools/correlate_whea_crashes.py`

---

## 0.1 可复跑性（**已实测**）

本报告全部读数由**一条只读命令**重新生成并逐项核对：

```
powershell -NoProfile -ExecutionPolicy Bypass -File plan/runtime_evidence/tools/run_whea_forensics.ps1
```

**实测复现结果（10 秒内）**：

| 读数 | 报告值 | 复现值 | 一致 |
|---|---|---|---|
| Event 41 总数 | 146 | 146 | ✅ |
| 60s 内 CPU 级 WHEA 前置 | 144 | 144 | ✅ |
| 匹配率 | 98.6% | 98.6% | ✅ |
| delta min | 5.657 | 5.657 | ✅ |
| delta median | 7.706 / 7.707 | 7.707 | ✅（四舍五入差异） |
| 全部 BugcheckCode = 0 | 是 | 是 | ✅ |
| 全部 Severity = FATAL_ERROR | 是 | 是 | ✅ |
| 按月 08/09/10/11 | 95/16/17/16 | 95/16/17/16 | ✅ |
| WHEA 原始记录数 | 3427 | 3427 | ✅ |
| CPU 级 WHEA 记录数 | 1145 | 1145 | ✅ |

> **注意 `max` 的口径差异**：报告 §3 中「60s 窗口内」的 delta max = **10.345s**；
> 全量 145 条配对的 delta max = **247.377s**（含 1 条落在 60s 窗口外的离群记录）。
> 两者**不矛盾** —— 前者是**窗口内**统计，后者是**全量**统计。已在校验脚本中分别给出。

### 0.2 流水线踩坑（**后续复用必读**）

1. `wevtutil epl <log> out.xml` **导出的是二进制 EVTX** —— 即使扩展名写 `.xml`
   （文件头是 `ElfFile` 而非 `<?xml`）→ **必须用 `wevtutil qe <log> /q:"..." /f:xml`**。
2. `qe` 的 XML 输出是 **UTF-16LE 带 BOM** → 提取工具必须按 `utf-16` 解码。
3. `wevtutil` 的 **text 输出显示本地时间（+8）**，**只有 XML 的 `SystemTime` 才是 UTC** ——
   时间相关性分析**必须**走 XML 路径，否则会把事件配到错误的时刻。
4. WHEA 记录的 GUID **字节序不统一**：processor section 存小端，FRU 存大端 → 查表需两种拼写。
5. `SectionLength` 是 **WHEA 模板长度**（3800），超出实际缓冲区（1009 字节）→
   **不能**用 `length <= len(raw)` 做校验，只能校验 severity 枚举。

---

## 0. 一句话结论

原 P3 段落中 **三条陈述与实机读数不符**，本次已实测纠正：
崩溃的直接触发是 **CPU 级 WHEA 硬件错误**（不是内存问题、不是 CPU 占用问题），
`CrashDumpEnabled` **本来就是 7（自动内存转储）**，三个 WHEA/HardwareEvents 日志通道 **本来就都是 enabled**。

---

## 1. 纠正项一：`CrashDumpEnabled` 原本就是 `7`，不是「完全没有 dump」

实测读数（`HKLM\SYSTEM\CurrentControlSet\Control\CrashControl`）：

| 名称 | 实测值 | 原计划说法 | 判定 |
|---|---|---|---|
| `CrashDumpEnabled` | `7`（Automatic memory dump） | 「现在完全没有 dump」 | ❌ **错误** |
| `AutoReboot` | `1` | 需改为 `0` | ✅ 待修 |
| `DumpFile` | `C:\Windows\MEMORY.DMP` | — | 未设置但值正确 |
| `MinidumpDir` | `C:\Windows\Minidump` | — | 未设置但值正确 |
| `MinidumpsCount` | `5` | — | 偏少，建议 20 |
| `AlwaysKeepMemoryDump` | **未设置** | — | 建议显式置 1 |
| `DumpLogLevel` | `0` | — | 建议置 3 |

补充实测：`C:\Windows\MEMORY.DMP` 与 `C:\Windows\Minidump\` **确实不存在**（空目录、无 dump 文件）。
所以准确表述是：**转储开关已开，但从未产出过 dump 文件**。

### 1.1 为什么「开着却没产出」——页面文件不满足内核转储要求

```
PagingFiles      : {C:\pagefile.sys 0 0, E:\pagefile.sys 10240 512000}
ExistingPageFiles: {\??\C:\pagefile.sys, \??\E:\pagefile.sys}
物理内存 TotalPhysical : 31.94 GB
```

- `C:\pagefile.sys 0 0` = 系统托管（由 OS 自动决定大小），**且不在系统盘根目录的显式固定大小配置中**；
- 内核内存转储（`CrashDumpEnabled = 2`）要求 **系统盘页面文件 ≥ 物理内存**；
- 当前 E 盘页面文件上限 512000 MB，但**内核转储只认系统盘（C:）的页面文件**。

**因此：单独把 `CrashDumpEnabled` 从 7 改成 2 是不够的，必须同时固定 C 盘页面文件。**

---

## 2. 纠正项二：WHEA 日志通道三个本来就全开，计划里的路径也不存在

实测（`wevtutil gl`）：

| 通道 | enabled | maxSize | 说明 |
|---|---|---|---|
| `Microsoft-Windows-Kernel-WHEA/Errors` | **true** | 33554432 | 真正的硬件错误日志，当前无事件 |
| `Microsoft-Windows-Kernel-WHEA/Operational` | **true** | 33554432 | 承载 Event 42 的 CPER 原始数据 |
| `HardwareEvents`（`System\HardwareEvents.evtx`） | **true** | 20971520 | 即原计划所指的「硬件事件」通道 |

原计划写的路径 `Applications and Services Logs → Microsoft → Windows → HardwareEvents-Platform` **在 Windows 10 上不存在**。
真实对应物是上面的 `HardwareEvents`（`C:\Windows\System32\winevt\Logs\HardwareEvents.evtx`）。

**结论：「开 WHEA 硬件错误记录」这一项本来就是完成状态，本次只需做只读校验，不需要改动。**
本次交付的脚本对三个通道只做 `wevtutil gl` 只读体检，不做任何写入。

---

## 3. 纠正项三：这 5 次崩溃不是蓝屏，内核转储对它们无效

实测 Event 41（Kernel-Power）：

```
2026-11-22T08:10:49  BugcheckCode=0  LongPowerButtonPressDetected=false
2026-11-22T05:46:04  BugcheckCode=0
2026-11-22T01:14:39  BugcheckCode=0
2026-11-18T03:15:38  BugcheckCode=0
2026-11-17T13:21:52  BugcheckCode=0
```

**全部 `BugcheckCode=0`** → OS 根本没走到蓝屏/转储阶段，是**硬断电或强制复位**。
这与「开了转储也没 dump 文件」完全自洽：没有 bugcheck 就没有转储。

> ⚠️ 诚实边界：`BugcheckCode=0` **不等于**「内核转储永远没用」。
> 未来若有软件触发的 BSOD，或硬件错误升级为可捕获的异常，转储仍是唯一取证的途径。
> 正确表述是：**内核转储覆盖「未来的蓝屏类崩溃」，不覆盖本次这 5 次断电类崩溃。**

---

## 4. 真正的崩溃触发：CPU 级 WHEA 硬件错误（决定性证据）

### 4.1 CPER 解码结果

每条 Event 42（`Id=2147483660`，`Length=1009`）解出两个 CPER section：

| SectionId | 名称 | SectionLength | Severity |
|---|---|---|---|
| `83c1603c-1552-48a7-87d1-14d9467d7765` | **WHEA_PROCESSOR_GENERIC_ERROR_SECTION** | 3800 | **FATAL_ERROR** |
| `8d7c2157-665e-fb44-8033-9b74cacedf5b` | WHEA_FRU_ERROR_SECTION | 1124 | FATAL_ERROR |

配套记录：`Id=2147483658`（`PSHD` 头，len=34）、`Id=2147483650`（`KRNL`，len=32）成对出现。

> **诚实边界**：目前确认到 **CPU/内核处理器级**（`PROCESSOR_GENERIC_ERROR_SECTION`）。
> `ErrorType` 与 `ApicId` 字段尚未从该模板记录中最终定位（SectionLength 3800 是 WHEA 的**模板长度**，
> 超出 1009 字节的实际缓冲区，错误主体字段在记录中被稀疏填充），
> **因此尚未定位到具体 CPU 核心/具体故障部件**。不得据此声称「已定位到某个物理部件」。

### 4.2 崩溃与 WHEA 的时间配对（决定性）

对 2026 全年 146 次 Event 41，与全部 3427 条 WHEA 记录做时间配对：

```
Event 41 总数                     : 146
60s 内有 CPU 级 WHEA 前置记录     : 144   (98.6%)
未匹配                            : 2
所有 Event 41 的 BugcheckCode      : 全部为 0
所有匹配记录 WHEA Severity        : 全部为 FATAL_ERROR

前置时间差 delta (秒)：
  min = 5.657   max = 10.345   mean = 7.791   median = 7.706   stdev = 0.828
```

按月：

| 月份 | Event 41 | 60s 内匹配 | 命中率 |
|---|---|---|---|
| 2026-08 | 97 | 95 | 98.0% |
| 2026-09 | 16 | 16 | 100% |
| 2026-10 | 17 | 17 | 100% |
| 2026-11 | 16 | 16 | 100% |

**这排除了巧合可能**：144 次崩溃都恰好落在 CPU 硬件错误后 5.7~10.3 秒（标准差仅 0.83 秒），
这个窗口就是「WHEA 上报 → 硬件挂死 → 断电/复位」的完整链路。

---

## 5. 与「VS Installer 后台下载」的关系：两个独立问题

| | VS 后台下载 | CPU 硬件错误 (WHEA) |
|---|---|---|
| 角色 | 崩溃**频率**诱因 | 崩溃**直接**触发 |
| 证据 | 计划任务当天触发 7 次，累计下载 1,392.45 GB | 144/146 次崩溃前 5.7~10.3s 有 CPU FATAL WHEA |
| 关系 | 两者**相互独立**，需分别处理 | |

**不能**用「内存不够」或「CPU 占用太高」解释这些崩溃：
- 崩溃时 `MemoryLoad` 48%、可用物理内存 16.52 GB、可用页面文件 31.36 GB —— **不是内存耗尽**；
- `BugcheckCode=0` 且有硬件错误记录 —— **不是 OS 层崩溃**。

---

## 6. 本次交付的动作（区分「已可做」与「需管理员」）

### 6.1 已在本会话完成（免提权）

- [x] 实测并纠正全部三条错误陈述（本报告 §1、§2、§3）
- [x] 导出并解码 3427 条 WHEA 记录 → CPER 结构化 JSON
- [x] 与 146 次 Event 41 做时间配对，产出统计摘要
- [x] 生成 `tools/enable_crash_forensics.ps1`（含 `-Verify` 只读体检模式）
- [x] 生成 `tools/enable_crash_forensics.cmd`（一键提权执行器）
- [x] 生成 `tools/capture_crash_watch_metrics.ps1` + `.cmd`（崩溃前 PerfMon 快照）

### 6.2 需管理员（脚本已就绪，本会话身份 `QIAN\CodexSandboxOffline` 非管理员，无法执行）

需在管理员 PowerShell 中执行：

```
E:\2026OPC大赛\flashLoan\plan\runtime_evidence\tools\enable_crash_forensics.cmd
```

该脚本会做：

1. `CrashDumpEnabled = 2`（内核内存转储）
2. `AutoReboot = 0`（不自动重启，留现场）
3. `AlwaysKeepMemoryDump = 1`（不覆盖旧 dump）
4. `MinidumpsCount = 20`
5. `DumpLogLevel = 3`
6. **C 盘页面文件固定为 ≥ 物理内存**（`C:\pagefile.sys 32768 32768`）—— 这是让内核转储真正生效的**必要条件**
7. 三个 WHEA/HardwareEvents 通道**只读校验**（不做写入，因其本就 enabled）

### 6.3 PerfMon 快照（免提权即可跑，但需 Performance Log Users 组）

当前沙箱身份 `QIAN\CodexSandboxOffline` **不在** `Performance Log Users` 组，
`typeperf` 会报 `Error: No valid counters`。
真实用户 `QIAN\cdao` **已在** `Performance Log Users` 与 `Administrators` 组，用该账号运行即可：

```
E:\2026OPC大赛\flashLoan\plan\runtime_evidence\tools\capture_crash_watch_metrics.cmd
```

采集 `\Memory\Available MBytes`、`\Processor(_Total)\% Processor Time`、`\Paging File(_Total)\% Usage`，间隔 5s，输出 CSV 到 `plan\runtime_evidence\logs\`。

---

## 7. 修正后的 P3 表述（可直接替换原段落）

> **P3 — 下次崩溃能取证（已按实测纠正）**
>
> - `CrashDumpEnabled` 原本就是 `7`（自动内存转储），但 C 盘页面文件为托管 `0 0`，
>   且内核转储要求系统盘页面文件 ≥ 物理内存（31.94 GB）→ **必须同时固定 C 盘页面文件**，
>   否则转储开关开了也产不出 dump。需管理员。
> - `AutoReboot` 默认为 `1`，需改为 `0` 以留现场。需管理员。
> - WHEA / HardwareEvents 三个日志通道**本来就全部 enabled**，本次只做只读校验，不重复创建。
>   原计划所写 `HardwareEvents-Platform` 路径在 Windows 10 不存在，真实通道是 `HardwareEvents`。
> - **本次 5 次崩溃 `BugcheckCode=0`，不是蓝屏，内核转储对这 5 次无效**；
>   转储的作用是覆盖未来的蓝屏类崩溃，并为 WHEA 之外的其他故障留证。
> - 崩溃的直接触发已确认为 **CPU 级 WHEA 硬件错误**
>   （`WHEA_PROCESSOR_GENERIC_ERROR_SECTION`，Severity=FATAL_ERROR）：
>   2026 年 146 次 Event 41 中 144 次（98.6%）在其前 5.66~10.35 秒（中位 7.71s，标准差 0.83s）
>   必有该记录。已定位到 **CPU 级**，**尚未**定位到具体核心/部件。
> - PerfMon 快照（5s 间隔 CSV）已就绪；需 `Performance Log Users` 组身份，
>   真实用户 `QIAN\cdao` 已在该组。
> - 硬件层面后续：BIOS/微码更新、CPU 电压与温度核查、内存测试（MemTest86 / Windows 内存诊断）、
>   电源与 CPU 供电检查。这些**需要先拿到具体 APIC ID**，目前尚未定位。

---

## 8. 尚未解决 / 下一步

1. **定位具体 CPU 核心与故障部件**：需解出 `CommonProcessorGenericErrorSection` 的
   `ErrorType` / `ApicId` / `CpuNumber`（WHEA 以 3800 字节模板写入，记录中仅前若干字节非零）。
   可行路径：改用 `Get-WinEvent` 读取 WHEA **Errors** 通道的已解码事件（该通道目前为空，
   说明严重错误走的是 Operational 原始 CPER）；或用 WinDbg/!errrec 解析内存转储。
2. **跑一次真实 dump 验证**：修好 C 盘页面文件后，需一次可捕获的 BSOD 才能产出 dump 做离线分析。
3. **硬件排查**：在定位到 APIC ID 前不宜定向更换硬件；建议先做 BIOS 更新 + MemTest86 全量内存测试 +
   满载温度/电压观测。
4. **VS 后台下载**：计划任务仍 Enabled（当天触发 7 次、累计 1,392.45 GB），需另行以管理员关闭
   （见 `tools/disable_vs_background_download.cmd`），这是降低崩溃**频率**的独立动作。
