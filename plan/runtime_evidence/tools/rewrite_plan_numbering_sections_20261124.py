# -*- coding: utf-8 -*-
"""重写各任务 PLAN.md 的 §1.0 编号逻辑节：改为「组合索引」口径。"""
import io,os,re

ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAN=os.path.join(ROOT,"plan")
TASKS=os.path.join(PLAN,"tasks","P-01-池子重构","tasks")
REG=os.path.join(PLAN,"TASK_REGISTRY.md")

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

reg=read(REG)[0]
MOD={}
for line in reg.split("\n"):
    m=re.match(r"\|\s*`(M-\d+)`\s*\|([^|]*)\|\s*`([^`]+)`\s*\|",line)
    if m: MOD[m.group(1)]=(m.group(2).strip(),m.group(3).strip())
def mn(x): return int(x.split("-")[1])

def mods_of(text):
    sec=text.split("### 1.1",1)[1]
    if "### 1.2" in sec: sec=sec.split("### 1.2",1)[0]
    out=[]
    for line in sec.split("\n"):
        m=re.match(r"\|\s*`(M-\d+)`\s*\|",line)
        if m and m.group(1) not in out: out.append(m.group(1))
    return sorted(out,key=mn)

# group by combination index; order groups by first module number, then index text
groups={}
for name in sorted(os.listdir(TASKS)):
    d=os.path.join(TASKS,name)
    if not os.path.isdir(d): continue
    t=read(os.path.join(d,"PLAN.md"))[0]
    ms=mods_of(t)
    idx="".join(MOD[m][1] for m in ms)
    groups.setdefault(idx,[]).append({"name":name,"dir":d,"mods":ms})

def group_key(idx):
    return (mn(groups[idx][0]["mods"][0]), len(groups[idx][0]["mods"]), idx)

new_sec={}
for idx in sorted(groups,key=group_key):
    tasks=groups[idx]
    for i,task in enumerate(tasks,1):
        name=task["name"]; d=task["dir"]; ms=task["mods"]
        tid=name                      # e.g. T-方授储.01
        acc="AC-"+name.split("-",1)[1]
        scope="**跨模块（组合索引）**" if len(ms)>1 else "**模块内（单模块索引）**"
        idx_desc="、".join("`%s`（%s）"%(m,MOD[m][0]) for m in ms)
        others=[t["name"] for t in tasks if t["name"]!=name]
        others_txt="、".join("`%s`"%o for o in others) if others else "**无**"
        rows="\n".join("| `%s` | %s | `%s` |"%(m,MOD[m][0],MOD[m][1]) for m in ms)
        if len(ms)>1:
            why=("**为什么是这个组合索引**：本任务**同时改动 %d 个模块**（%s）⇒ "
                 "按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`%s`**。"
                 % (len(ms),"、".join("`%s`"%m for m in ms),idx))
            note=("本任务涉及的每个模块**都是平级改动**（不是被强制牵连）⇒ "
                  "**全部进入组合索引**。")
        else:
            why=("本任务**只改动一个模块** `%s`（%s）⇒ "
                 "编号索引即该模块索引 **`%s`**。"%(ms[0],MOD[ms[0]][0],idx))
            note=""
        sec = """## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`%s`** |
| **本任务验收编号** | **`%s`**（与任务编号**同号段**） |
| **编号作用域** | %s |
| **编号索引** | **`%s`**（%s） |
| **序号** | 第 `%02d` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | %s |

%s

%s

**规则**：

1. **一个任务只改动一个模块** ⇒ 编号索引 = 该模块索引（模块内编号）。
2. **一个任务改动多个模块** ⇒ 编号索引 = **各模块索引按模块号升序拼接**（组合索引）。
   拼接顺序**按模块号 `M-n`**（`M-3` < `M-4` < … < `M-10`），**不按索引字典序** ——
   否则同一组合会出现两种拼法 ⇒ 编号不可复算。
3. **模块索引表**见 [`plan/TASK_REGISTRY.md`](../../../../TASK_REGISTRY.md) §1.1；
   **组合索引规则**见同文件 §1.2。
4. **不同索引的序号互不消耗** —— `方储` 做到 `.09` **不影响** `储` 的 `.04`。
5. **前置与顺序只按模块号 `M-n` 表达** —— **禁止**写「等 `T-x.yy` 完成」。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 |
|---|---|---|
%s
%s

**登记**：本任务的号段占用情况登记在
[`plan/TASK_REGISTRY.md`](../../../../TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

""" % (tid,acc,scope,idx,idx_desc,i,others_txt,why,note,rows,"")
        new_sec[os.path.join(d,"PLAN.md")]=sec

changed=0
for path,sec in new_sec.items():
    t,bom,nl=read(path)
    a=t.find("## 1.0 编号逻辑")
    b=t.find("## 1. 身份与范围")
    if a<0 or b<0:
        print("SKIP:",path); continue
    write(path,t[:a]+sec+t[b:],bom,nl)
    changed+=1
print("rewritten PLAN.md §1.0:",changed)
