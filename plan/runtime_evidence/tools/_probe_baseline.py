import json, sys
sys.stdout.reconfigure(encoding="utf-8")
d = json.load(open("plan/runtime_evidence/t401_review_integrity_baseline.json", encoding="utf-8"))
acc = d.get("acceptance", {})
print("acceptance keys:", list(acc.keys())[:20])
items = acc.get("items") or acc.get("checks") or []
print("n items:", len(items))
for it in items:
    if isinstance(it, dict):
        print("  %-8s %-46s %s" % (it.get("status"), (it.get("id") or it.get("name") or "")[:46],
                                    (it.get("detail") or it.get("note") or "")[:90]))
    else:
        print("  ", it)
print("--- checklist section ---")
ck = d.get("checklist")
print(json.dumps(ck, ensure_ascii=False, indent=1)[:4000])
