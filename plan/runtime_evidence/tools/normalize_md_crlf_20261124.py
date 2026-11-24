import sys
sys.stdout.reconfigure(encoding="utf-8")
paths = [
 r"plan/tasks/P-01-池子重构/tasks/T-复编.02/D-047-PLAN.md",
 r"plan/tasks/P-01-池子重构/tasks/T-复编.02/docs/D-104-复核完整性实测.md",
 r"plan/tasks/P-01-池子重构/README.md",
 r"plan/tasks/P-01-池子重构/docs/README.md",
 r"plan/tasks/P-01-池子重构/docs/D-014-未完成任务清单_20261123.md",
 r"plan/tasks/P-01-池子重构/docs/D-013-开发计划_20261123.md",
 r"plan/runtime_evidence/README.md",
]
for p in paths:
    raw = open(p, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8")
    changed = data != raw
    if changed:
        open(p, "wb").write(data)
    print(("CHANGED " if changed else "same    ") + p + ("  (had BOM)" if bom else ""))
