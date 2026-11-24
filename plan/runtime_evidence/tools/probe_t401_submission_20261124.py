import json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
base = "http://127.0.0.1:5000"
with urllib.request.urlopen(base + "/api/dex/opportunity-scanner/snapshot", timeout=180) as h:
    payload = json.loads(h.read().decode("utf-8", "replace"))
snap = payload.get("snapshot") or {}
inner = snap.get("payload") or {}
print("snapshot keys:", sorted(snap.keys()))
print("payload keys:", sorted(inner.keys()))
for key in ("groupsDiscovered", "managerGroupsAdded", "groupsConsidered", "capacity", "skipped", "failures", "existingGroupKeys", "submitted", "accepted"):
    if key in inner:
        value = inner[key]
        if isinstance(value, list) and len(value) > 8:
            value = value[:8] + ["...(%d)" % len(inner[key])]
        print(key, "=", json.dumps(value, ensure_ascii=False))
workers = (payload.get("scanner") or {}).get("closed_loop_rust_workers") or {}
print("workers:", json.dumps({k: v for k, v in workers.items() if k != "groups"}, ensure_ascii=False)[:1200])
