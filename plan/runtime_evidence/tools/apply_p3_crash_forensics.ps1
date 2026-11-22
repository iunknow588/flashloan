<#
.SYNOPSIS
    P3 崩溃取证 - 一键管理员执行器（把三件事合并到一次管理员会话）。

.DESCRIPTION
    本脚本按顺序完成：

      步骤 1  关闭 VS Installer 自动后台下载（崩溃「频率诱因」）
              -> disable_vs_background_download.ps1
      步骤 2  写入崩溃取证开关 + 固定 C 盘页面文件（崩溃「直接触发」取证）
              -> enable_crash_forensics.ps1
      步骤 3  写入后只读复核
              -> enable_crash_forensics.ps1 -Verify

    步骤 1 与 2 需要管理员权限。步骤 3 为只读。

.PARAMETER SkipVsFix
    跳过步骤 1（仅做崩溃取证开关）。

.PARAMETER SkipPageFile
    不修改 C 盘页面文件（仅改 CrashControl 开关）。
    注意：不固定页面文件时，内核转储**大概率仍不会产出 dump**。

.EXAMPLE
    .\apply_p3_crash_forensics.ps1

.EXAMPLE
    .\apply_p3_crash_forensics.ps1 -SkipVsFix
#>
[CmdletBinding()]
param(
    [switch]$SkipVsFix,
    [switch]$SkipPageFile
)

$ErrorActionPreference = 'Continue'
$toolDir = $PSScriptRoot

function Write-Head([string]$t) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Head '权限不足'
    Write-Host '  请改用 apply_p3_crash_forensics.cmd （会自行请求提权）。' -ForegroundColor Yellow
    exit 2
}

$failures = @()

# ---------------------------------------------------------------- 步骤 1
if (-not $SkipVsFix) {
    Write-Head '步骤 1 / 3  关闭 VS Installer 自动后台下载（频率诱因）'
    $vs = Join-Path $toolDir 'disable_vs_background_download.ps1'
    if (Test-Path $vs) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $vs
        Write-Host ("  退出码: {0}" -f $LASTEXITCODE)
        if ($LASTEXITCODE -ne 0) { $failures += '步骤1 退出码 ' + $LASTEXITCODE }
    } else {
        Write-Host ("  [!!] 找不到 {0}" -f $vs) -ForegroundColor Red
        $failures += '步骤1 脚本缺失'
    }
} else {
    Write-Head '步骤 1 / 3  跳过（-SkipVsFix）'
}

# ---------------------------------------------------------------- 步骤 2
Write-Head '步骤 2 / 3  写入崩溃取证开关（直接触发取证）'
$cf = Join-Path $toolDir 'enable_crash_forensics.ps1'
if (-not (Test-Path $cf)) {
    Write-Host ("  [!!] 找不到 {0}" -f $cf) -ForegroundColor Red
    exit 1
}
$cfArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $cf)
if ($SkipPageFile) {
    Write-Host '  [-SkipPageFile] 不修改 C 盘页面文件（注意：内核转储可能仍不产出 dump）' -ForegroundColor Yellow
} else {
    $cfArgs += @('-SkipPageFile')
}
& powershell @cfArgs
Write-Host ("  退出码: {0}" -f $LASTEXITCODE)
if ($LASTEXITCODE -ne 0) { $failures += '步骤2 退出码 ' + $LASTEXITCODE }

# ---------------------------------------------------------------- 步骤 3
Write-Head '步骤 3 / 3  写入后只读复核'
& powershell -NoProfile -ExecutionPolicy Bypass -File $cf -Verify
Write-Host ("  退出码: {0}" -f $LASTEXITCODE)

# ---------------------------------------------------------------- 汇总
Write-Head '汇总'
if ($failures.Count -eq 0) {
    Write-Host '  [OK] 全部步骤已完成。' -ForegroundColor Green
    Write-Host ''
    Write-Host '  剩余的人工动作：' -ForegroundColor Cyan
    Write-Host '   1) 重启一次，使 C 盘页面文件配置生效（页面文件改动需重启）。' -ForegroundColor Yellow
    Write-Host '   2) 重启后跑 enable_crash_forensics.ps1 -Verify，确认 PagingFiles 已生效。' -ForegroundColor Yellow
    Write-Host '   3) 以 QIAN\cdao 身份运行 capture_crash_watch_metrics.cmd（PerfMon CSV，5s 间隔）。' -ForegroundColor Yellow
    Write-Host '   4) 静置 >= 30 分钟，复核 state.json 的 updateDownloadedSize 不再增长。' -ForegroundColor Yellow
    exit 0
} else {
    Write-Host '  [!!] 以下步骤未成功：' -ForegroundColor Red
    foreach ($f in $failures) { Write-Host ("    - {0}" -f $f) -ForegroundColor Red }
    exit 1
}
