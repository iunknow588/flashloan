# 已完成任务归档 —— Uniswap Trading API Key 接入与 USDG 估值实测

**归档日期**：2026-11-12
**批次**：VAL-2 前置（实盘估值连通性验证）
**状态**：✅ 已完成
**生产写入**：**0 次**（全程只读探测）

---

## 1. 背景

`token-manager` 的 `value-tokens` 子命令依赖 Uniswap Trading API
`POST https://trade-api.gateway.uniswap.org/v1/quote`，此前因缺少 API Key
无法区分"**真的没有兑换路径**"与"**根本没发问**"——
这使得股票池的 USDG 经济门禁（VAL-4）无法产出可用证据。

## 2. 已完成工作

### 2.1 Skill 安装

```
npx skills add uniswap/uniswap-ai --skill swap-integration
```

- 安装位置：`E:\2026OPC大赛\flashLoan\.agents\skills\swap-integration\`
- 内容：`SKILL.md`（67 KB / 1719 行，v1.6.0）+ `references/advanced-patterns.md`
- 安全评估：Gen **Safe** / Socket **0 alerts** / Snyk **Med Risk**
- `.agents/` 已被 `.gitignore` 覆盖，**不进入版本控制**

### 2.2 API Key 配置

| 文件 | 内容 | 是否入库 |
|---|---|---|
| `flashloan/app_runtime/.env` | `UNISWAP_TRADING_API_KEY=<key>` | ❌ gitignored，**密钥零外泄** |
| `flashloan/app_runtime/.env.example` | `UNISWAP_TRADING_API_KEY=`（空占位） | ✅ 仅模板 |

使用**规范名** `UNISWAP_TRADING_API_KEY`（`token-manager` 的读取优先级为
`UNISWAP_TRADING_API_KEY` > `UNISWAP_API_KEY` > `UNISWAP_TRADING_KEY`）。

已通过 `flashloan/src/core/env_loader.py::load_env_files` 验证加载成功。

### 2.3 端到端实测

`token-manager value-tokens` 真实调用链 4663：

| 指标 | 结果 |
|---|---|
| 样本 | 60 只股票代币 |
| `route_found` | **45（75%）** |
| `api_error` | 15（HTTP 404 = 无路径） |
| 估值 p25 / median / p75 / max | 29.89 / **76.72** / 223.74 / 1030.32 USDG |
| ≥ 10 USDG 的代币 | 39 / 45 |
| 价格冲击中位 | 5.55 bps |

双向报价均可用（token→USDG 与 USDG→token）。实盘价格合理：
SPY ≈ $758、MSTR ≈ $119、CRCL ≈ $65.8、WETH ≈ $2051。
Gas 估算均 < $0.02。

### 2.4 证据

- `logs/plan/runtime_evidence/reports/uniswap_usdg_valuation_probe_20261112.json`
  （原始报告）
- `logs/plan/runtime_evidence/reports/uniswap_usdg_valuation_summary_20261112.json`
  （汇总 + 分位统计 + 逐代币明细）

## 3. 遗留缺陷（已登记为 `VAL-4-13`，P1）

Trading API 对链 4663 上**没有兑换路径**的代币返回
**HTTP 404** + `{"errorCode":"NoRouteFoundError"}`，
但 `token-manager` 当前把**所有非 200** 一律归为 `api_error`。

**后果**：真负面被误分类为未知。实测 **15/60（25%）** 受影响。
若不先修就跑 194 只全量，产出的 `unknownStockCount` 里有 1/4
其实是**确认无路径**，会直接污染股票池成员关系门禁。

**修法**：按 `errorCode` 细分——
`NoRouteFoundError` → `no_route`；其余非 200 → `api_error`。

## 4. 连带完成的其他工作

| 项 | 说明 |
|---|---|
| `file-io` Skill | 自建 Windows 安全文件读写工具（UTF-8/LF/无 BOM 默认，兼容多格式），50/50 自测通过 |
| `.env` 编辑方式 | 改用 `file_io.py`，精确保留原文件 **UTF-8-BOM + CRLF** 约定，未引入格式漂移 |
| `.gitignore` | 新增 `.tmp_fileio_selftest/` |

## 5. 对计划的影响

- `VAL-4-7` 状态：`⏳ 阻塞于 VAL-2` → `✅ Key 已就位`（剩余：全量跑 + 落库）
- 新增 `VAL-4-13`（P1），并**前置**到 `VAL-4-7` 之前
- `VAL-4-B` 从 `⏸ 阻塞中` → `⬜ 可推进`
- §7 执行顺序新增 **VAL / ARCH 两条并行轨道**（不需授权，可与 W6–W8 同时推进）
