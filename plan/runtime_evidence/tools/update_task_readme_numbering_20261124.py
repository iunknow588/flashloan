# -*- coding: utf-8 -*-
"""更新各二级任务 README 的「编号逻辑」行：改为组合索引口径。"""
import io,os,re

def find_root():
    d=os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d,"plan")) and os.path.isdir(os.path.join(d,".git")): return d
        p=os.path.dirname(d)
        if p==d: raise SystemExit("找不到仓库根")
        d=p
ROOT=find_root(); T=os.path.join(ROOT,"plan","tasks","P-01-池子重构","tasks")

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

changed=0
for tid in sorted(os.listdir(T)):
    d=os.path.join(T,tid)
    if not os.path.isdir(d): continue
    pf=[x for x in os.listdir(d) if x.endswith("PLAN.md")]
    rf=[x for x in os.listdir(d) if x.endswith("README.md")]
    if not pf or not rf: continue
    plan=read(os.path.join(d,pf[0]))[0].replace("\r\n","\n")
    m=re.search(r"\|\s*\*\*编号索引\*\*\s*\|\s*\*\*`([^`]+)`\*\*\s*（([^）]*)",plan)
    if not m: continue
    idx=m.group(1); desc=m.group(2)
    scope="跨模块（组合索引）" if len(idx)>1 else "模块内（单模块索引）"
    rp=os.path.join(d,rf[0])
    t,bom,nl=read(rp)
    flat=t.replace("\r\n","\n")
    new_flat=re.sub(
        r"^\*\*编号逻辑\*\*：.*$",
        "**编号逻辑**：`%s` / `AC-%s` —— **%s**，编号索引 **`%s`**（%s），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）"
        % (tid, tid.split("-",1)[1], scope, idx, desc),
        flat, count=1, flags=re.M)
    if new_flat!=flat:
        out=new_flat.replace("\n","\r\n") if nl=="\r\n" else new_flat
        write(rp,out,bom,nl); changed+=1
print("README 编号逻辑行更新：%d 个"%changed)
