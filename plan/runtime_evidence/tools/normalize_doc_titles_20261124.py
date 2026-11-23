# -*- coding: utf-8 -*-
"""把文档标题行归位到首行（去掉标题前的空行），并同步编号册 §5.5 总表路径。"""
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

moved=0
for root,dirs,fs in os.walk(PLAN):
    dirs[:]=[d for d in dirs if d not in ("archive","back","__pycache__","runtime_evidence")]
    for f in sorted(fs):
        if not f.endswith(".md"): continue
        p=os.path.join(root,f)
        t,bom,nl=read(p)
        flat=t.replace("\r\n","\n")
        lines=flat.split("\n")
        # 找首个以 "# " 开头的行
        hi=next((i for i,l in enumerate(lines) if l.startswith("# ")),None)
        if hi is None or hi==0: continue
        title=lines[hi]
        rest=lines[:hi]+lines[hi+1:]
        # 去掉 rest 开头多余空行，标题后保留一个空行
        while rest and not rest[0].strip(): rest.pop(0)
        newlines=[title,""]+rest
        out="\n".join(newlines)
        if nl=="\r\n": out=out.replace("\n","\r\n")
        write(p,out,bom,nl)
        moved+=1
print("标题归位：%d 个文档"%moved)

# 重建 §5.5
raw=open(REG,"rb").read()
bom=raw[:3]==b"\xef\xbb\xbf"
nl="\r\n" if "\r\n" in raw.decode("utf-8-sig") else "\n"
reg=raw.decode("utf-8-sig").replace("\r\n","\n")
docs=[]
for root,dirs,fs in os.walk(PLAN):
    dirs[:]=[d for d in dirs if d not in ("archive","back","__pycache__","runtime_evidence")]
    for f in sorted(fs):
        if not f.endswith(".md"): continue
        rel=os.path.relpath(os.path.join(root,f),ROOT).replace("\\","/")
        if f=="README.md" or f.endswith("_TEMPLATE.md"): continue
        m=re.match(r"^D-(\d{3})-(.+)\.md$",f)
        if m: docs.append((int(m.group(1)),rel,f))
docs.sort()
def owner_of(rel):
    if rel=="plan/D-001-TASK_REGISTRY.md": return "体系"
    key="/tasks/P-01-池子重构/tasks/"
    if key in rel: return "P-01 "+rel.split(key)[1].split("/")[0]
    if "/tasks/P-01-池子重构/docs/" in rel: return "P-01 专题"
    return "P-01 一级"
tbl=["| 编号 | 名字（**可自由替换**） | 归属 | 路径 |","|---|---|---|---|"]
for n,rel,f in docs:
    tbl.append("| `D-%03d` | %s | %s | `%s` |"%(n,f.split("-",2)[2][:-3],owner_of(rel),rel))
a=reg.index("### 5.5 文档总表"); b=reg.index("\n\n---",a)
reg=reg[:a]+("### 5.5 文档总表（**编号唯一真相源**）\n\n> 新增文档 ⇒ 在此登记一行，编号取**下一个可用**（本表尾号 + 1）。\n\n"+"\n".join(tbl))+reg[b:]
out=reg.replace("\n","\r\n") if nl=="\r\n" else reg
d=out.encode("utf-8")
if bom: d=b"\xef\xbb\xbf"+d
io.open(REG,"wb").write(d)
print("§5.5：%d 行"%len(docs))
