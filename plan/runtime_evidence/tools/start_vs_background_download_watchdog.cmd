@echo off
REM VS Installer ??????? ?? ???????
setlocal
set LOGDIR=%~dp0..\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOG=%LOGDIR%\vs_bg_watchdog_stdout.log
start "VS-BG-Watchdog" /B python.exe -u "%~dp0vs_background_download_watchdog.py" --interval 20 >> "%LOG%" 2>&1
echo VS-BG-Watchdog started, log: %LOG%
