@echo off
REM ===================================================================
REM  apply_p3_crash_forensics.cmd
REM  P3 崩溃取证 - 一键管理员执行器
REM
REM  一次管理员会话内完成：
REM    步骤 1  关闭 VS Installer 自动后台下载（频率诱因）
REM    步骤 2  写入崩溃取证开关 + 固定 C 盘页面文件（直接触发取证）
REM    步骤 3  写入后只读复核
REM
REM  需要管理员权限。本脚本会自行请求提权（UAC）。
REM
REM  用法：
REM    apply_p3_crash_forensics.cmd              全部三步
REM    apply_p3_crash_forensics.cmd -SkipVsFix   只做崩溃取证开关
REM    apply_p3_crash_forensics.cmd -SkipPageFile 不改页面文件（注意：可能仍无 dump）
REM ===================================================================
setlocal
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%apply_p3_crash_forensics.ps1"

net session >nul 2>&1
if %errorlevel%==0 goto :run_admin

echo.
echo   需要管理员权限，正在请求提权...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%ComSpec%' -ArgumentList '/c','\"\"%~f0\"\" /elevated\"' -Verb RunAs"
if %errorlevel% neq 0 (
    echo   [!] 提权失败，请手动以管理员身份运行本脚本。
    pause
    exit /b 1
)
exit /b 0

:elevated
echo.
echo   [管理员] 正在执行 P3 崩溃取证一键流程...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
    echo   [OK] 已完成。请按脚本提示重启一次。
) else (
    echo   [!] 执行失败，退出码 %RC%。
)
pause
exit /b %RC%

:run_admin
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%errorlevel%"
pause
exit /b %RC%
