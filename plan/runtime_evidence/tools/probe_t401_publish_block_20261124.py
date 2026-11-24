import collections, json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
with urllib.request.urlopen("http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot", timeout=180) as h:
    payload = json.loads(h.read().decode("utf-8", "replace"))
workers = (payload.get("scanner") or {}).get("closed_loop_rust_workers") or {}
rows = workers.get("workers") or workers.get("groups") or []
print("worker rows:", len(rows))
blocks = collections.Counter()
sample = None
for row in rows:
    if not isinstance(row, dict):
        continue
    blk = row.get("lastReviewPublish") or row.get("last_review_publish")
    if not isinstance(blk, dict):
        blocks["<none>"] += 1
        continue
    key = "%s|%s|%s|%s|%s" % (blk.get("published"), blk.get("reason"), blk.get("evidenceCount"), blk.get("catalogCount"), blk.get("missingCount"))
    blocks[key] += 1
    if sample is None and blk.get("published"):
        sample = blk
for k, v in blocks.most_common(12):
    print(v, "->", k)
if sample:
    print("PUBLISHED SAMPLE:", json.dumps(sample, ensure_ascii=False)[:800])
print("worker keys:", sorted(rows[0].keys())[:40] if rows and isinstance(rows[0], dict) else None)
