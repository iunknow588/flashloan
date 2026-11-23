# -*- coding: utf-8 -*-
"""为受控文档加「编号 + 名字」：编号用于唯一检索，名字可自由替换。

编号格式： D-<n>-<名字>.md   （n 从 1 递增）
         D-0            = 内部综合文档（README / 模板），保留号，不逐一编号
标题行：   # D-<n> <名字>

用法：
    python plan/runtime_evidence/tools/migrate_doc_ids_20261124.py            # 预演
    python plan/runtime_evidence/tools/migrate_doc_ids_20261124.py --apply
"""
import argparse,io,json,os,re,sys

ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAN=os.path.join(ROOT,"plan")
REG=os.path.join(PLAN,"TASK_REGISTRY.md")
PRIMARY=os.path.join("plan","tasks","P-01-池子重构").replace("\\","/")

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

def is_internal(path):
    """内部综合文档：README 与模板 —— 统一归 D-0，不逐一编号。"""
    b=os.path.basename(path)
    return b=="README.md" or b.endswith("_TEMPLATE.md")

def collect():
    """收集受控文档，按「归属任务」分组，组内按路径排序，保证编号稳定可复算。"""
    docs=[]
    for root,dirs,fs in os.walk(PLAN):
        dirs[:]=[d for d in dirs if d not in ("archive","back","__pycache__","runtime_evidence")]
        for f in sorted(fs):
            if not f.endswith(".md"): continue
            p=os.path.relpath(os.path.join(root,f)).replace("\\","/")
            if is_internal(p): continue
            docs.append(p)
    return sorted(docs)

def owner_of(path):
    """归属：一级任务 / 专题文档区 / 体系级。"""
    if path.startswith("plan/TASK_REGISTRY.md"): return "体系"
    if path.startswith(PRIMARY+"/docs/"): return "P-01 专题"
    if path.startswith(PRIMARY+"/tasks/"):
        parts=path.split("/")
        return parts[4]
    if path.startswith(PRIMARY+"/"): return "P-01 一级"
    return "体系"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--apply",action="store_true")
    ap.add_argument("--report",default="")
    args=ap.parse_args()

    docs=collect()
    ledger={}
    n=0
    # 分组顺序：体系 → P-01 一级 → P-01 专题 → 各二级任务（按任务序）
    def group_key(p):
        o=owner_of(p)
        if o=="体系": return (0,"")
        if o=="P-01 一级": return (1,"")
        if o=="P-01 专题": return (2,"")
        if o.startswith("P-01 T-"): return (3,o)
        return (4,o)
    for p in sorted(docs,key=group_key):
        n+=1
        ledger[p]={"n":n,"owner":owner_of(p)}
    print("受控文档（需编号）：%d" % n)
    for p in sorted(ledger,key=lambda x:ledger[x]["n"]):
        base=os.path.basename(p)
        print("  D-%03d  %-46s  [%s]" % (ledger[p]["n"],base,ledger[p]["owner"]))

    # 计算新文件名
    renames={}
    for p,info in ledger.items():
        d=os.path.dirname(p); base=os.path.basename(p)
        m=re.match(r"^(\d+-)?(.*)$",base)
        stem=m.group(2)
        newbase="D-%03d-%s" % (info["n"],stem)
        renames[p]=os.path.join(d,newbase).replace("\\","/")
    # 冲突检查
    vals=list(renames.values())
    if len(vals)!=len(set(vals)):
        seen=set()
        for v in vals:
            if v in seen: print("冲突:",v); raise SystemExit(1)
            seen.add(v)

    # 改写正文：链接目标 + 标题行
    # 关键：同名文档在多个目录存在（如 01-现状与可行性分析.md 有 13 份），
    #       因此链接必须**按解析后的绝对相对路径**替换，不能按 basename 替换。
    link_re=re.compile(r"(\[[^\]]*\]\()([^)]*?)(\.md)(#[^)]*)?(\))")
    changed=[]
    for p in sorted(docs):
        t,bom,nl=read(p)
        srcdir=os.path.dirname(p)
        def fix_link(m):
            prefix,target,ext,anchor,suffix=m.group(1),m.group(2),m.group(3),m.group(4) or "",m.group(5)
            if target.startswith(("http://","https://","#","mailto:")): return m.group(0)
            resolved=os.path.normpath(os.path.join(srcdir,target)).replace("\\","/")
            if resolved in renames:
                nb=os.path.basename(renames[resolved])
                # 保持原相对写法：替换最后一段文件名
                head=target[:len(target)-len(os.path.basename(target))]
                return prefix+head+nb+ext+anchor+suffix
            return m.group(0)
        new=link_re.sub(fix_link,t)
        # 标题行加编号
        lines=new.split("\n")
        for i,l in enumerate(lines):
            if l.startswith("# "):
                rest=l[2:]
                rest=re.sub(r"^`?D-\d{3}`?\s*","",rest)
                rest=re.sub(r"^`?","",rest)
                lines[i]="# D-%03d %s" % (ledger[p]["n"],rest.strip())
                break
        new="\n".join(lines)
        if new!=t:
            changed.append((p,bom,nl,new))

    print("\n需改写正文：%d 个文档；需改文件名：%d 个" % (len(changed),len(renames)))

    if args.report:
        with io.open(args.report,"w",encoding="utf-8") as h:
            json.dump({p:{"n":ledger[p]["n"],"owner":ledger[p]["owner"],
                          "new":os.path.basename(renames[p])} for p in ledger},
                      h,ensure_ascii=False,indent=1,sort_keys=True)

    if not args.apply:
        print("（预演）加 --apply 执行")
        return 0

    for p,bom,nl,new in changed:
        write(p,new,bom,nl)
    for old,newp in sorted(renames.items()):
        if os.path.exists(old) and not os.path.exists(newp):
            os.rename(old,newp)
    print("\n已改写 %d 个文档，重命名 %d 个文件" % (len(changed),len(renames)))
    return 0

if __name__=="__main__":
    sys.exit(main())
