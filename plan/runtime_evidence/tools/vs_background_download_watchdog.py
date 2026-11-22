# -*- coding: utf-8 -*-
"""vs_background_download_watchdog.py

VS Installer 后台下载看门狗（无需管理员权限的即时缓解层）。

背景
----
`\\Microsoft\\VisualStudio\\Updates\\BackgroundDownload` 计划任务以 InteractiveToken
身份（cdao）运行 `BackgroundDownload.exe`，其触发条件是「空闲 10 分钟」。
彻底关闭需要管理员写 HKLM/ProgramData（见 disable_vs_background_download.py），
但在拿到管理员权限之前，本看门狗可以在**无需提权**的前提下持续把已经拉起的
下载进程按下去，直接消除「下载完成 -> 自动触发约 500 个 MSI 安装」这条崩溃链路。

它做什么
--------
* 每 N 秒检查一次 `BackgroundDownload.exe`；一旦出现就结束它。
* 记录被拦截的次数与时间戳到 JSONL 证据文件。
* `--once` 只检查一次；`--interval` 自定义轮询间隔（默认 15 秒）。

它**不能**做什么
----------------
* 不能禁用计划任务本身（需要管理员）。
* 不能阻止任务被拉起，只能在拉起后立刻结束进程。

用法
----
    # 常驻（Ctrl+C 退出）
    python vs_background_download_watchdog.py

    # 单次体检
    python vs_background_download_watchdog.py --once

    # 自定义间隔与证据文件
    python vs_background_download_watchdog.py --interval 10 \\
        --evidence plan/runtime_evidence/logs/vs_bg_download_watchdog.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

try:  # 保证重定向到文件时中文不乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from datetime import datetime, timezone
from pathlib import Path

PROCESS_NAME = "BackgroundDownload"
DEFAULT_EVIDENCE = Path(__file__).resolve().parent.parent / "logs" / "vs_bg_download_watchdog.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _enum_processes() -> list[tuple[int, str]]:
    """用 CreateToolhelp32Snapshot 枚举 (pid, exeName)。

    不用 tasklist：在受限沙箱里 tasklist 会直接失败（rc=1，输出为空）。
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid_handle:
        return []
    result: list[tuple[int, str]] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                result.append((int(entry.th32ProcessID), entry.szExeFile))
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def list_downloader_pids() -> list[int]:
    """返回正在运行的 BackgroundDownload.exe 进程 PID 列表（无需管理员）。"""
    target = PROCESS_NAME.lower()
    pids: list[int] = []
    try:
        for pid, name in _enum_processes():
            if name.lower() == f"{target}.exe":
                pids.append(pid)
    except Exception:
        return pids
    return pids


def _terminate_process(pid: int) -> tuple[bool, str]:
    """用 Win32 TerminateProcess 结束进程。

    比 taskkill 更可靠：当进程属于另一个账户时 taskkill 会返回
    "ERROR: Access denied"，而 TerminateProcess 在拥有句柄权限时仍可成功。
    """
    import ctypes

    PROCESS_TERMINATE = 0x0001
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False, f"OpenProcess failed: {ctypes.get_last_error()}"
        try:
            if kernel32.TerminateProcess(handle, 1):
                return True, "TerminateProcess ok"
            return False, f"TerminateProcess failed: {ctypes.get_last_error()}"
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:
        return False, str(exc)


def kill_pid(pid: int) -> tuple[bool, str]:
    """先尝试 taskkill（可输出友好信息），失败则回退 TerminateProcess。"""
    detail = ""
    try:
        proc = subprocess.run(
            ["taskkill.exe", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            return True, (proc.stdout + proc.stderr).strip()
        detail = (proc.stdout + proc.stderr).strip()
    except Exception as exc:
        detail = str(exc)
    ok, detail2 = _terminate_process(pid)
    return ok, f"{detail} | fallback: {detail2}" if detail else detail2


def append_evidence(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def sweep(evidence: Path) -> int:
    """检查并结束当前所有下载进程，返回被拦截的数量。"""
    pids = list_downloader_pids()
    if not pids:
        return 0
    for pid in pids:
        ok, detail = kill_pid(pid)
        append_evidence(
            evidence,
            {
                "ts": now_iso(),
                "event": "background_download_killed",
                "pid": pid,
                "ok": ok,
                "detail": detail[:500],
            },
        )
    return len(pids)


def task_enabled() -> bool | None:
    """计划任务是否仍处于启用状态（用于提示剩余风险）。"""
    try:
        proc = subprocess.run(
            [
                "schtasks.exe",
                "/Query",
                "/TN",
                "\\Microsoft\\VisualStudio\\Updates\\BackgroundDownload",
                "/V",
                "/FO",
                "LIST",
            ],
            capture_output=True,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout or b""
    if b"\x00" in raw[:400]:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.strip().startswith("Scheduled Task State:"):
            return "Enabled" in line
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="VS Installer 后台下载看门狗（免提权即时缓解）")
    parser.add_argument("--once", action="store_true", help="只检查一次后退出")
    parser.add_argument("--interval", type=int, default=15, help="轮询间隔秒数（默认 15）")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE, help="证据 JSONL 路径")
    args = parser.parse_args()

    evidence = args.evidence.resolve()
    state = task_enabled()

    if args.once:
        killed = sweep(evidence)
        print(f"[{now_iso()}] 拦截 {killed} 个后台下载进程；计划任务启用状态={state}")
        return 0

    print(f"看门狗启动：每 {args.interval}s 检查一次 {PROCESS_NAME}.exe")
    print(f"证据文件：{evidence}")
    print(f"计划任务启用状态={state}")
    if state:
        print("注意：计划任务仍为 Enabled，会周期性拉起下载进程；本看门狗只负责立即结束它。")
        print("      彻底关闭请以管理员执行 disable_vs_background_download.ps1。")
    print("Ctrl+C 退出。")

    total = 0
    try:
        while True:
            killed = sweep(evidence)
            if killed:
                total += killed
                print(f"[{now_iso()}] 拦截 {killed} 个下载进程（累计 {total}）")
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        print(f"\n看门狗退出，累计拦截 {total} 次。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
