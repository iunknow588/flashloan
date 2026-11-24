import json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
base = "http://127.0.0.1:5000"
with urllib.request.urlopen(base + "/api/dex/opportunity-scanner/snapshot", timeout=180) as h:
    payload = json.loads(h.read().decode("utf-8", "replace"))
snap = payload.get("snapshot") or {}
print("updated_at:", snap.get("updated_at"))
print("recording:", json.dumps(snap.get("recording"), ensure_ascii=False)[:400])
print("summary:", json.dumps(snap.get("summary"), ensure_ascii=False)[:600])
print("error:", json.dumps(snap.get("error"), ensure_ascii=False)[:400])
print("trigger_context:", json.dumps(snap.get("trigger_context"), ensure_ascii=False)[:600])
workers = (payload.get("scanner") or {}).get("closed_loop_rust_workers") or {}
print("maintenance:", json.dumps(workers.get("maintenance"), ensure_ascii=False, indent=2)[:2000])
print("reviewQueue:", json.dumps(workers.get("reviewQueue"), ensure_ascii=False)[:800])
print("groups:", json.dumps(workers.get("groups"), ensure_ascii=False)[:600])
