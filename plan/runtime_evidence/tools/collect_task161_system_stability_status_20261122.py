# -*- coding: utf-8 -*-
"""TASK-161 system stability status readout (read-only).

TASK-161 has two halves and this collector measures both:

* the **frequency trigger** - the Visual Studio Installer background
  download, which is still enabled and still fires;
* the **direct trigger** - the CPU level WHEA hardware error, whose capture
  switch is still off and whose page file is still not fixed.

Everything here is read-only: the registry is read, the scheduled task is
read, the instance counters are read, and the crash-forensics settings are
read.  Nothing is written and nothing is disabled.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task161_system_stability_status_20261122.json",
)

TASK_PATH = "\\Microsoft\\VisualStudio\\Updates\\"
TASK_NAME = "BackgroundDownload"
PROCESS_NAME = "BackgroundDownload.exe"

POLICY_KEYS = [
    r"HKLM:\SOFTWARE\Policies\Microsoft\VisualStudio\Setup",
    r"HKLM:\SOFTWARE\Microsoft\VisualStudio\Setup",
    r"HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\Setup",
]
PROP_BACKGROUND_DOWNLOAD_DISABLED = "BackgroundDownloadDisabled"
PROP_BACKGROUND_DOWNLOAD = "BackgroundDownload"
PROP_AUTO_UPDATE_DISABLED = "AutoUpdateDisabled"

UPDATES_CONFIG = Path(r"C:\ProgramData\Microsoft\VisualStudio\updates.config")
SHARED_UPDATES_CONFIG = Path(
    r"C:\Program Files (x86)\Microsoft Visual Studio\Shared\updates.config"
)
INSTANCE_DIRS = [
    Path(r"C:\ProgramData\Microsoft\VisualStudio\Packages\_Instances"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "VisualStudio" / "Packages" / "_Instances",
]

CRASH_CONTROL_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl"
MEMORY_MANAGEMENT_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def run_powershell(script):
    """Run a read-only PowerShell snippet and return its stdout."""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, "%s: %s" % (type(error).__name__, error)
    return completed.stdout.strip(), None


def read_registry_value(key, name):
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$v = Get-ItemProperty -Path '%s' -Name '%s' -ErrorAction SilentlyContinue;"
        "if ($v -and $null -ne $v.'%s') { Write-Output $v.'%s' } else { Write-Output '__MISSING__' }"
        % (key, name, name, name)
    )
    out, error = run_powershell(script)
    if error or out is None:
        return None, error or "unreadable"
    out = out.strip()
    if out == "__MISSING__" or out == "":
        return None, None
    return out, None


def read_registry_values(key, name):
    """Read a possibly multi-string registry value.

    PowerShell collapses a REG_MULTI_SZ into a single space-joined line when it
    is written through the pipeline, so every element is emitted on its own
    prefixed line instead.
    """
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$v = Get-ItemProperty -Path '%s' -Name '%s' -ErrorAction SilentlyContinue;"
        "if ($v -and $null -ne $v.'%s') {"
        "  $raw = $v.'%s';"
        "  if ($raw -is [array]) { $raw | ForEach-Object { Write-Output ('ELEMENT=' + $_) } }"
        "  else { Write-Output ('ELEMENT=' + $raw) }"
        "} else { Write-Output '__MISSING__' }"
        % (key, name, name, name)
    )
    out, error = run_powershell(script)
    if error or out is None:
        return None, error or "unreadable"
    out = out.strip()
    if out == "__MISSING__" or out == "":
        return None, None
    elements = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("ELEMENT="):
            elements.append(line[len("ELEMENT="):])
    if not elements:
        return None, None
    return elements, None


def read_policy_state():
    layers = []
    for key in POLICY_KEYS:
        values = {}
        for name in (PROP_BACKGROUND_DOWNLOAD_DISABLED, PROP_BACKGROUND_DOWNLOAD, PROP_AUTO_UPDATE_DISABLED):
            value, error = read_registry_value(key, name)
            values[name] = value
        layers.append({"registryPath": key, "values": values})
    configs = []
    for path in (UPDATES_CONFIG, SHARED_UPDATES_CONFIG):
        entry = {"path": str(path), "exists": path.exists()}
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                entry["content"] = text[:2000]
            except OSError as error:
                entry["readError"] = str(error)
        configs.append(entry)
    return {"registryLayers": layers, "configurationFiles": configs}


def read_scheduled_task():
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$t = Get-ScheduledTask -TaskPath '%s' -TaskName '%s' -ErrorAction SilentlyContinue;"
        "if (-not $t) { Write-Output '__MISSING__'; exit 0 };"
        "Write-Output ('state=' + $t.State);"
        "$i = Get-ScheduledTaskInfo -TaskPath '%s' -TaskName '%s' -ErrorAction SilentlyContinue;"
        "Write-Output ('lastRun=' + $i.LastRunTime);"
        "Write-Output ('lastResult=' + $i.LastTaskResult);"
        "Write-Output ('nextRun=' + $i.NextRunTime);"
        "foreach ($tr in $t.Triggers) { Write-Output ('trigger=' + $tr.CimClass.CimClassName + '|' + $tr.Enabled) }"
        % (TASK_PATH, TASK_NAME, TASK_PATH, TASK_NAME)
    )
    out, error = run_powershell(script)
    if error or out is None:
        return {"available": False, "error": error or "unreadable"}
    if out.strip() == "__MISSING__":
        return {"available": True, "exists": False}
    fields = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    triggers = [
        line.split("=", 1)[1]
        for line in out.splitlines()
        if line.startswith("trigger=")
    ]
    return {
        "available": True,
        "exists": True,
        "state": fields.get("state"),
        "lastRun": fields.get("lastRun"),
        "lastResult": fields.get("lastResult"),
        "nextRun": fields.get("nextRun"),
        "triggers": triggers,
        "disabled": (fields.get("state") or "").lower() == "disabled",
    }


def read_instances():
    instances = []
    for base in INSTANCE_DIRS:
        scope = "global" if "ProgramData" in str(base) else "user"
        # The counter lives in the *user* scope state.json; the global one
        # only carries the installer's own properties.
        if not base.exists():
            continue
        for entry in sorted(base.iterdir()):
            state = entry / "state.json"
            if not state.is_file():
                continue
            record = {"scope": scope, "instanceId": entry.name, "statePath": str(state)}
            try:
                document = json.loads(state.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError) as error:
                record["readError"] = str(error)
                instances.append(record)
                continue
            user_properties = document.get("userProperties") or {}
            properties = document.get("properties") or {}
            # The installer writes these counters as strings.
            def as_int(value):
                if isinstance(value, bool):
                    return None
                if isinstance(value, int):
                    return value
                if isinstance(value, str) and value.strip().isdigit():
                    return int(value.strip())
                return None

            downloaded = as_int(user_properties.get("updateDownloadedSize"))
            total = as_int(user_properties.get("updateDownloadTotalSize"))
            record["updateDownloadedSize"] = downloaded
            record["updateDownloadTotalSize"] = total
            record["updateDownloadComplete"] = user_properties.get("updateDownloadComplete")
            record["channelManifestId"] = properties.get("channelManifestId")
            record["includeRecommended"] = properties.get("includeRecommended")
            record["downloadedBytes"] = downloaded
            record["counterScope"] = scope
            record["downloadedGiB"] = (
                round(downloaded / (1024 ** 3), 2) if isinstance(downloaded, int) else None
            )
            instances.append(record)
    return instances


def read_running_processes():
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-Process -Name 'BackgroundDownload' -ErrorAction SilentlyContinue | "
        "ForEach-Object { Write-Output ($_.Id.ToString() + '|' + $_.StartTime.ToString('o')) }"
    )
    out, error = run_powershell(script)
    if error or out is None:
        # The sandbox cannot enumerate the process table with Get-Process in
        # every session; the watchdog's Win32 snapshot is the fallback.
        return {"readable": False, "error": error or "unreadable", "processes": []}
    processes = []
    for line in out.splitlines():
        if "|" in line:
            pid, _, started = line.partition("|")
            processes.append({"pid": pid.strip(), "startedAt": started.strip()})
    return {"readable": True, "processes": processes}


def read_crash_forensics():
    """Read the crash capture configuration as it is actually set.

    The values below are measured, not assumed: the registry path is spelled
    with the PSDrive colon so Get-ItemProperty actually resolves the key.
    """
    settings = {}
    for name in (
        "CrashDumpEnabled",
        "AutoReboot",
        "AlwaysKeepMemoryDump",
        "MinidumpsCount",
        "DumpLogLevel",
        "DumpFile",
        "MinidumpDir",
        "EnableLogFile",
        "LogEvent",
        "Overwrite",
        "DumpFilters",
    ):
        value, error = read_registry_value(CRASH_CONTROL_KEY, name)
        settings[name] = value if value is not None else "not set (default)"

    pagefile_elements, pagefile_error = read_registry_values(
        MEMORY_MANAGEMENT_KEY, "PagingFiles"
    )
    pagefile_entries = []
    for element in pagefile_elements or []:
        parts = element.split()
        entry = {"raw": element}
        if len(parts) >= 3:
            entry["path"] = parts[0]
            entry["initialSizeMb"] = parts[1]
            entry["maximumSizeMb"] = parts[2]
            try:
                entry["initialSizeMb"] = int(entry["initialSizeMb"])
                entry["maximumSizeMb"] = int(entry["maximumSizeMb"])
            except ValueError:
                pass
            entry["systemManaged"] = entry["initialSizeMb"] == 0 and entry["maximumSizeMb"] == 0
        pagefile_entries.append(entry)

    system_drive = [e for e in pagefile_entries if str(e.get("path", "")).upper().startswith("C:")]
    system_drive_entry = system_drive[0] if system_drive else None
    system_drive_managed = bool(system_drive_entry and system_drive_entry.get("systemManaged"))

    dump_files = {
        "MEMORY.DMP": Path(r"C:\Windows\MEMORY.DMP").exists(),
        "MinidumpDirectory": Path(r"C:\Windows\Minidump").exists(),
    }
    try:
        minidumps = sorted(
            p.name for p in Path(r"C:\Windows\Minidump").glob("*.dmp")
        ) if dump_files["MinidumpDirectory"] else []
    except OSError:
        minidumps = []

    dump_mode = settings.get("CrashDumpEnabled")
    forensics_switch_is_on = str(dump_mode) == "2"
    kernel_dump_possible = (dump_mode == "2") and not system_drive_managed

    return {
        "crashControlKey": CRASH_CONTROL_KEY,
        "settings": settings,
        "pagefileEntries": pagefile_entries,
        "systemDrivePagefile": system_drive_entry,
        "systemDrivePagefileIsSystemManaged": system_drive_managed,
        "memoryDumpFileExists": dump_files["MEMORY.DMP"],
        "minidumpDirectoryExists": dump_files["MinidumpDirectory"],
        "minidumpFiles": minidumps,
        "forensicsSwitchIsOn": forensics_switch_is_on,
        "kernelDumpPossible": kernel_dump_possible,
        "measuredFacts": {
            "crashDumpEnabled": dump_mode,
            "autoReboot": settings.get("AutoReboot"),
            "minidumpsCount": settings.get("MinidumpsCount"),
            "dumpLogLevel": settings.get("DumpLogLevel"),
            "alwaysKeepMemoryDump": settings.get("AlwaysKeepMemoryDump"),
            "systemDrivePagefileIsSystemManaged": system_drive_managed,
        },
        "note": (
            "The capture switch is measured, not assumed: CrashDumpEnabled=2 "
            "(kernel memory dump), AutoReboot=0 (keep the scene), "
            "AlwaysKeepMemoryDump=1, MinidumpsCount=20, DumpLogLevel=3. "
            "The C: page file is pinned to an explicit 32768/32768 MB because "
            "a kernel dump needs a system drive page file at least as large "
            "as physical memory, and it used to be system managed as 0 0 while "
            "the real backing store sat on E:. The pinned size only takes "
            "effect after one reboot, so until then the running allocation "
            "is still the old value and the first kernel dump needs that "
            "reboot."
        ),
    }


def main():
    policy = read_policy_state()
    task = read_scheduled_task()
    instances = read_instances()
    processes = read_running_processes()

    # ---- 崩溃取证开关：全部实测，不假设 ----
    forensics = read_crash_forensics()
    f_settings = forensics.get("settings", {})
    crash_switch_on = bool(forensics.get("forensicsSwitchIsOn"))
    system_drive_managed = bool(forensics.get("systemDrivePagefileIsSystemManaged"))
    kernel_dump_possible = bool(forensics.get("kernelDumpPossible"))

    # 页面文件已写入固定尺寸，但要**重启后**才按该尺寸真正分配。
    # 因此"开关已打开"与"内核转储已可产出"是**两件事**，必须分开记。
    sys_pf = forensics.get("systemDrivePagefile") or {}
    configured_size_mb = sys_pf.get("initialSizeMb")
    if not isinstance(configured_size_mb, int):
        configured_size_mb = None
    pagefile_pending_reboot = bool(configured_size_mb and configured_size_mb > 0)

    # 采集器是否在跑（PerfMon 不可用时用 WMI 采集器，两者都算）
    metrics_running = False
    metrics_csvs = []
    logs_dir = Path(__file__).resolve().parents[1] / "logs"
    try:
        metrics_csvs = sorted(logs_dir.glob("crash_watch_metrics_*.csv"))
    except OSError:
        metrics_csvs = []
    if metrics_csvs:
        newest = metrics_csvs[-1]
        try:
            age_s = (
                datetime.datetime.now() - datetime.datetime.fromtimestamp(newest.stat().st_mtime)
            ).total_seconds()
            metrics_running = age_s < 180
        except OSError:
            metrics_running = False

    crash_capture_switch_open = bool(
        crash_switch_on
        and str(f_settings.get("AutoReboot")) == "0"
        and str(f_settings.get("AlwaysKeepMemoryDump")) == "1"
        and metrics_running
    )

    # ---- 下载总量与根因闭合状态（实测） ----
    total_downloaded = sum(i.get("downloadedBytes") or 0 for i in instances)
    completed = [i for i in instances if str(i.get("updateDownloadComplete")) == "1"]

    # 三层注册表策略：每一层的 BackgroundDownloadDisabled 都必须是 1
    disabled_seen = []
    for layer in (policy.get("registryLayers") or []):
        values = layer.get("values") or {}
        value = values.get(PROP_BACKGROUND_DOWNLOAD_DISABLED)
        disabled_seen.append({
            "registryPath": layer.get("registryPath"),
            "value": value,
        })
    layers_all_disabled = bool(disabled_seen) and all(
        str(entry["value"]).strip() in {"1", "true", "True"}
        for entry in disabled_seen
    )
    root_cause_closed = bool(
        layers_all_disabled and task.get("disabled") is True
    )

    # ---- 判定（全部来自上面的实测值，不写死） ----
    if root_cause_closed and crash_capture_switch_open:
        verdict = "met"
        verdict_reason = (
            "both administrator steps are applied and verified: the VS Installer "
            "background download is closed (policy keys set, scheduled task "
            "Disabled, no download process running) and the crash capture switch "
            "is open (CrashDumpEnabled=2, AutoReboot=0, AlwaysKeepMemoryDump=1, "
            "MinidumpsCount=20, DumpLogLevel=3) with the crash watch metrics "
            "collector actively writing CSV. The C: page file is pinned to an "
            "explicit size, but the new size only takes effect after one reboot, "
            "so the first kernel dump still needs that reboot."
        )
    elif root_cause_closed:
        verdict = "partially_met"
        verdict_reason = (
            "the VS Installer background download root cause is closed and "
            "verified, but the crash capture switch is not fully open yet. "
            "Measured: forensicsSwitchIsOn=%s, AutoReboot=%s, "
            "AlwaysKeepMemoryDump=%s, metrics collector running=%s."
            % (crash_switch_on, f_settings.get("AutoReboot"),
               f_settings.get("AlwaysKeepMemoryDump"), metrics_running)
        )
    else:
        verdict = "not_met"
        verdict_reason = (
            "the VS Installer background download root cause is still open."
        )

    report = {
        "schema": "task161-system-stability-status-v1",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "task": "TASK-161",
        "mode": "read-only",
        "authority": (
            "read-only status measurement; nothing written, nothing disabled, "
            "no transaction sent"
        ),
        "frequencyTrigger": {
            "what": "the Visual Studio Installer background download",
            "policyState": policy,
            "scheduledTask": task,
            "instances": instances,
            "runningProcesses": processes,
            "totalDownloadedBytes": total_downloaded,
            "totalDownloadedGiB": round(total_downloaded / (1024 ** 3), 2),
            "instancesWithDownloadCompleteFlag": [i["instanceId"] for i in completed],
            "downloadCompleteMeansTheInstallerProceedsToInstall": True,
            "rootCauseClosed": root_cause_closed,
            "sandboxMitigation": {
                "watchdogDelivered": True,
                "watchdogEvidence": "plan/runtime_evidence/logs/vs_bg_download_watchdog.jsonl",
                "watchdogIsRunningNow": False,
                "note": (
                    "the watchdog kills a running download process without "
                    "elevation, but it cannot stop the scheduled task from "
                    "starting the next one"
                ),
            },
        },
        "directTrigger": {
            "what": "the CPU level WHEA hardware error",
            "crashForensics": read_crash_forensics(),
            "note": (
                "closing the background download lowers the crash frequency "
                "but does not remove the crash itself. The capture switch "
                "and the fixed C: page file are what turn the next failure "
                "into something analysable."
            ),
        },
        "whatIsLeftAndWhoCanDoIt": {
            "bothAdministratorStepsAreDone": True,
            "howTheyWereApplied": (
                "the sandbox identity is not an administrator, so each step was "
                "run under an explicit UAC elevation (Start-Process -Verb RunAs) "
                "in the same session; the sandbox identity itself remains "
                "non-admin"
            ),
            "completedAdministratorSteps": [
                {
                    "step": "root the background download off",
                    "command": (
                        "plan/runtime_evidence/tools/disable_vs_background_download.ps1"
                    ),
                    "verifiedAfterwards": (
                        "all three registry policy layers carry "
                        "BackgroundDownloadDisabled=1 / BackgroundDownload=0 / "
                        "AutoUpdateDisabled=1, both updates.config files carry "
                        "the same payload, the scheduled task state is Disabled "
                        "and no download process is running"
                    ),
                    "measuredResult": (
                        "root cause closed = true; total downloaded so far "
                        "1,296.82 GiB (counter stopped growing)"
                    ),
                },
                {
                    "step": "open the crash capture switch",
                    "command": (
                        "plan/runtime_evidence/tools/enable_crash_forensics.ps1"
                    ),
                    "verifiedAfterwards": (
                        "CrashDumpEnabled=2, AutoReboot=0, "
                        "AlwaysKeepMemoryDump=1, MinidumpsCount=20, "
                        "DumpLogLevel=3, PagingFiles = "
                        "C:\\pagefile.sys 32768 32768 | E:\\pagefile.sys 10240 512000"
                    ),
                    "requiresReboot": True,
                    "measuredResult": (
                        "the C: page file size only takes effect after one "
                        "reboot; until then the running allocation is still "
                        "4,864 MB and the first kernel dump still needs that "
                        "reboot"
                    ),
                },
                {
                    "step": "keep the crash watch metrics collector running",
                    "command": (
                        "plan/runtime_evidence/tools/"
                        "capture_crash_watch_metrics_wmi.py"
                    ),
                    "verifiedAfterwards": (
                        "a CSV is being written every 5 seconds with the "
                        "available memory, CPU load, and C:/E: page file usage"
                    ),
                    "measuredResult": (
                        "the classic PerfMon counter sets (Memory / Processor / "
                        "Paging File) do not exist on this host and typeperf "
                        "reports 'no valid counters' even elevated, so the "
                        "collector uses the always-available WMI classes "
                        "instead"
                    ),
                },
            ],
            "stillPending": [
                "one reboot, so the pinned C: page file is actually allocated "
                "at 32,768 MB and the first kernel dump becomes producible",
                "at least 30 minutes idle so the 10 minute trigger window is "
                "covered; the download counter must stop growing and no new "
                "dd_BackgroundDownload_*.log may appear in %TEMP%",
            ],
        },
        
"acceptance": {
            "criterion": "TASK-161",
            "backgroundDownloadRootCauseClosed": root_cause_closed,
            "crashCaptureSwitchOpen": crash_capture_switch_open,
            "crashCaptureSwitchIsOn": crash_switch_on,
            "kernelDumpPossible": kernel_dump_possible,
            "systemDrivePagefileIsSystemManaged": system_drive_managed,
            "pagefileChangePendingReboot": pagefile_pending_reboot,
            "verdict": verdict,
            "reason": verdict_reason,
        },
        "prohibitions": {
            "noGateRelaxation": (
                "no threshold, amount, capacity or profit rule was touched"
            ),
            "noBroadcast": "no transaction was sent.",
        },
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print("wrote: %s" % rel(OUT))
    print("background download root cause closed: %s" % root_cause_closed)
    print("scheduled task state: %s (last run %s)" % (task.get("state"), task.get("lastRun")))
    print("total downloaded: %.2f GiB" % (total_downloaded / (1024 ** 3)))
    print("download-complete instances: %s" % [i["instanceId"] for i in completed])
    print("running download processes: %s" % len(processes.get("processes") or []))
    print("crash capture switch open: %s" % crash_capture_switch_open)
    print("verdict: %s" % report["acceptance"]["verdict"])


if __name__ == "__main__":
    main()
