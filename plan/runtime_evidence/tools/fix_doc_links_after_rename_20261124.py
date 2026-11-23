# -*- coding: utf-8 -*-
"""修复文档改名后残留的失效链接（含 README 等 D-0 内部文档）。"""
import io,json,os,re

def _find_root():
    """向上找同时含 plan/ 与 .git/ 的目录 = 仓库根。"""
    d=os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d,"plan")) and os.path.isdir(os.path.join(d,".git")):
            return d
        parent=os.path.dirname(d)
        if parent==d: raise SystemExit("找不到仓库根")
        d=parent

ROOT=_find_root()
PLAN=os.path.join(ROOT,"plan")

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

def rel(p): return os.path.relpath(p).replace("\\","/")

MAP=os.path.join(ROOT,"_docmap.json")
dm=json.load(open(MAP,encoding="utf-8"))
old2new={rel(p):rel(os.path.join(os.path.dirname(p),info["new"])) for p,info in dm.items()}

link_re=re.compile(r"(\[[^\]]*\]\()([^)]*?\.md)(#[^)]*)?(\))")
fixed=0;changed=[]
_scanned=0;_links=0;_dbg=[0]
for root,dirs,fs in os.walk(PLAN):
    dirs[:]=[d for d in dirs if d not in ("archive","back","__pycache__")]
    for f in sorted(fs):
        if not f.endswith(".md"): continue
        path=os.path.join(root,f)
        src=rel(path); srcdir=os.path.dirname(src)
        t,bom,nl=read(path)
        _scanned+=1
        cnt=[0]
        def fix(m):
            prefix,target,anchor,suffix=m.groups()
            if target.startswith(("http://","https://","mailto:","#")): return m.group(0)
            resolved=os.path.normpath(os.path.join(srcdir,target)).replace("\\","/")
            if resolved in old2new:
                nb=os.path.basename(old2new[resolved])
                head=target[:len(target)-len(os.path.basename(target))]
                cnt[0]+=1
                return prefix+head+nb+(anchor or "")+suffix
            return m.group(0)
        _links+=len(link_re.findall(t))
        new=link_re.sub(fix,t)
        if cnt[0]:
            write(path,new,bom,nl); fixed+=cnt[0]; changed.append((src,cnt[0]))
print("scanned files:",_scanned,"links seen:",_links)
print("fixed links:",fixed,"files:",len(changed))
for s,c in changed[:10]: print("  ",c,s)
