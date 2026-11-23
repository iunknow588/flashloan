# -*- coding: utf-8 -*-
"""校验任务编号逻辑（新方案：模块索引 / 组合索引）与文档编号（D-nnn）。

校验项：
  A. 模块索引表：唯一、≤2 字符、不复用
  B. 组合索引可复算：按模块号升序拼接，与任务目录里的编号索引一致
  C. 任务目录名 == 文档里声明的编号；每个二级任务目录都有 §1.0 编号逻辑
  D. 无平铺的旧式 `T-nn` 编号（组合索引不得含数字）
  E. 文档编号 D-nnn：全局唯一、从 1 递增、目录名与标题行一致
  F. 文档编号唯一真相源（编号册 §5.5 总表）与磁盘一致
"""
import io,os,re,sys

def find_root():
    d=os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d,"plan")) and os.path.isdir(os.path.join(d,".git")):
            return d
        p=os.path.dirname(d)
        if p==d: raise SystemExit("找不到仓库根")
        d=p

ROOT=find_root()
PLAN=os.path.join(ROOT,"plan")
REG=os.path.join(PLAN,"D-001-TASK_REGISTRY.md")
TASKDIR=os.path.join(PLAN,"tasks","P-01-池子重构","tasks")

def read(p):
    return open(p,"rb").read().decode("utf-8-sig").replace("\r\n","\n")

fails=[];checks=0
def check(cond,msg):
    global checks
    checks+=1
    if not cond: fails.append(msg)

reg=read(REG)

# ---------- A. 模块索引表 ----------
MOD={}
for line in reg.split("\n"):
    m=re.match(r"\|\s*`(M-\d+)`\s*\|([^|]*)\|\s*`([^`]+)`\s*\|",line)
    if m: MOD[m.group(1)]=(m.group(2).strip(),m.group(3).strip())
check(len(MOD)>=11,"A: 模块索引表为空或过少（%d）"%len(MOD))
idxs=[v[1] for v in MOD.values()]
check(len(set(idxs))==len(idxs),"A: 模块索引不唯一")
for k,(name,idx) in MOD.items():
    check(len(idx)<=2,"A: 模块 %s 的索引 `%s` 超过 2 字符"%(k,idx))
    check(idx.strip()==idx and idx!="","A: 模块 %s 索引为空或含空格"%k)
print("A 模块索引：%d 个模块，索引唯一且 ≤2 字符"%len(MOD))

# ---------- B/C. 任务编号与组合索引 ----------
def mods_of(text):
    sec=text.split("### 1.1",1)[1]
    if "### 1.2" in sec: sec=sec.split("### 1.2",1)[0]
    out=[]
    for line in sec.split("\n"):
        m=re.match(r"\|\s*`(M-\d+)`\s*\|",line)
        if m and m.group(1) not in out: out.append(m.group(1))
    return sorted(out,key=lambda x:int(x.split("-")[1]))

def idx_of(text):
    m=re.search(r"\|\s*\*\*编号索引\*\*\s*\|\s*\*\*`([^`]+)`\*\*",text)
    return m.group(1) if m else None

tasks=[]
for tid in sorted(os.listdir(TASKDIR)):
    d=os.path.join(TASKDIR,tid)
    if not os.path.isdir(d): continue
    pf=[x for x in os.listdir(d) if x.endswith("PLAN.md")]
    if not pf: continue
    t=read(os.path.join(d,pf[0]))
    tasks.append((tid,t))

check(len(tasks)==19,"C: 二级任务目录数应为 19，实为 %d"%len(tasks))
seen_ids=set()
for tid,t in tasks:
    mods=mods_of(t)
    expect="".join(MOD[m][1] for m in mods) if all(m in MOD for m in mods) else None
    actual=idx_of(t)
    check(expect is not None,"B: 任务 %s 的改动模块不在索引表：%s"%(tid,mods))
    check(actual==expect,"B: 任务 %s 编号索引 `%s` 与模块集合推导 `%s` 不符"%(tid,actual,expect))
    m=re.match(r"^T-(.+)\.(\d+)$",tid)
    check(bool(m),"D: 任务目录名 `%s` 不是 T-<索引>.<序> 形式"%tid)
    if m:
        check(m.group(1)==actual,"C: 任务目录 `%s` 与文档声明索引 `%s` 不符"%(tid,actual))
        check(tid not in seen_ids,"C: 任务编号重复：%s"%tid)
        seen_ids.add(tid)
    check("## 1.0 编号逻辑" in t,"C: 任务 %s 缺少 §1.0 编号逻辑节"%tid)
    check("**编号索引**" in t,"C: 任务 %s 的 §1.0 未声明编号索引"%tid)
    check("编号所依据的模块" not in t,"C: 任务 %s 的 §1.0 仍用旧的「编号所依据的模块」口径"%tid)
    # 组合索引里不得含数字（旧式平铺编号）
    m2=re.match(r"^T-([^.]+)\.",tid)
    if m2:
        check(not any(ch.isdigit() for ch in m2.group(1)),
              "D: 任务 %s 的索引含数字（应为模块索引字符，不应平铺 `T-nn`）"%tid)
print("B/C 组合索引可复算：%d 个任务，索引与模块集合一一对应"%len(tasks))

# ---------- E. 文档编号 ----------
docs={}
for root,dirs,fs in os.walk(PLAN):
    dirs[:]=[d for d in dirs if d not in ("archive","back","__pycache__","runtime_evidence")]
    for f in sorted(fs):
        if not f.endswith(".md"): continue
        p=os.path.join(root,f).replace("\\","/")
        rel=os.path.relpath(p,ROOT).replace("\\","/")
        if f=="README.md" or f.endswith("_TEMPLATE.md"):
            continue
        docs[rel]=p
nums={}
for rel,p in docs.items():
    m=re.match(r"^(D-(\d{3}))-(.+)\.md$",os.path.basename(p))
    check(bool(m),"E: 文档 `%s` 文件名不符合 `D-nnn-名字.md`"%rel)
    if not m: continue
    n=int(m.group(2))
    check(n not in nums,"E: 文档编号重复：D-%03d（%s / %s）"%(n,nums.get(n),rel))
    nums[n]=rel
    t=read(p)
    first=t.split("\n")[0]
    check(first.startswith("# D-%03d "%n),"E: 文档 %s 标题行与编号不符：%s"%(rel,first[:50]))
check(sorted(nums)==list(range(1,len(nums)+1)),
      "E: 文档编号不是从 1 连续递增（缺口或越界）")
print("E 文档编号：%d 份受控文档，编号唯一、连续、标题一致"%len(nums))

# ---------- F. 编号册 §5.5 总表与磁盘一致 ----------
m=re.search(r"### 5\.5 文档总表(.*?)(?:\n\n---|\n## )",reg,re.S)
check(bool(m),"F: 编号册缺少 §5.5 文档总表")
if m:
    listed=set(re.findall(r"`(plan/[^`]+\.md)`",m.group(1)))
    on_disk=set(docs.keys())
    only_disk=on_disk-listed
    only_list=listed-on_disk
    check(not only_disk,"F: 编号册总表缺少 %d 份文档：%s"%(len(only_disk),sorted(only_disk)[:5]))
    check(not only_list,"F: 编号册总表登记了不存在的文档 %d 份：%s"%(len(only_list),sorted(only_list)[:5]))
print("F 编号册总表与磁盘一致")

# ---------- 汇总 ----------
print("\n检查项：%d，失败：%d"%(checks,len(fails)))
for f in fails: print("  FAIL:",f)
sys.exit(1 if fails else 0)
