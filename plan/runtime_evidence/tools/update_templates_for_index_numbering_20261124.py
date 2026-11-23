# -*- coding: utf-8 -*-
"""更新模板与 README：占位符与编号规则切到「模块索引 / 组合索引」+ 文档编号。"""
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

TPL=os.path.join(PLAN,"template")
tpls=["TASK_TEMPLATE.md","SCOPE_GUARD_TEMPLATE.md","CHECKLIST_TEMPLATE.md","DOCS_README_TEMPLATE.md","README.md"]

# 1) 占位符：T-<模块>.<序> → T-<索引>.<序>
subs=[
 (r"T-<模块>\.<序>", "T-<索引>.<序>"),
 (r"AC-<模块>\.<序>", "AC-<索引>.<序>"),
 (r"T-<模块>", "T-<索引>"),
 (r"AC-<模块>", "AC-<索引>"),
 (r"`plan/TASK_REGISTRY\.md`", "`plan/D-001-TASK_REGISTRY.md`"),
 (r"\(\.\./D-001-TASK_REGISTRY\.md\)", "(../D-001-TASK_REGISTRY.md)"),
 (r"\[`plan/TASK_REGISTRY\.md`\]\(\.\./TASK_REGISTRY\.md\)", "[`plan/D-001-TASK_REGISTRY.md`](../D-001-TASK_REGISTRY.md)"),
]
changed=[]
for f in tpls:
    p=os.path.join(TPL,f)
    if not os.path.exists(p): continue
    t,bom,nl=read(p)
    orig=t
    for a,b in subs: t=re.sub(a,b,t)
    # 文档编号占位符
    t=t.replace("`{{标题}}` · 开发计划","`D-<nnn>-<名字>.md` · 开发计划")
    if t!=orig:
        write(p,t,bom,nl); changed.append(f)
print("模板更新：",changed)

# 2) TASK_TEMPLATE 的 §1.0 换成新口径
p=os.path.join(TPL,"TASK_TEMPLATE.md")
t,bom,nl=read(p)
flat=t.replace("\r\n","\n")
a=flat.index("## 1.0 编号逻辑")
b=flat.index("## 1. 任务说明")
new = """## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-<索引>.<序>`** |
| **本任务验收编号** | **`AC-<索引>.<序>`**（与任务编号**同号段**） |
| **编号作用域** | `{{模块内（单模块索引） / 跨模块（组合索引）}}` |
| **编号索引** | **`{{索引}}`**（`{{模块索引}}` / `{{组合索引}}`） |
| **序号** | 第 `{{序}}` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `{{该索引号段内的其它任务号，或「无」}}` |

**为什么是这个索引**：`{{说明：只改一个模块 ⇒ 索引 = 该模块索引；改多个模块 ⇒ 索引 = 各模块索引按模块号升序拼接}}`

**规则**：

1. **一个任务只改动一个模块** ⇒ 编号索引 = 该模块索引（模块内编号）。
2. **一个任务改动多个模块** ⇒ 编号索引 = **各模块索引按模块号升序拼接**（组合索引）。
   拼接顺序**按模块号 `M-n`**（`M-3` < `M-4` < … < `M-10`），**不按索引字典序** ——
   否则同一组合会出现两种拼法 ⇒ 编号不可复算。
3. **模块索引表**见 [`plan/D-001-TASK_REGISTRY.md`](../D-001-TASK_REGISTRY.md) §1.1；
   **组合索引规则**见同文件 §1.2。
4. **不同索引的序号互不消耗** —— `方储` 做到 `.09` **不影响** `储` 的 `.04`。
5. **前置与顺序只按模块号 `M-n` 表达** —— **禁止**写「等 `T-x.yy` 完成」。
6. **任务目录名 = `T-<索引>.<序>`** —— 目录名与本节声明必须一致。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-n` | `{{名称}}` | `{{索引}}` |

**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

"""
flat=flat[:a]+new+flat[b:]
out=flat.replace("\n","\r\n") if nl=="\r\n" else flat
write(p,out,bom,nl)
print("TASK_TEMPLATE §1.0 已换成新口径")
