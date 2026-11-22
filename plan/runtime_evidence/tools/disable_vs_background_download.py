# -*- coding: utf-8 -*-
"""disable_vs_background_download.py

关闭 Visual Studio Installer 的自动后台下载（系统跑死机的元凶）。

依据（2026-11-22 现场取证）:
  * 计划任务 \\Microsoft\\VisualStudio\\Updates\\BackgroundDownload
    以 InteractiveToken 身份 (SID ...-1001 / cdao) 运行 BackgroundDownload.exe，
    2026-11-22 当天 03:46 / 07:21 / 08:22 x2 / 08:30 x2 共触发 6 次。
  * dd_BackgroundDownload_*.log 明确记录:
        Policy setting: BackgroundDownloadDisabled, value: False
        Policy setting: UpdateConfigurationFile,
            value: C:\\ProgramData\\Microsoft\\VisualStudio\\updates.config
  * 三个实例 state.json 的 userProperties 累计下载量:
        238f66ce (VS2019 BuildTools 16.11) updateDownloadedSize = 1,314,698,544,469 (~1.31 TB)
        7416e62c (VS2022 BuildTools 17.14) updateDownloadTotalSize = 31,632,317
        968a13cd (VS2022 BuildTools 17.14) updateDownloadedSize = 64,835,831,473 (~64.8 GB)

安装器解析「策略设置」的优先级（由 Microsoft.VisualStudio.Setup.dll 反编译确认）:
  1) HKLM\\SOFTWARE\\Policies\\Microsoft\\VisualStudio\\Setup   （组策略，最强）
  2) <UpdateConfigurationFile>                                  （updates.config，name/value JSON）
  3) HKLM\\SOFTWARE\\Microsoft\\VisualStudio\\Setup              （安装器设置）
  4) 实例 state.json 的 properties / userProperties

本脚本同时写入 1) 2) 3) 三条路径，并禁用计划任务，确保无论安装器从哪一层
读取都能拿到 BackgroundDownloadDisabled=1。

用法（必须以管理员身份运行）:
    powershell -ExecutionPolicy Bypass -File disable_vs_background_download.ps1
    python disable_vs_background_download.py            # 需管理员权限的 python
    python disable_vs_background_download.py --verify  # 只做只读体检，不写任何东西

只读体检（--verify）不需要管理员权限，可以在 Codex 沙箱里跑。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TASK_PATH = "\\Microsoft\\VisualStudio\\Updates\\"
TASK_NAME = "BackgroundDownload"
BG_EXE = (
    r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\resources\app"
    r"\ServiceHub\Services\Microsoft.VisualStudio.Setup.Service\BackgroundDownload.exe"
)
SHARED_DIR = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Shared")
UPDATES_CONFIG = Path(r"C:\ProgramData\Microsoft\VisualStudio\updates.config")

POLICY_KEYS = [
    r"HKLM\SOFTWARE\Policies\Microsoft\VisualStudio\Setup",
    r"HKLM\SOFTWARE\Microsoft\VisualStudio\Setup",
    r"HKLM\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\Setup",
]

# 安装器属性名（反编译确认的 property bag 键名）
PROP_BACKGROUND_DOWNLOAD_DISABLED = "BackgroundDownloadDisabled"
PROP_BACKGROUND_DOWNLOAD = "BackgroundDownload"
PROP_AUTO_UPDATE_DISABLED = "AutoUpdateDisabled"

# 实例状态存在两份：
#   ProgramData ...\_Instances\<id>\state.json  -> 全局 properties（安装器设置）
#   LOCALAPPDATA ...\_Instances\<id>\state.json -> userProperties（后台下载计数器）
INSTANCE_DIRS = [
    Path(r"C:\ProgramData\Microsoft\VisualStudio\Packages\_Instances"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "VisualStudio" / "Packages" / "_Instances",
]


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _reg(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["reg.exe", *args], capture_output=True)


def reg_read(key: str, value: str) -> tuple[bool, str]:
    proc = _reg("query", key, "/v", value)
    if proc.returncode != 0:
        return False, ""
    for line in _decode_win_output(proc).splitlines():
        line = line.strip()
        if line.startswith(value):
            parts = line.split(None, 1)
            if len(parts) == 2:
                return True, parts[1].strip()
    return False, ""


def reg_write(key: str, value: str, data: str) -> tuple[bool, str]:
    proc = _reg("add", key, "/v", value, "/t", "REG_DWORD", "/d", data, "/f")
    return proc.returncode == 0, _decode_win_output(proc).strip()


def reg_write_str(key: str, value: str, data: str) -> tuple[bool, str]:
    proc = _reg("add", key, "/v", value, "/t", "REG_SZ", "/d", data, "/f")
    return proc.returncode == 0, _decode_win_output(proc).strip()


def _decode_win_output(proc: subprocess.CompletedProcess) -> str:
    """schtasks/reg 控制台输出为单字节代码页（CP437/CP936），按需自适应解码。"""
    raw = proc.stdout or b""
    if isinstance(raw, str):
        return raw
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16-le", errors="replace")
    for enc in ("utf-8", "mbcs", "cp936", "cp437"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if enc in ("mbcs", "cp936", "cp437"):
            return text
        return text
    return raw.decode("utf-8", errors="replace")


def task_state() -> dict:
    out = {"exists": False, "state": "", "status": "", "last_run": "", "last_result": ""}
    proc = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK_PATH + TASK_NAME, "/V", "/FO", "LIST"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return out
    out["exists"] = True
    for line in _decode_win_output(proc).splitlines():
        line = line.strip()
        if line.startswith("Scheduled Task State:"):
            out["state"] = line.split(":", 1)[1].strip()
        elif line.startswith("Last Run Time:"):
            out["last_run"] = line.split(":", 1)[1].strip()
        elif line.startswith("Last Result:"):
            out["last_result"] = line.split(":", 1)[1].strip()
        elif line.startswith("Status:"):
            out["status"] = line.split(":", 1)[1].strip()
    return out


def task_disable() -> tuple[bool, str]:
    proc = subprocess.run(
        ["schtasks.exe", "/Change", "/TN", TASK_PATH + TASK_NAME, "/DISABLE"],
        capture_output=True,
    )
    return proc.returncode == 0, _decode_win_output(proc).strip()


def write_updates_config() -> tuple[bool, str]:
    """写 name/value JSON 策略文件。格式与安装器 ParseNameValuePairJsonFile 一致。"""
    payload = {
        PROP_BACKGROUND_DOWNLOAD_DISABLED: "1",
        PROP_BACKGROUND_DOWNLOAD: "0",
        PROP_AUTO_UPDATE_DISABLED: "1",
        "RemoveOos": "0",
        "KeepDownloadedPayloads": "0",
    }
    target = UPDATES_CONFIG
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - admin 才会走到
        return False, f"mkdir failed: {exc}"
    try:
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        return False, f"write failed: {exc}"
    return True, str(target)


def write_shared_updates_config() -> tuple[bool, str]:
    """备用路径：<SharedInstallPath>\\updates.config（安装器也会读）。"""
    payload = {
        PROP_BACKGROUND_DOWNLOAD_DISABLED: "1",
        PROP_BACKGROUND_DOWNLOAD: "0",
        PROP_AUTO_UPDATE_DISABLED: "1",
    }
    target = SHARED_DIR / "updates.config"
    try:
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        return False, f"write failed: {exc}"
    return True, str(target)


def collect() -> dict:
    """只读体检：汇总当前后台下载相关的所有状态。"""
    report: dict = {
        "is_admin": is_admin(),
        "registry": {},
        "updates_config": {},
        "task": task_state(),
        "background_download_exe_exists": BG_EXE and Path(BG_EXE).exists(),
        "instances": [],
    }
    for key in POLICY_KEYS:
        entry = {}
        for value in (
            PROP_BACKGROUND_DOWNLOAD_DISABLED,
            PROP_BACKGROUND_DOWNLOAD,
            PROP_AUTO_UPDATE_DISABLED,
        ):
            found, data = reg_read(key, value)
            entry[value] = data if found else None
        report["registry"][key] = entry

    for path in (UPDATES_CONFIG, SHARED_DIR / "updates.config"):
        info = {"path": str(path), "exists": path.exists()}
        if path.exists():
            try:
                info["content"] = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                info["content"] = f"<parse error: {exc}>"
        report["updates_config"][str(path)] = info

    for base in INSTANCE_DIRS:
        if not base.exists():
            continue
        for inst_dir in sorted(base.iterdir()):
            state_file = inst_dir / "state.json"
            if not state_file.is_file():
                continue
            item = {
                "instance": inst_dir.name,
                "store": "global" if "ProgramData" in str(base) else "user",
                "state_file": str(state_file),
            }
            try:
                data = json.loads(state_file.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                item["error"] = str(exc)
                report["instances"].append(item)
                continue
            product = data.get("product") or {}
            item["product"] = product.get("displayVersion") or product.get("productDisplayVersion")
            item["properties"] = data.get("properties", {}) or {}
            item["userProperties"] = data.get("userProperties", {}) or {}
            report["instances"].append(item)
    return report


def apply() -> int:
    if not is_admin():
        print("[FAIL] 当前不是管理员，无法写入 HKLM / ProgramData。")
        print("       请以「管理员身份」重新运行本脚本。")
        return 2

    actions = []

    for key in POLICY_KEYS:
        for value, data in (
            (PROP_BACKGROUND_DOWNLOAD_DISABLED, "1"),
            (PROP_BACKGROUND_DOWNLOAD, "0"),
            (PROP_AUTO_UPDATE_DISABLED, "1"),
        ):
            ok, msg = reg_write(key, value, data)
            actions.append({"target": f"{key}\\{value}", "ok": ok, "detail": msg})

    ok, msg = write_updates_config()
    actions.append({"target": str(UPDATES_CONFIG), "ok": ok, "detail": msg})

    ok, msg = write_shared_updates_config()
    actions.append({"target": str(SHARED_DIR / "updates.config"), "ok": ok, "detail": msg})

    ok, msg = task_disable()
    actions.append({"target": f"task {TASK_PATH}{TASK_NAME}", "ok": ok, "detail": msg})

    # 停掉可能正在跑的下载进程与提权服务
    for exe in ("BackgroundDownload.exe",):
        subprocess.run(
            ["taskkill.exe", "/F", "/IM", exe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    subprocess.run(
        ["net.exe", "stop", "VSInstallerElevationService"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    print("=" * 72)
    print("VS Installer 后台下载关闭结果")
    print("=" * 72)
    failed = 0
    for act in actions:
        flag = "OK  " if act["ok"] else "FAIL"
        if not act["ok"]:
            failed += 1
        print(f"[{flag}] {act['target']}")
        if not act["ok"]:
            print(f"         {act['detail']}")

    print("-" * 72)
    print("关闭后复核：")
    report = collect()
    for key, entry in report["registry"].items():
        print(f"  {key}")
        for name, value in entry.items():
            print(f"      {name} = {value}")
    print(f"  task state = {report['task'].get('state')}  status = {report['task'].get('status')}")
    print(f"  task last_run = {report['task'].get('last_run')}  last_result = {report['task'].get('last_result')}")
    for path, info in report["updates_config"].items():
        print(f"  {path} exists={info['exists']}")
    return 1 if failed else 0


def verify() -> int:
    report = collect()
    print("=" * 72)
    print("VS Installer 后台下载状态体检（只读）")
    print("=" * 72)
    print(f"管理员权限        : {report['is_admin']}")
    print(f"BackgroundDownload.exe 存在 : {report['background_download_exe_exists']}")
    print()
    print("注册表策略：")
    for key, entry in report["registry"].items():
        print(f"  {key}")
        for name, value in entry.items():
            mark = "  <-- 未设置" if value is None else ""
            print(f"      {name} = {value}{mark}")
    print()
    print("策略配置文件：")
    for path, info in report["updates_config"].items():
        print(f"  {path}  exists={info['exists']}")
        if info["exists"] and "content" in info:
            print(f"      {info['content']}")
    print()
    print("计划任务：")
    task = report["task"]
    print(f"  {TASK_PATH}{TASK_NAME}")
    print(f"      exists = {task['exists']}")
    print(f"      state  = {task['state']}")
    print(f"      last_run = {task['last_run']}  last_result = {task['last_result']}")
    print()
    print("实例状态（ProgramData=全局 / LOCALAPPDATA=当前用户）：")
    for inst in report["instances"]:
        up = inst.get("userProperties", {}) or {}
        pr = inst.get("properties", {}) or {}
        print(
            f"  [{inst.get('store')}] {inst['instance']}  ver={inst.get('product')}  "
            f"downloaded={up.get('updateDownloadedSize', '-')}  "
            f"total={up.get('updateDownloadTotalSize', '-')}  "
            f"complete={up.get('updateDownloadComplete', '-')}"
        )
        if pr:
            print(f"      properties={pr}")

    disabled = []
    for entry in report["registry"].values():
        if entry.get(PROP_BACKGROUND_DOWNLOAD_DISABLED) == "1":
            disabled.append(True)
    print()
    print("结论：", "已关闭（至少一条注册表路径生效）" if disabled else "未关闭（需要管理员执行 apply）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="关闭 Visual Studio Installer 自动后台下载")
    parser.add_argument("--verify", action="store_true", help="只做只读体检，不做任何修改")
    args = parser.parse_args()
    if args.verify:
        return verify()
    return apply()


if __name__ == "__main__":
    sys.exit(main())
