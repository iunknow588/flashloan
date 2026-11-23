# -*- coding: utf-8 -*-
"""崩溃前性能快照采集器（WMI 口径，locale 无关）。

背景：本机系统区域为 zh-CN，经典 PerfMon 计数器集（Memory / Processor / Paging File）
实测**缺失**（Get-Counter -ListSet 中不存在），typeperf 因此报“没有有效计数器”。
WMI 的 Win32_PerfFormattedData_* 性能类同样不可用（无效类）。
故本采集器改用**始终可用**的 WMI 基础类：
  - Win32_OperatingSystem  -> AvailableMBytes（换算自 FreePhysicalMemory KB）
  - Win32_PageFileUsage   -> 分页文件已用/已分配/峰值（MB）
  - Win32_Processor       -> LoadPercentage / 逻辑核数
并在 typeperf 可用时**自动**追加 PerfMon 口径（双源互证）。

输出：CSV，每行一条样本，字段：
  timestamp_iso, available_mbytes, cpu_load_pct, logical_cpus,
  pagefile_c_used_mb, pagefile_c_alloc_mb, pagefile_c_peak_mb,
  pagefile_e_used_mb, pagefile_e_alloc_mb, pagefile_e_peak_mb,
  perfmon_available_mbytes, perfmon_cpu_pct, perfmon_paging_usage_pct, source
"""
import csv, datetime, os, subprocess, sys, time

OUT_DIR = r"E:\2026OPC大赛\flashLoan\plan\runtime_evidence\logs"
INTERVAL = 5

FIELDS = [
    "timestamp_iso", "available_mbytes", "cpu_load_pct", "logical_cpus",
    "pagefile_c_used_mb", "pagefile_c_alloc_mb", "pagefile_c_peak_mb",
    "pagefile_e_used_mb", "pagefile_e_alloc_mb", "pagefile_e_peak_mb",
    "perfmon_available_mbytes", "perfmon_cpu_pct", "perfmon_paging_usage_pct",
    "source",
]

PERFMON_COUNTERS = [
    r"\Memory\Available MBytes",
    r"\Processor(_Total)\\% Processor Time",
    r"\Paging File(_Total)\% Usage",
]


def _ps(script):
    """用 powershell 取一段 CIM 数据，返回 stdout 文本。

    注意：Windows 控制台默认输出代码页可能是 GBK/936（含中文错误消息），
    因此**不能**用 text=True（会按 UTF-8 解码并抛 UnicodeDecodeError）。
    这里改为二进制捕获后按 UTF-8 -> GBK -> latin-1 依次尝试解码。
    """
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True, timeout=30)
        raw = r.stdout or b""
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return raw.decode(enc).strip()
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="replace").strip()
    except Exception:
        return ""


def read_os_memory():
    out = _ps("(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory")
    try:
        return int(float(out) / 1024.0)
    except Exception:
        return None


def read_cpu():
    out = _ps("(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average")
    cpus = _ps("(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum")
    load = None
    try:
        load = round(float(out), 2)
    except Exception:
        pass
    try:
        cpus = int(float(cpus))
    except Exception:
        cpus = None
    return load, cpus


def read_pagefiles():
    out = _ps(
        "Get-CimInstance Win32_PageFileUsage | "
        "ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.Name,$_.CurrentUsage,$_.AllocatedBaseSize,$_.PeakUsage }")
    result = {}
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 4:
            continue
        name = parts[0].strip().lower()
        drive = "c" if name.startswith("c:") else ("e" if name.startswith("e:") else "other")
        try:
            result[drive] = (int(parts[1]), int(parts[2]), int(parts[3]))
        except Exception:
            pass
    return result


def perfmon_available():
    """typeperf 单次采样（仅当可用）。"""
    try:
        r = subprocess.run(
            ["typeperf"] + PERFMON_COUNTERS + ["-sc", "1"],
            capture_output=True, timeout=30)
        out = (r.stdout or b"").decode("latin-1", errors="replace")
        err = (r.stderr or b"").decode("latin-1", errors="replace")
        if "No valid counters" in (out + err):
            return None, None, None
        lines = [l for l in out.splitlines() if l.strip()]
        if len(lines) < 2:
            return None, None, None
        vals = lines[-1].split(",")
        nums = []
        for v in vals[1:]:
            try:
                nums.append(float(v.strip()))
            except Exception:
                nums.append(None)
        while len(nums) < 3:
            nums.append(None)
        return nums[0], nums[1], nums[2]
    except Exception:
        return None, None, None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    csv_path = os.path.join(OUT_DIR, "crash_watch_metrics_%s.csv" % stamp)
    print("[OK] CSV 输出: %s" % csv_path)
    print("[OK] 间隔 %ds，Ctrl+C 停止" % INTERVAL)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        fh.flush()

        # 启动时先探一次 PerfMon，确定主口径
        pm = perfmon_available()
        perfmon_ok = pm[0] is not None
        print("[*] PerfMon(typeperf) 可用 = %s" % perfmon_ok)
        if not perfmon_ok:
            print("[*] 主口径 = WMI（经典计数器集在本机缺失，locale=zh-CN）")

        count = 0
        while True:
            try:
                avail = read_os_memory()
                load, cpus = read_cpu()
                pf = read_pagefiles()
                pm_avail = pm_cpu = pm_pf = None
                if perfmon_ok:
                    pm_avail, pm_cpu, pm_pf = perfmon_available()

                c = pf.get("c", (None, None, None))
                e = pf.get("e", (None, None, None))
                row = {
                    "timestamp_iso": datetime.datetime.now().isoformat(timespec="seconds"),
                    "available_mbytes": avail,
                    "cpu_load_pct": load,
                    "logical_cpus": cpus,
                    "pagefile_c_used_mb": c[0], "pagefile_c_alloc_mb": c[1], "pagefile_c_peak_mb": c[2],
                    "pagefile_e_used_mb": e[0], "pagefile_e_alloc_mb": e[1], "pagefile_e_peak_mb": e[2],
                    "perfmon_available_mbytes": pm_avail,
                    "perfmon_cpu_pct": pm_cpu,
                    "perfmon_paging_usage_pct": pm_pf,
                    "source": "wmi+perfmon" if perfmon_ok else "wmi",
                }
                writer.writerow(row)
                fh.flush()
                count += 1
                if count % 12 == 0:
                    print("  ... 已采集 %d 条" % count)
            except KeyboardInterrupt:
                print("\n[*] 收到中断，停止采集。")
                break
            except Exception as exc:
                print("[!] 采样异常: %s" % exc)
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
