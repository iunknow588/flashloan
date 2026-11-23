# -*- coding: utf-8 -*-
"""全局清扫：占位符与旧编号表述切到新方案。"""
import io,os,re

def find_root():
    d=os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d,"plan")) and os.path.isdir(os.path.join(d,".git")): return d
        p=os.path.dirname(d)
        if p==d: raise SystemExit("找不到仓库根")
        d=p
ROOT=find_root(); PLAN=os.path.join(ROOT,"plan")

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

files=[]
for root,dirs,fs in os.walk(PLAN):
    dirs[:]=[d for d in dirs if d not in ("archive","back","__pycache__","runtime_evidence")]
    for f in sorted(fs):
        if f.endswith(".md"):
            files.append(os.path.join(root,f))

subs=[
 (r"T-<模块>\.<序>","T-<索引>.<序>"),
 (r"AC-<模块>\.<序>","AC-<索引>.<序>"),
 (r"T-<模块>","T-<索引>"),
 (r"AC-<模块>","AC-<索引>"),
 (r"编号所依据的模块","编号索引"),
 (r"主改动模块决定号段","组合索引（按模块号升序拼接）"),
 (r"主模块定号段","组合索引（按模块号升序拼接）"),
 (r"\*\*跨模块（主改动模块决定号段）\*\*","**跨模块（组合索引）**"),
 (r"\*\*跨模块（主模块定号段）\*\*","**跨模块（组合索引）**"),
 # 旧路径引用（相对与绝对）
 (r"\(\.\./\.\./\.\./\.\./TASK_REGISTRY\.md\)","(../../../../D-001-TASK_REGISTRY.md)"),
 (r"\(\.\./\.\./TASK_REGISTRY\.md\)","(../../D-001-TASK_REGISTRY.md)"),
 (r"\(\./TASK_REGISTRY\.md\)","(./D-001-TASK_REGISTRY.md)"),
 (r"\(\.\./TASK_REGISTRY\.md\)","(../D-001-TASK_REGISTRY.md)"),
 (r"\[`plan/TASK_REGISTRY\.md`\]","[`plan/D-001-TASK_REGISTRY.md`]"),
 (r"`plan/TASK_REGISTRY\.md`","`plan/D-001-TASK_REGISTRY.md`"),
 (r"\](\.\./)*TASK_REGISTRY\.md\)", lambda m: "](%sD-001-TASK_REGISTRY.md)"%("".join("../" for _ in range(m.group(1).count("../"))) if m.group(1) else "")),
]
changed=0
for p in files:
    t,bom,nl=read(p)
    orig=t
    for a,b in subs:
        t=re.sub(a,b,t)
    if t!=orig:
        write(p,t,bom,nl); changed+=1
print("清扫文件：%d"%changed)
