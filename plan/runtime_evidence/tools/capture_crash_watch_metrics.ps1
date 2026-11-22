<#
.SYNOPSIS
    崩溃前性能快照采集器（PerfMon / typeperf -> CSV）。

.DESCRIPTION
    持续采集以下计数器，间隔 5 秒，写入 CSV：

      \Memory\Available MBytes
      \Processor(_Total)\% Processor Time
      \Paging File(_Total)\% Usage

    用途：在下一次硬断电/强制复位（Event 41, BugcheckCode=0）之前，
    留下内存/CPU/页面文件的连续读数，用于区分「内存耗尽」与「CPU 硬件错误」。

    重要：typeperf 需要「Performance Log Users」组成员身份，或在管理员命令行下运行。
    实测本机：
      - QIAN\cdao            在 Performance Log Users 与 Administrators 组 -> 可直接运行
      - QIAN\CodexSandboxOffline  不在该组 -> typeperf 报 "Error: No valid counters"

    若报该错误，请用真实用户账号运行，或执行：
      net localgroup "Performance Log Users" <你的账号> /add

.PARAMETER IntervalSeconds
    采样间隔秒数，默认 5。

.PARAMETER OutputDir
    CSV 输出目录，默认 <repo>/plan/runtime_evidence/logs。

.PARAMETER DurationMinutes
    最长运行分钟数，0 表示一直运行（默认 0，需 Ctrl+C 停止）。

.PARAMETER MaxSamples
    最大采样数，0 表示不限（默认 0）。

.EXAMPLE
    .\capture_crash_watch_metrics.ps1

.EXAMPLE
    .\capture_crash_watch_metrics.ps1 -DurationMinutes 60 -MaxSamples 720
#>
[CmdletBinding()]
param(
    [int]$IntervalSeconds = 5,
    [string]$OutputDir = '',
    [int]$DurationMinutes = 0,
    [int]$MaxSamples = 0
)

$ErrorActionPreference = 'Stop'

$counters = @(
    '\Memory\Available MBytes'
    '\Processor(_Total)\% Processor Time'
    '\Paging File(_Total)\% Usage'
)

if (-not $OutputDir) {
    $scriptRoot = Split-Path -Parent $PSScriptRoot
    $OutputDir = Join-Path $scriptRoot 'logs'
}
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$csvPath = Join-Path $OutputDir "crash_watch_metrics_$stamp.csv"

Write-Host ''
Write-Host ('=' * 72) -ForegroundColor DarkCyan
Write-Host '  崩溃前性能快照采集器' -ForegroundColor Cyan
Write-Host ('=' * 72) -ForegroundColor DarkCyan
Write-Host "  间隔       : $IntervalSeconds 秒"
Write-Host "  输出       : $csvPath"
Write-Host "  计数器     :"
foreach ($c in $counters) { Write-Host "              $c" }
Write-Host "  运行时长   : $(if ($DurationMinutes -gt 0) { "$DurationMinutes 分钟" } else { '不限 (Ctrl+C 停止)' })"
Write-Host "  最大样本数 : $(if ($MaxSamples -gt 0) { $MaxSamples } else { '不限' })"
Write-Host ''

# --- 权限预检：typeperf 需要 Performance Log Users 或管理员 ---
function Test-CounterAccess {
    $probe = & typeperf '\Memory\Available MBytes' -sc 1 2>&1
    return -not ($probe -match 'No valid counters')
}
if (-not (Test-CounterAccess)) {
    Write-Host '  [!] typeperf 无法访问性能计数器。' -ForegroundColor Red
    Write-Host '      原因：当前身份不在「Performance Log Users」组，且非管理员。' -ForegroundColor Yellow
    Write-Host '      解决（管理员 PowerShell）：' -ForegroundColor Yellow
    Write-Host '          net localgroup "Performance Log Users" "$env:USERDOMAIN\$env:USERNAME" /add' -ForegroundColor Yellow
    Write-Host '      然后重新登录，再运行本脚本。' -ForegroundColor Yellow
    exit 3
}
Write-Host '  [OK] 性能计数器可访问。' -ForegroundColor Green
Write-Host ''

# --- 后台运行 typeperf ---
$argList = @('-f', 'CSV', '-si', "$IntervalSeconds") + $counters
if ($MaxSamples -gt 0) { $argList += @('-sc', "$MaxSamples") }
$argList += @('-o', $csvPath)

Write-Host "  启动 typeperf ..."
$proc = Start-Process -FilePath 'typeperf.exe' -ArgumentList $argList -PassThru -NoNewWindow
Write-Host "  typeperf PID = $($proc.Id)"
Write-Host ''
Write-Host '  采集中。按 Ctrl+C 停止。' -ForegroundColor Yellow
Write-Host ''

$endAt = if ($DurationMinutes -gt 0) { (Get-Date).AddMinutes($DurationMinutes) } else { $null }
try {
    while (-not $proc.HasExited) {
        if ($endAt -and (Get-Date) -gt $endAt) {
            Write-Host "  达到 $DurationMinutes 分钟上限，停止采集..."
            try { $proc.Kill() } catch { }
            break
        }
        Start-Sleep -Seconds 5
    }
} catch {
    Write-Host "  收到中断信号，停止采集..."
    try { $proc.Kill() } catch { }
}

Start-Sleep -Seconds 2
Write-Host ''
Write-Host ('=' * 72) -ForegroundColor DarkCyan
Write-Host '  采集结束' -ForegroundColor Cyan
Write-Host ('=' * 72) -ForegroundColor DarkCyan
if (Test-Path $csvPath) {
    $lines = (Get-Content $csvPath -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
    Write-Host "  CSV      : $csvPath"
    Write-Host "  行数     : $lines"
    Write-Host "  文件大小 : $([math]::Round((Get-Item $csvPath).Length / 1KB, 1)) KB"
} else {
    Write-Host "  [!] 未生成 CSV（typeperf 可能未获得计数器权限）" -ForegroundColor Red
}
Write-Host ''
exit 0
