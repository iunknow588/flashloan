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
 r"plan/tasks/P-01-池子重构/tasks/T-储.01/D-032-PLAN.md",
 r"plan/tasks/P-01-池子重构/tasks/T-储.01/D-033-SCOPE_GUARD.md",
 r"plan/tasks/P-01-池子重构/tasks/T-储.01/D-034-checklist.md",
 r"plan/tasks/P-01-池子重构/tasks/T-储.01/docs/D-035-现状与可行性分析.md",
 r"plan/D-001-TASK_REGISTRY.md",
 r"plan/tasks/P-01-池子重构/D-002-PLAN.md",
 r"plan/tasks/P-01-池子重构/docs/D-011-check_list_20261123.md",
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
    if "\ufffd" in text:
        for i, line in enumerate(text.splitlines(), start=1):
            if "\ufffd" in line:
                print("  U+FFFD %s:%d %s" % (p, i, line.strip()[:160]))
