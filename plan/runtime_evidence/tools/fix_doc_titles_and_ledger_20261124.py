# -*- coding: utf-8 -*-
"""补齐缺失的文档标题行（编号 + 名字），并重建编号册 §5.5 文档总表（用改名后的真实路径）。"""
import io,os,re

def find_root():
    d=os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d,"plan")) and os.path.isdir(os.path.join(d,".git")): return d
        p=os.path.dirname(d)
        if p==d: raise SystemExit("找不到仓库根")
        d=p
ROOT=find_root(); PLAN=os.path.join(ROOT,"plan")
REG=os.path.join(PLAN,"D-001-TASK_REGISTRY.md")
TASKDIR=os.path.join(PLAN,"tasks","P-01-池子重构","tasks")

def read(p):
    raw=open(p,"rb").read()
    bom=raw[:3]==b"\xef\xbb\xbf"
    t=raw.decode("utf-8-sig")
    return t,bom,("\r\n" if "\r\n" in t else "\n")
def write(p,t,bom,nl):
    if nl=="\r\n": t=t.replace("\r\n","\n").replace("\n","\r\n")
    d=t.encode("utf-8")
    if bom: d=b"\xef\xbb\xbf"+d
    io.open(p,"wb").write(d)

# ---------- 1) 补标题 ----------
fixed=[]
for tid in sorted(os.listdir(TASKDIR)):
    d=os.path.join(TASKDIR,tid)
    if not os.path.isdir(d): continue
    for f in sorted(os.listdir(d)):
        if not f.endswith("checklist.md"): continue
        p=os.path.join(d,f)
        t,bom,nl=read(p)
        flat=t.replace("\r\n","\n")
        lines=flat.split("\n")
        if lines and lines[0].startswith("# "): continue     # 已有标题
        m=re.match(r"^D-(\d{3})-checklist\.md$",f)
        if not m: continue
        n=m.group(1)
        # 标题取该任务 PLAN.md 的标题
        pf=[x for x in os.listdir(d) if x.endswith("PLAN.md")]
        title=tid
        if pf:
            pt=read(os.path.join(d,pf[0]))[0].replace("\r\n","\n")
            mt=re.search(r"\|\s*\*\*标题\*\*\s*\|\s*([^|]+)\|",pt)
            if mt: title=mt.group(1).strip()
        newtitle="# D-%s %s｜%s · 测试目标 check list"%(n,tid,title)
        # 保留原有空行结构：把标题插到首个非空行之前
        idx=next((i for i,l in enumerate(lines) if l.strip()),len(lines))
        newlines=lines[:idx]+[newtitle,""]+lines[idx:]
        out="\n".join(newlines)
        if nl=="\r\n": out=out.replace("\n","\r\n")
        write(p,out,bom,nl)
        fixed.append((tid,n))
print("补齐标题：%d 个 checklist"%len(fixed))

# ---------- 2) 重建 §5.5 总表 ----------
raw=open(REG,"rb").read()
bom=raw[:3]==b"\xef\xbb\xbf"
nl="\r\n" if "\r\n" in raw.decode("utf-8-sig") else "\n"
reg=raw.decode("utf-8-sig").replace("\r\n","\n")

docs=[]
for root,dirs,fs in os.walk(PLAN):
    dirs[:]=[d for d in dirs if d not in ("archive","back","__pycache__","runtime_evidence")]
    for f in sorted(fs):
        if not f.endswith(".md"): continue
        p=os.path.join(root,f)
        rel=os.path.relpath(p,ROOT).replace("\\","/")
        if f=="README.md" or f.endswith("_TEMPLATE.md"): continue
        m=re.match(r"^D-(\d{3})-(.+)\.md$",f)
        if not m: continue
        docs.append((int(m.group(1)),rel,f))
docs.sort()
owner={}
for n,rel,f in docs:
    if rel=="plan/D-001-TASK_REGISTRY.md": o="体系"
    elif "/tasks/P-01-池子重构/tasks/" in rel: o="P-01 "+rel.split("/tasks/P-01-池子重构/tasks/")[1].split("/")[0]
    elif "/tasks/P-01-池子重构/docs/" in rel: o="P-01 专题"
    else: o="P-01 一级"
    owner[rel]=o
tbl=["| 编号 | 名字（**可自由替换**） | 归属 | 路径 |","|---|---|---|---|"]
for n,rel,f in docs:
    name=f.split("-",2)[2][:-3]
    tbl.append("| `D-%03d` | %s | %s | `%s` |"%(n,name,owner[rel],rel))

a=reg.index("### 5.5 文档总表")
b=reg.index("\n\n---",a)
new="### 5.5 文档总表（**编号唯一真相源**）\n\n> 新增文档 ⇒ 在此登记一行，编号取**下一个可用**（本表尾号 + 1）。\n\n"+"\n".join(tbl)
reg=reg[:a]+new+reg[b:]
out=reg.replace("\n","\r\n") if nl=="\r\n" else reg
d=out.encode("utf-8")
if bom: d=b"\xef\xbb\xbf"+d
io.open(REG,"wb").write(d)
print("§5.5 重建：%d 行"%len(docs))
