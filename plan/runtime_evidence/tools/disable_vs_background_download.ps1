<#
.SYNOPSIS
    关闭 Visual Studio Installer 自动后台下载（系统跑死机元凶）。

.DESCRIPTION
    需要【管理员身份】运行。本脚本封装了全部三类关闭手段：
      1) 注册表策略：HKLM\SOFTWARE\Policies\Microsoft\VisualStudio\Setup
         HKLM\SOFTWARE\Microsoft\VisualStudio\Setup
         HKLM\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\Setup
         -> BackgroundDownloadDisabled = 1 (DWORD)
         -> BackgroundDownload        = 0 (DWORD)
         -> AutoUpdateDisabled        = 1 (DWORD)
      2) 策略配置文件：
         C:\ProgramData\Microsoft\VisualStudio\updates.config
         C:\Program Files (x86)\Microsoft Visual Studio\Shared\updates.config
      3) 计划任务：禁用 \Microsoft\VisualStudio\Updates\BackgroundDownload
         并结束 BackgroundDownload.exe / 停止 VSInstallerElevationService

    证据：2026-11-22 当天该后台下载触发 6 次（03:46 / 07:21 / 08:22 x2 / 08:30 x2），
    三个 VS 实例累计下载 1.31 TB + 64.8 GB，与两次系统崩溃时间点吻合。

.EXAMPLE
    # 自提升执行
    powershell -ExecutionPolicy Bypass -File disable_vs_background_download.ps1

.EXAMPLE
    # 只做只读体检（不需要管理员）
    powershell -ExecutionPolicy Bypass -File disable_vs_background_download.ps1 -Verify
#>
[CmdletBinding()]
param(
    [switch]$Verify,
    [switch]$NoElevate
)

$ErrorActionPreference = 'Continue'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $Verify -and -not (Test-Admin)) {
    if ($NoElevate) {
        Write-Host '[FAIL] 需要管理员权限。' -ForegroundColor Red
        exit 2
    }
    Write-Host '[*] 正在请求管理员权限（UAC 自提升）...' -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($NoElevate) { $argList += '-NoElevate' }
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList -WindowStyle Hidden
        exit 0
    } catch {
        Write-Host "[FAIL] UAC 自提升失败：$($_.Exception.Message)" -ForegroundColor Red
        exit 2
    }
}

$taskPath = '\Microsoft\VisualStudio\Updates\'
$taskName = 'BackgroundDownload'
$regKeys = @(
    'HKLM:\SOFTWARE\Policies\Microsoft\VisualStudio\Setup',
    'HKLM:\SOFTWARE\Microsoft\VisualStudio\Setup',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\Setup'
)
$updatesConfig = 'C:\ProgramData\Microsoft\VisualStudio\updates.config'
$sharedConfig  = 'C:\Program Files (x86)\Microsoft Visual Studio\Shared\updates.config'
$payload = [ordered]@{
    BackgroundDownloadDisabled = '1'
    BackgroundDownload        = '0'
    AutoUpdateDisabled        = '1'
    RemoveOos                = '0'
    KeepDownloadedPayloads    = '0'
}

function Show-Status {
    Write-Host ('=' * 72)
    Write-Host 'VS Installer 后台下载状态'
    Write-Host ('=' * 72)
    Write-Host "管理员权限: $(Test-Admin)"
    Write-Host ''
    Write-Host '注册表策略:'
    foreach ($k in $regKeys) {
        Write-Host "  $k"
        foreach ($v in @('BackgroundDownloadDisabled','BackgroundDownload','AutoUpdateDisabled')) {
            $val = $null
            if (Test-Path $k) { $val = (Get-ItemProperty -Path $k -Name $v -ErrorAction SilentlyContinue).$v }
            if ($null -eq $val) {
                Write-Host "      $v = (未设置)  <-- 未设置"
            } else {
                Write-Host "      $v = $val"
            }
        }
    }
    Write-Host ''
    Write-Host '策略配置文件:'
    foreach ($c in @($updatesConfig, $sharedConfig)) {
        Write-Host "  $c  exists=$(Test-Path $c)"
        if (Test-Path $c) { Write-Host "      $((Get-Content $c -Raw).Trim())" }
    }
    Write-Host ''
    Write-Host '计划任务:'
    schtasks /Query /TN "$taskPath$taskName" /V /FO LIST 2>&1 |
        Select-String -Pattern 'Scheduled Task State|Last Run Time|Last Result' |
        ForEach-Object { Write-Host "      $($_.Line.Trim())" }
}

if ($Verify) {
    Show-Status
    exit 0
}

$failed = 0

Write-Host '[1/4] 写入注册表策略...' -ForegroundColor Cyan
foreach ($k in $regKeys) {
    if (-not (Test-Path $k)) {
        try { New-Item -Path $k -Force -ErrorAction Stop | Out-Null }
        catch { Write-Host "  [FAIL] 无法创建 $k : $($_.Exception.Message)" -ForegroundColor Red; $failed++; continue }
    }
    foreach ($v in @('BackgroundDownloadDisabled','BackgroundDownload','AutoUpdateDisabled')) {
        $d = if ($v -eq 'BackgroundDownload') { 0 } else { 1 }
        try {
            New-ItemProperty -Path $k -Name $v -Value $d -PropertyType DWord -Force -ErrorAction Stop | Out-Null
            Write-Host "  [OK] $k\$v = $d"
        } catch {
            Write-Host "  [FAIL] $k\$v : $($_.Exception.Message)" -ForegroundColor Red
            $failed++
        }
    }
}

Write-Host '[2/4] 写入策略配置文件...' -ForegroundColor Cyan
$json = ($payload | ConvertTo-Json -Depth 3)
foreach ($c in @($updatesConfig, $sharedConfig)) {
    try {
        $dir = Split-Path $c -Parent
        if (-not (Test-Path $dir)) { New-Item -Path $dir -Force -ErrorAction Stop | Out-Null }
        Set-Content -Path $c -Value $json -Encoding UTF8 -Force -ErrorAction Stop
        Write-Host "  [OK] $c"
    } catch {
        Write-Host "  [FAIL] $c : $($_.Exception.Message)" -ForegroundColor Red
        $failed++
    }
}

Write-Host '[3/4] 禁用计划任务...' -ForegroundColor Cyan
try {
    Disable-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction Stop | Out-Null
    Write-Host "  [OK] $taskPath$taskName 已禁用"
} catch {
    Write-Host "  [FAIL] 禁用任务: $($_.Exception.Message)" -ForegroundColor Red
    $failed++
}

Write-Host '[4/4] 结束后台下载进程与提权服务...' -ForegroundColor Cyan
try { Stop-Process -Name 'BackgroundDownload' -Force -ErrorAction SilentlyContinue; Write-Host '  [OK] BackgroundDownload 已结束' } catch {}
try { Stop-Service -Name 'VSInstallerElevationService' -Force -ErrorAction SilentlyContinue; Write-Host '  [OK] VSInstallerElevationService 已停止' } catch {}

Write-Host ''
Show-Status

if ($failed -gt 0) {
    Write-Host "[DONE] 完成，但有 $failed 项失败。" -ForegroundColor Yellow
    exit 1
}
Write-Host '[DONE] 全部成功，VS Installer 后台下载已关闭。' -ForegroundColor Green
exit 0
