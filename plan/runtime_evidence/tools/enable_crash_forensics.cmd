@echo off
REM ===================================================================
REM  enable_crash_forensics.cmd
REM  启用崩溃取证开关（CrashDumpEnabled=2 / AutoReboot=0 / C盘页面文件固定）
REM
REM  需要管理员权限。本脚本会自行请求提权（UAC）。
REM  仅做只读体检（不写入任何注册表）请加 -Verify 参数。
REM
REM  用法：
REM    enable_crash_forensics.cmd            写入开关（请求提权）
REM    enable_crash_forensics.cmd -Verify    只读体检（不需要提权）
REM ===================================================================
setlocal
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%enable_crash_forensics.ps1"

if /i "%~1"=="-Verify" goto :verify

REM --- 需要提权 ---
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
echo   [管理员] 正在写入崩溃取证开关...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
    echo   [OK] 已写入。请重启一次使 C 盘页面文件配置生效。
) else (
    echo   [!] 写入失败，退出码 %RC%。
)
pause
exit /b %RC%

:run_admin
:verify
echo.
echo   只读体检模式，不写入任何注册表...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Verify
pause
exit /b 0
