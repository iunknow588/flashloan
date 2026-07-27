@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0get.ps1" %*
