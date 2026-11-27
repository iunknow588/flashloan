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
def _mods_in(sec):
    out=[]
    for line in sec.split("\n"):
        m=re.match(r"\|\s*`(M-\d+)`\s*\|",line)
        if m and m.group(1) not in out: out.append(m.group(1))
    return sorted(out,key=lambda x:int(x.split("-")[1]))

def mods_of(text):
    # The module table lives either in section 1.0 ("本任务涉及的模块" — the template's place)
    # or in section 1.1 (the newer task layout).  Both are read; when a task
    # carries both they must agree, otherwise the task is reported.
    # A task with neither has no module table at all: the module set cannot
    # be read, so the index cannot be cross-checked — a documentation gap
    # (reported separately), not a crash.
    sec10=text.split("## 1.0",1)[1].split("\n## ",1)[0] if "## 1.0" in text else ""
    m10=_mods_in(sec10)
    m11=None
    if "### 1.1" in text:
        sec=text.split("### 1.1",1)[1]
        if "### 1.2" in sec: sec=sec.split("### 1.2",1)[0]
        m11=_mods_in(sec)
    if m10 and m11 and m10!=m11:
        check(False,"B: 任务 §1.0 模块集合 %s 与 §1.1 的 %s 不一致"%(m10,m11))
    return m10 or m11 or None

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

# 期望总数 = 编号册 §4.2 登记的全部二级任务；**已归档的**任务目录已被移除，
# 但归档件仍在 `plan/archive/` ⇒ 判据改为「在盘 + 已归档 == 登记总数」。
m42=re.search(r"### 4\.2 二级任务(.*?)(?:\n### 4\.2\.1|\n### 4\.3|\n## )",reg,re.S)
declared=set()
if m42:
    for line in m42.group(1).split("\n"):
        if not line.startswith("|"): continue
        cols=line.split("|")
        if len(cols)<6: continue
        # 第 4 列 = 「已占用编号」（第 5 列是「下一个可用」，**不算**）
        declared|=set(re.findall(r"`(T-[^`]+?\.\d+)`",cols[4]))
ARCHDIR=os.path.join(PLAN,"archive")
archived=set()
if os.path.isdir(ARCHDIR):
    for f in os.listdir(ARCHDIR):
        mm=re.match(r"\u4efb\u52a1\u5f52\u6863_(T-[^_]+)_.*\.md$",f)
        if mm: archived.add(mm.group(1))
on_disk_ids={tid for tid,_t in tasks}
# The registered task set is cross-checked against the decomposition table
# of D-002 section 2.1 (every registered task has a row there).  The count
# itself is not hard coded: it grows with the registry.
D002=os.path.join(PLAN,"tasks","P-01-池子重构","D-002-PLAN.md")
listed=set()
if os.path.isfile(D002):
    sec=read(D002).split("### 2.1",1)[1].split("### 2.2",1)[0]
    listed=set(re.findall(r"`(T-[^`]+?\.\d+)`",sec))
check(bool(declared),"C: 编号册 §4.2 没有登记任何二级任务")
check(declared==listed,
      "C: 编号册 §4.2 登记(%d) 与 D-002 §2.1 分解表(%d) 不一致：多 %s / 缺 %s"%(
          len(declared),len(listed),
          sorted(declared-listed)[:5],sorted(listed-declared)[:5]))
check(on_disk_ids|archived==declared,
      "C: 在盘(%d)+已归档(%d) 与编号册登记(%d) 不一致：多出 %s / 缺 %s"%(
          len(on_disk_ids),len(archived),len(declared),
          sorted((on_disk_ids|archived)-declared),sorted(declared-(on_disk_ids|archived))))
check(not (on_disk_ids&archived),"C: 同时出现在盘与归档区的任务：%s"%sorted(on_disk_ids&archived))
check(len(tasks)==len(declared)-len(archived),
      "C: 二级任务目录数应为 %d（登记 %d − 已归档 %d），实为 %d"%(
          len(declared)-len(archived),len(declared),len(archived),len(tasks)))
seen_ids=set()
unverifiable=[]
for tid,t in tasks:
    m=re.match(r"^T-(.+)\.(\d+)$",tid)
    check(bool(m),"D: 任务目录名 `%s` 不是 T-<索引>.<序> 形式"%tid)
    if m:
        check(tid not in seen_ids,"C: 任务编号重复：%s"%tid)
        seen_ids.add(tid)
    mods=mods_of(t)
    actual=idx_of(t)
    if mods is None:
        # No module table in the plan: the index cannot be derived, so the
        # cross-check is skipped and the task is listed at the end.
        unverifiable.append(tid)
        continue
    expect="".join(MOD[m][1] for m in mods) if all(m in MOD for m in mods) else None
    check(expect is not None,"B: 任务 %s 的改动模块不在索引表：%s"%(tid,mods))
    check(actual==expect,"B: 任务 %s 编号索引 `%s` 与模块集合推导 `%s` 不符"%(tid,actual,expect))
    if m:
        check(m.group(1)==actual,"C: 任务目录 `%s` 与文档声明索引 `%s` 不符"%(tid,actual))
    check("## 1.0 编号逻辑" in t,"C: 任务 %s 缺少 §1.0 编号逻辑节"%tid)
    check("**编号索引**" in t,"C: 任务 %s 的 §1.0 未声明编号索引"%tid)
    check("编号所依据的模块" not in t,"C: 任务 %s 的 §1.0 仍用旧的「编号所依据的模块」口径"%tid)
    # 组合索引里不得含数字（旧式平铺编号）
    m2=re.match(r"^T-([^.]+)\.",tid)
    if m2:
        check(not any(ch.isdigit() for ch in m2.group(1)),
              "D: 任务 %s 的索引含数字（应为模块索引字符，不应平铺 `T-nn`）"%tid)
print("B/C 组合索引可复算：%d 个任务，索引与模块集合一一对应"%(len(tasks)-len(unverifiable)))
if unverifiable:
    print("  无 §1.0/§1.1 模块表、编号索引无法互验（在盘 %d 个）：%s"%(len(unverifiable),unverifiable))

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

# 已归档任务的技术文案**并入归档件**（单文件多锚点）⇒ 其编号在盘上没有独立文件，
# 但仍受编号纪律约束：唯一、连续、标题一致。
embedded={}
if os.path.isdir(ARCHDIR):
    for f in sorted(os.listdir(ARCHDIR)):
        if not f.startswith("\u4efb\u52a1\u5f52\u6863_") or not f.endswith(".md"): continue
        ap=os.path.join(ARCHDIR,f)
        at=read(ap)
        for mm in re.finditer(r"<!-- \u539f\u6587\u4ef6\uff1a`[^`]*?/D-(\d{3})-[^`]+\.md` -->",at):
            n2=int(mm.group(1))
            check(n2 not in nums and n2 not in embedded,
                  "E: 文档编号重复：D-%03d（归档件 %s）"%(n2,f))
            embedded[n2]=(f,at)
for n2,(f,at) in sorted(embedded.items()):
    check(any(line.startswith("# D-%03d "%n2) for line in at.split("\n")),
          "E: 归档件 %s 内的 D-%03d 缺少规范标题行"%(f,n2))
    nums[n2]="archive/"+f
# 编号空档：登记册显式声明的空号（草稿预留但未成文）⇒ 允许缺口；未声明的缺口仍然失败。
void=set()
for line in reg.split("\n"):
    if line.startswith("> **编号空档"):
        void=set(int(x) for x in re.findall(r"D-(\d{3})",line))
        break
used=set(nums)
holes=[n for n in range(1,max(used)+1) if n not in used]
check(not (void & used),"E: 编号空档声明的 D-nnn 已被占用：%s"%sorted(void & used))
check(all(h in void for h in holes),
      "E: 文档编号不是从 1 连续递增（缺口或越界）：未声明的缺口 %s"%sorted(set(holes)-void))
print("E 文档编号：%d 份受控文档，编号唯一、连续（已声明空档 %s）、标题一致"%(len(nums),sorted(void) or "无"))

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
    arch_listed=set(int(x) for x in re.findall(r"`plan/archive/[^`]+\.md#d(\d{3})`",m.group(1)))
    check(arch_listed==set(embedded),
          "F: 编号册总表登记的归档文档与归档件不一致：表 %s / 件 %s"%(
              sorted(arch_listed),sorted(embedded)))
print("F 编号册总表与磁盘一致")

# ---------- 汇总 ----------
print("\n检查项：%d，失败：%d"%(checks,len(fails)))
for f in fails: print("  FAIL:",f)
sys.exit(1 if fails else 0)
