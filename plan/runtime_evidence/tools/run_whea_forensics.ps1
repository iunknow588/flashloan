<#
.SYNOPSIS
    WHEA 崩溃取证流水线：一键重跑「CPU 级 WHEA 硬件错误 -> 崩溃」的全部证据。

.DESCRIPTION
    步骤：
      1. wevtutil qe 导出 WHEA Operational（EventID=42）与 System（EventID=41/6008）为 XML
         注意：必须用 qe /f:xml（epl 会导出为二进制 EVTX，扩展名 .xml 也不管用）
      2. extract_whea_raw.py   -> 抽 RawData（UTF-16LE 带 BOM，必须按 utf-16 解码）
      3. decode_whea_cper.py   -> 解 CPER section（识别 WHEA_PROCESSOR_GENERIC_ERROR_SECTION）
      4. correlate_whea_crashes.py -> Event41 <-> CPU WHEA 配对
      5. 打印 60s 窗口统计（匹配率 / delta 分布 / 按月分布）

    全部为只读操作，不需要管理员权限。

.PARAMETER WorkDir
    中间文件目录，默认 <repo>/plan/runtime_evidence/logs/whea_forensics。

.EXAMPLE
    .\run_whea_forensics.ps1

.EXAMPLE
    .\run_whea_forensics.ps1 -WorkDir D:\whea
#>
[CmdletBinding()]
param(
    [string]$WorkDir = ''
)

$ErrorActionPreference = 'Stop'
$toolDir = $PSScriptRoot

if (-not $WorkDir) {
    # $toolDir = <repo>/plan/runtime_evidence/tools  ->  <repo>/plan/runtime_evidence/logs/whea_forensics
    $runtimeEvidence = Split-Path -Parent $toolDir
    $WorkDir = Join-Path $runtimeEvidence 'logs\whea_forensics'
}
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null }

function Write-Head([string]$t) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
}

$wheaXml  = Join-Path $WorkDir 'whea_operational.xml'
$sysXml   = Join-Path $WorkDir 'system.xml'
$rawJson  = Join-Path $WorkDir 'whea_raw.json'
$decJson  = Join-Path $WorkDir 'whea_decoded.json'
$corJson  = Join-Path $WorkDir 'whea_crash_correlation.json'

Write-Head '步骤 1 / 5  导出事件日志（只读）'
Write-Host "  wevtutil qe Microsoft-Windows-Kernel-WHEA/Operational /q:*[System[(EventID=42)]] /f:xml"
& wevtutil qe Microsoft-Windows-Kernel-WHEA/Operational '/q:*[System[(EventID=42)]]' '/f:xml' '/rd:true' '/c:100000' > $wheaXml
if ($LASTEXITCODE -ne 0) { throw "导出 WHEA Operational 失败（exit=$LASTEXITCODE）" }
Write-Host ("  -> {0}  ({1:N1} MB)" -f $wheaXml, ((Get-Item $wheaXml).Length / 1MB))

Write-Host "  wevtutil qe System /q:*[System[(EventID=41 or EventID=6008)]] /f:xml"
& wevtutil qe System '/q:*[System[(EventID=41 or EventID=6008)]]' '/f:xml' '/rd:true' '/c:100000' > $sysXml
if ($LASTEXITCODE -ne 0) { throw "导出 System 失败（exit=$LASTEXITCODE）" }
Write-Host ("  -> {0}  ({1:N1} MB)" -f $sysXml, ((Get-Item $sysXml).Length / 1MB))

Write-Head '步骤 2 / 5  抽取 WHEA RawData'
& python (Join-Path $toolDir 'extract_whea_raw.py') $wheaXml $rawJson
if ($LASTEXITCODE -ne 0) { throw "extract_whea_raw.py 失败" }

Write-Head '步骤 3 / 5  解码 CPER section'
& python (Join-Path $toolDir 'decode_whea_cper.py') $rawJson $decJson
if ($LASTEXITCODE -ne 0) { throw "decode_whea_cper.py 失败" }

Write-Head '步骤 4 / 5  Event41 <-> CPU WHEA 配对'
& python (Join-Path $toolDir 'correlate_whea_crashes.py') $sysXml $decJson $corJson | Select-Object -Last 6
if ($LASTEXITCODE -ne 0) { throw "correlate_whea_crashes.py 失败" }

Write-Head '步骤 5 / 5  统计摘要（60s 窗口）'
& python (Join-Path $toolDir 'summarize_whea_correlation.py') $corJson
if ($LASTEXITCODE -ne 0) { throw "summarize_whea_correlation.py 失败" }

Write-Host ''
Write-Host '  产物目录：' -ForegroundColor Cyan
Write-Host "    $WorkDir"
Write-Host ''
Write-Output ''
exit 0
