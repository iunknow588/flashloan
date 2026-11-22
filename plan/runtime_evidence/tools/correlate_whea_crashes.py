import re, json, datetime as dt, sys, os

def parse_xml(path):
    raw = open(path, "rb").read().decode("utf-16", errors="replace")
    out = []
    for ev in re.findall(r"<Event\s[^>]*>.*?</Event>", raw, re.S):
        t = re.search(r"<TimeCreated SystemTime='([^']+)'", ev)
        eid = re.search(r"<EventID>(\d+)</EventID>", ev)
        f = dict(re.findall(r"<Data Name='([^']+)'>([^<]*)</Data>", ev))
        out.append({
            "time_created": t.group(1) if t else None,
            "event_id": int(eid.group(1)) if eid else None,
            "fields": f,
        })
    return out

sys_events = parse_xml(sys.argv[1])
whea = json.load(open(sys.argv[2], encoding="utf-8"))

def parse(ts):
    return dt.datetime.strptime(ts[:26].rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")

cpu = [r for r in whea if r.get("sections")
       and any(s["section_id_name"] == "WHEA_PROCESSOR_GENERIC_ERROR_SECTION" for s in r["sections"])]
cpu.sort(key=lambda r: r["time_created"])

e41 = sorted([e for e in sys_events if e["event_id"] == 41], key=lambda e: e["time_created"], reverse=True)
e6008 = sorted([e for e in sys_events if e["event_id"] == 6008], key=lambda e: e["time_created"], reverse=True)

rows = []
print("%-22s %-24s %-8s %-10s %-8s %s" % ("Event41 (UTC)", "nearest CPU WHEA (UTC)", "delta_s", "bugcheck", "len", "severity"))
print("-" * 96)
for e in e41:
    ct = parse(e["time_created"])
    best = None
    for r in cpu:
        delta = (ct - parse(r["time_created"])).total_seconds()
        if 0 <= delta <= 300:
            if best is None or delta < best[0]:
                best = (delta, r)
    bug = e["fields"].get("BugcheckCode")
    if best:
        d, r = best
        sec = [s for s in r["sections"] if s["section_id_name"] == "WHEA_PROCESSOR_GENERIC_ERROR_SECTION"][0]
        print("%-22s %-24s %-8.3f %-10s %-8s %s" % (
            e["time_created"][:19], r["time_created"][:19], d, bug, r["length"], sec["severity_name"]))
        rows.append({
            "event41_utc": e["time_created"][:23],
            "bugcheck_code": bug,
            "bugcheck_params": [e["fields"].get(f"BugcheckParameter{i}") for i in (1,2,3,4)],
            "long_power_button_press_detected": e["fields"].get("LongPowerButtonPressDetected"),
            "sleep_in_progress": e["fields"].get("SleepInProgress"),
            "whea_utc": r["time_created"][:23],
            "delta_seconds_before_crash": round(d, 3),
            "whea_event_id": r["event_id"],
            "whea_record_length": r["length"],
            "whea_section_id_guid": sec["section_id_guid"],
            "whea_section_id_name": sec["section_id_name"],
            "whea_section_length": sec["section_length"],
            "whea_severity": sec["severity_name"],
        })
    else:
        print("%-22s %-24s %-8s %-10s %-8s %s" % (
            e["time_created"][:19], "(no CPU WHEA <=300s)", "-", bug, "-", "-"))
        rows.append({
            "event41_utc": e["time_created"][:23],
            "bugcheck_code": bug,
            "whea_utc": None,
            "delta_seconds_before_crash": None,
            "note": "no CPU-level WHEA record within 300s",
        })

out = sys.argv[3] if len(sys.argv) > 3 else \
    r"E:\2026OPC大赛\flashLoan\plan\runtime_evidence\reports\whea_crash_correlation_20261122.json"
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
json.dump({"event41_count": len(e41), "event6008_count": len(e6008),
           "cpu_whea_record_count": len(cpu), "rows": rows},
          open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print()
print("Event41:", len(e41), " Event6008:", len(e6008), " CPU-WHEA:", len(cpu))
print("matched:", sum(1 for r in rows if r.get("whea_utc")), "/", len(e41))
print("->", out)
