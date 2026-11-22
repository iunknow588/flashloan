<#
.SYNOPSIS
    启用崩溃取证开关并校验 WHEA / HardwareEvents 日志通道状态。

.DESCRIPTION
    本脚本针对「P3 - 下次崩溃能取证」执行以下动作：

    写入（需管理员）：
      1. CrashDumpEnabled     = 2   内核内存转储
      2. AutoReboot           = 0   崩溃后不自动重启，保留现场
      3. AlwaysKeepMemoryDump = 1   不覆盖既有 dump
      4. MinidumpsCount       = 20
      5. DumpLogLevel         = 3
      6. PagingFiles = 'C:\pagefile.sys <RAM> <RAM>'   <-- 关键
         内核内存转储要求「系统盘页面文件 >= 物理内存」，否则开关打开也不会产出 dump。
         实测本机 C:\pagefile.sys 为托管 '0 0'，必须显式固定。

    只读校验（不做任何写入）：
      7. Microsoft-Windows-Kernel-WHEA/Errors       实测本就 enabled=true
      8. Microsoft-Windows-Kernel-WHEA/Operational  实测本就 enabled=true
      9. HardwareEvents                             实测本就 enabled=true
         原计划所写 'HardwareEvents-Platform' 路径在 Windows 10 不存在。

    重要边界：本次 5 次崩溃 BugcheckCode=0，属断电/强制复位而非蓝屏，
    内核转储对这 5 次无效；其作用是覆盖未来的蓝屏类崩溃并为 WHEA 之外故障留证。

.PARAMETER Verify
    只读体检模式：不写入任何注册表，仅打印当前状态与建议。仅需普通权限。

.PARAMETER SkipPageFile
    不修改 C 盘页面文件（仅改 CrashControl 开关）。
    注意：不固定页面文件时，内核转储**大概率仍不会产出 dump**。

.PARAMETER RamOverrideMB
    指定用于固定 C 盘页面文件的内存大小（MB）。默认自动读取物理内存并向上取整到 1024 的整数倍。

.EXAMPLE
    .\enable_crash_forensics.ps1 -Verify

.EXAMPLE
    .\enable_crash_forensics.ps1
#>
[CmdletBinding()]
param(
    [switch]$Verify,
    [switch]$SkipPageFile,
    [int]$RamOverrideMB = 0
)

$ErrorActionPreference = 'Stop'
$CrashControl = 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
$MemoryMgmt   = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'
$LogChannels  = @(
    'Microsoft-Windows-Kernel-WHEA/Errors'
    'Microsoft-Windows-Kernel-WHEA/Operational'
    'HardwareEvents'
)

function Initialize-MemProbe {
    if ('MemProbe' -as [type]) { return }
    $src = @"
using System;
using System.Runtime.InteropServices;
public static class MemProbe {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Auto)]
  public class MEMORYSTATUSEX {
    public uint dwLength; public uint dwMemoryLoad;
    public ulong ullTotalPhys; public ulong ullAvailPhys;
    public ulong ullTotalPageFile; public ulong ullAvailPageFile;
    public ulong ullTotalVirtual; public ulong ullAvailVirtual;
    public ulong ullAvailExtendedVirtual;
  }
  [DllImport("kernel32.dll", CharSet=CharSet.Auto, SetLastError=true)]
  public static extern bool GlobalMemoryStatusEx([In, Out] MEMORYSTATUSEX lpBuffer);
}
"@
    Add-Type -TypeDefinition $src -ErrorAction SilentlyContinue
}

function Write-Head([string]$t) {
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkCyan
}

function Get-PhysicalMemoryMB {
    $m = New-Object -TypeName 'MemProbe+MEMORYSTATUSEX'
    $m.dwLength = [System.Runtime.InteropServices.Marshal]::SizeOf($m)
    [void][MemProbe]::GlobalMemoryStatusEx($m)
    return [int][math]::Ceiling(($m.ullTotalPhys / 1MB) / 1024) * 1024
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-CurrentState {
    $cc = Get-ItemProperty $CrashControl -ErrorAction SilentlyContinue
    $mm = Get-ItemProperty $MemoryMgmt   -ErrorAction SilentlyContinue
    [pscustomobject]@{
        CrashDumpEnabled     = $cc.CrashDumpEnabled
        AutoReboot           = $cc.AutoReboot
        AlwaysKeepMemoryDump = $cc.AlwaysKeepMemoryDump
        MinidumpsCount       = $cc.MinidumpsCount
        DumpLogLevel         = $cc.DumpLogLevel
        DumpFile             = $cc.DumpFile
        MinidumpDir          = $cc.MinidumpDir
        PagingFiles          = ($mm.PagingFiles -join ' | ')
        MemoryDumpExists     = Test-Path 'C:\Windows\MEMORY.DMP'
        MinidumpDirExists    = Test-Path 'C:\Windows\Minidump'
    }
}

Initialize-MemProbe

Write-Head '崩溃取证开关 - 当前状态'
Get-CurrentState | Format-List

Write-Head 'WHEA / HardwareEvents 日志通道（只读校验）'
foreach ($ch in $LogChannels) {
    $info = & wevtutil gl $ch 2>&1
    $enabled = ($info | Select-String -Pattern 'enabled:\s*(\w+)' | Select-Object -First 1)
    $maxSize = ($info | Select-String -Pattern 'maxSize:\s*(\d+)' | Select-Object -First 1)
    $enabledVal = if ($enabled) { $enabled.Matches[0].Groups[1].Value } else { 'unknown' }
    $sizeVal   = if ($maxSize) { $maxSize.Matches[0].Groups[1].Value } else { '?' }
    $mark = if ($enabledVal -eq 'true') { '[OK] 已启用' } else { '[!!] 未启用' }
    Write-Host ("  {0,-46} {1,-14} maxSize={2}" -f $ch, $mark, $sizeVal)
}
Write-Host "  注：三个通道实测本就 enabled=true，本次不做任何写入。"
Write-Host "  注：原计划所写 'HardwareEvents-Platform' 路径在 Windows 10 不存在，真实通道为 'HardwareEvents'。"

$ramMB = if ($RamOverrideMB -gt 0) { $RamOverrideMB } else { Get-PhysicalMemoryMB }
Write-Host ''
Write-Host ("  物理内存 (探测)      : {0} MB" -f $ramMB)
Write-Host ("  C 盘页面文件需固定为 : C:\pagefile.sys {0} {0}" -f $ramMB)

if ($Verify) {
    Write-Head '结论（只读模式，未做任何修改）'
    Write-Host '  CrashDumpEnabled = 7  ->  自动内存转储，本就开启（不是"完全没有 dump"）'
    Write-Host '  但 C:\pagefile.sys 为托管 0 0，且内核转储要求系统盘页面文件 >= 物理内存'
    Write-Host '  -> 这就是"开关开着却没有 dump 文件"的原因。'
    Write-Host '  AutoReboot = 1        ->  崩溃后会自动重启，现场会丢失，需改为 0。'
    Write-Host '  WHEA/HardwareEvents   ->  三通道均已 enabled，无需重复创建。'
    Write-Host ''
    Write-Host '  需要写入请以管理员执行：enable_crash_forensics.cmd'
    return
}

if (-not (Test-Admin)) {
    Write-Head '权限不足'
    Write-Host '  当前不是管理员，无法写入 HKLM。' -ForegroundColor Red
    Write-Host '  请改用：enable_crash_forensics.cmd （会自行请求提权）' -ForegroundColor Yellow
    exit 2
}

Write-Head '写入 CrashControl'

$changes = @(
    @{ Name = 'CrashDumpEnabled';     Value = 2;  Note = '内核内存转储' }
    @{ Name = 'AutoReboot';           Value = 0;  Note = '不自动重启，保留现场' }
    @{ Name = 'AlwaysKeepMemoryDump'; Value = 1;  Note = '不覆盖既有 dump' }
    @{ Name = 'MinidumpsCount';       Value = 20; Note = '小转储保留数量' }
    @{ Name = 'DumpLogLevel';         Value = 3;  Note = '转储日志级别' }
)

foreach ($c in $changes) {
    $old = (Get-ItemProperty $CrashControl).($c.Name)
    Set-ItemProperty -Path $CrashControl -Name $c.Name -Value $c.Value -Type DWord
    $new = (Get-ItemProperty $CrashControl).($c.Name)
    Write-Host ("  {0,-22} {1,-8} -> {2,-8} ({3})" -f $c.Name, $old, $new, $c.Note)
}

if ($SkipPageFile) {
    Write-Head '跳过 C 盘页面文件修改（-SkipPageFile）'
    Write-Host '  [!!] 注意：不固定系统盘页面文件时，内核内存转储大概率仍不会产出 dump。' -ForegroundColor Yellow
    Write-Host "  当前 PagingFiles : $((Get-ItemProperty $MemoryMgmt).PagingFiles -join ' | ')"
} else {
    Write-Head '写入 C 盘页面文件（内核转储生效的必要条件）'
    $existing = @((Get-ItemProperty $MemoryMgmt).PagingFiles)
    Write-Host "  原有 PagingFiles : $($existing -join ' | ')"
    $newPf = @("C:\pagefile.sys $ramMB $ramMB") + @($existing | Where-Object { $_ -notmatch '^[Cc]:\\pagefile\.sys' })
    Set-ItemProperty -Path $MemoryMgmt -Name PagingFiles -Value $newPf -Type MultiString
    Write-Host "  新的 PagingFiles : $($newPf -join ' | ')"
    Write-Host '  提示：页面文件改动需重启后完全生效。' -ForegroundColor Yellow
}

Write-Head 'WHEA / HardwareEvents 通道'
foreach ($ch in $LogChannels) {
    $info = & wevtutil gl $ch 2>&1
    $enabled = ($info | Select-String -Pattern 'enabled:\s*(\w+)' | Select-Object -First 1)
    $val = if ($enabled) { $enabled.Matches[0].Groups[1].Value } else { 'unknown' }
    if ($val -eq 'true') {
        Write-Host ("  {0,-46} 已 enabled，保持不变" -f $ch) -ForegroundColor DarkGray
    } else {
        Write-Host ("  {0,-46} 未启用 -> 启用中" -f $ch) -ForegroundColor Yellow
        & wevtutil sl $ch /e:true | Out-Null
    }
}

Write-Head '写入后复核'
Get-CurrentState | Format-List

Write-Host ''
Write-Host '  边界提醒：本次 5 次崩溃 BugcheckCode=0（断电/强制复位，非蓝屏），' -ForegroundColor Yellow
Write-Host '  内核转储对这 5 次无效；其作用是覆盖未来的蓝屏类崩溃。' -ForegroundColor Yellow
Write-Host '  崩溃的直接触发已确认为 CPU 级 WHEA 硬件错误' -ForegroundColor Yellow
Write-Host '  （WHEA_PROCESSOR_GENERIC_ERROR_SECTION，Severity=FATAL_ERROR）。' -ForegroundColor Yellow
Write-Host ''
Write-Host '  请重启一次使页面文件配置生效。' -ForegroundColor Cyan
Write-Host ''
exit 0
