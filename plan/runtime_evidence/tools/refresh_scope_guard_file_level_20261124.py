# -*- coding: utf-8 -*-
"""Fix the contention column to name the OTHER tasks, and add the generator script to plan/tools."""
import io, sys, os, re
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

REPO = r"E:\2026OPC大赛\flashLoan"
base = os.path.join(REPO, r"plan\tasks\P-01-池子重构\tasks")
SRC_EXT = (".rs", ".py", ".sql")

def read(p):
    raw = io.open(p, "rb").read()
    bom = raw[:3] == b"\xef\xbb\xbf"
    body = raw[3:] if bom else raw
    crlf = body.count(b"\r\n")
    return body.decode("utf-8").replace("\r\n", "\n"), bom, ("\r\n" if crlf else "\n")

def write(p, text, bom, nl):
    out = text.replace("\n", "\r\n") if nl == "\r\n" else text
    io.open(p, "wb").write((("\ufeff" if bom else "") + out).encode("utf-8"))
    print(f"  written: {os.path.relpath(p, REPO)}")

def files_under(rel):
    p = os.path.join(REPO, rel.replace("/", os.sep))
    if os.path.isfile(p): return [rel]
    out = []
    if not os.path.isdir(p): return out
    for dp, dns, fns in os.walk(p):
        dns[:] = [x for x in dns if x not in {"target","__pycache__",".git"}]
        for fn in fns:
            if not fn.endswith(SRC_EXT): continue
            out.append(os.path.relpath(os.path.join(dp, fn), REPO).replace("\\","/"))
    return sorted(out)

DECL, SGPATH = {}, {}
for t in sorted(os.listdir(base)):
    if not t.startswith("T-"): continue
    sg = [os.path.join(base, t, fn) for fn in os.listdir(os.path.join(base, t)) if "SCOPE_GUARD" in fn]
    if not sg: continue
    SGPATH[t] = sg[0]
    txt = io.open(sg[0], encoding="utf-8").read()
    i, j = txt.find("## 1."), txt.find("## 2.")
    rows = []
    for line in txt[i:j].split("\n"):
        if line.startswith("|") and "M-" in line:
            c = [x.strip() for x in line.strip("|").split("|")]
            if len(c) >= 4:
                rows.append((c[0].strip("`"), c[1].strip(), c[2].strip("`").strip("``")))
    DECL[t] = rows

FILEMAP = {}
for t, rows in DECL.items():
    s = set()
    for _, _, p in rows: s.update(files_under(p))
    FILEMAP[t] = sorted(s)
owner = defaultdict(list)
for t, fs in FILEMAP.items():
    for f in fs: owner[f].append(t)

TITLE = "## 1.1 代码落点（**文件级** · 并行判定用）"
for t in sorted(DECL):
    p = SGPATH[t]
    txt, bom, nl = read(p)
    if TITLE not in txt: continue
    i = txt.find(TITLE)
    j = txt.find("## 2. 逐模块回归命令")
    if j < 0: raise SystemExit(f"[{t}] no anchor")

    bymod = defaultdict(list)
    for mod, _, path in DECL[t]:
        bymod[mod].extend(files_under(path))

    L = []
    L.append(TITLE); L.append("")
    L.append("> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，")
    L.append("> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。")
    L.append("> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，")
    L.append("> **不靠人工枚举**；重跑该脚本即可刷新。")
    L.append(">")
    L.append("| 模块 | 代码落点（文件级） | 与他任务**同文件** |")
    L.append("|---|---|---|")
    for mod in sorted(bymod, key=lambda m: int(m.replace("M-",""))):
        fs = sorted(set(bymod[mod]))
        if not fs: continue
        paths = "<br>".join(f"`{f}`" for f in fs)
        shared_lines = []
        for f in fs:
            others = sorted(set(owner[f]) - {t})
            if others:
                shared_lines.append(f"`{f}`<br>↔ 与 {' / '.join(others)} 同文件")
        cell = "<br>".join(shared_lines) if shared_lines else "**无**（本任务独占）"
        L.append(f"| `{mod}` | {paths} | {cell} |")
    L.append("")
    L.append("**判定规则**：")
    L.append("")
    L.append("| 本任务与他任务的关系 | 判定 | 允许的动作 |")
    L.append("|---|---|---|")
    L.append("| **无同文件**（上表末列 = **无**） | ✅ **可并列** | 独立施工、独立回归、独立提交 |")
    L.append("| **有同文件**，但**改动落在不同函数/不同列** | ⚠️ **可并列，但须错开提交** | 各自改动**同一文件的不同位置** ⇒ **先合者不得覆盖后合者** |")
    L.append("| **有同文件**，且**改动落在同一处** | ⛔ **必须串行** | **模块级**顺序（见 `PLAN.md` §1 前置模块） |")
    L.append("")
    L.append("> **同文件 ≠ 同任务**：同文件只说明**提交要错开**，**不说明**两个任务有功能依赖。")
    L.append("> 功能依赖**只按模块号 `M-n`** 表达（见 `PLAN.md` §1 与 §4）。")
    L.append("")
    L.append("> **本任务的独占文件**（**无争用** ⇒ 本任务可与其它任务**同时施工**）：")
    L.append(">")
    excl = [f for f in FILEMAP[t] if len(owner[f]) == 1]
    if excl:
        for f in excl: L.append(f"> - `{f}`")
    else:
        L.append("> - **无**（本任务的每个文件都与其它任务同文件 ⇒ 提交必须错开）")
    L.append("")

    txt = txt[:i] + "\n".join(L) + txt[j:]
    write(p, txt, bom, nl)
print("OK")
