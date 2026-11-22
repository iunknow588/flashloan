@echo off
REM ===================================================================
REM  capture_crash_watch_metrics.cmd
REM  崩溃前性能快照采集器（PerfMon / typeperf -> CSV，间隔 5s）
REM
REM  采集计数器：
REM    \Memory\Available MBytes
REM    \Processor(_Total)\% Processor Time
REM    \Paging File(_Total)\% Usage
REM
REM  需要「Performance Log Users」组成员身份，或管理员。
REM  实测本机 QIAN\cdao 在该组，可直接运行。
REM
REM  用法：
REM    capture_crash_watch_metrics.cmd                    一直采集 (Ctrl+C 停止)
REM    capture_crash_watch_metrics.cmd 60 720             运行 60 分钟 / 最多 720 样本
REM ===================================================================
setlocal
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%capture_crash_watch_metrics.ps1"

set "DURATION=0"
set "MAXSAMPLES=0"
if not "%~1"=="" set "DURATION=%~1"
if not "%~2"=="" set "MAXSAMPLES=%~2"

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -DurationMinutes %DURATION% -MaxSamples %MAXSAMPLES%
if %errorlevel%==3 (
    echo.
    echo   [!] 缺少性能计数器读取权限。请用 Performance Log Users 组成员账号运行，
    echo       或由管理员执行：net localgroup "Performance Log Users" "%%USERDOMAIN%%%%USERNAME%%" /add
    pause
    exit /b 3
)
pause
exit /b 0
