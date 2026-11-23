# -*- coding: utf-8 -*-
"""收尾：把 plan/README.md、template/README.md、一级 PLAN.md 的编号表述切到新方案。"""
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

def patch(rel, pairs):
    p=os.path.join(ROOT,rel)
    t,bom,nl=read(p)
    flat=t.replace("\r\n","\n")
    orig=flat
    for a,b in pairs:
        flat=flat.replace(a,b)
    if flat!=orig:
        out=flat.replace("\n","\r\n") if nl=="\r\n" else flat
        write(p,out,bom,nl)
        print("patched",rel)

patch("plan/README.md",[
 ("2. 确定**主改动模块 `M-n`**（一个任务只服务一个模块），查\n   [`plan/D-001-TASK_REGISTRY.md`](./D-001-TASK_REGISTRY.md) §4.2 取该模块的**下一个可用**编号",
  "2. 确定**本任务改动的模块集合**（一个任务可只改一个，也可改多个平级模块），\n   查 [`plan/D-001-TASK_REGISTRY.md`](./D-001-TASK_REGISTRY.md) §1.1／§1.2 得出**编号索引**\n   （单模块 ⇒ 该模块索引；多模块 ⇒ 按模块号升序拼接的**组合索引**），\n   再查同文件 §4.2 取该索引号段的**下一个可用**编号"),
 ("| **下一个可用任务编号（二级）** | **按模块取**：见 [`TASK_REGISTRY.md`](./D-001-TASK_REGISTRY.md) §4.2 |",
  "| **下一个可用任务编号（二级）** | **按编号索引取**：见 [`D-001-TASK_REGISTRY.md`](./D-001-TASK_REGISTRY.md) §4.2 |"),
 ("| **任务编号册** | `plan/D-001-TASK_REGISTRY.md` | **编号控制的唯一真相源**：模块分组、占用情况、下一个可用编号 |",
  "| **任务编号册** | `plan/D-001-TASK_REGISTRY.md` | **编号控制的唯一真相源**：模块索引表（§1.1）、组合索引规则（§1.2）、占用情况与下一个可用编号（§4.2）、**文档编号总表（§5.5）** |"),
])

patch("plan/template/README.md",[
 ("# 1. 确定「主改动模块 M-n」—— 一个任务只服务一个模块（见编号册 §3）\n# 2. 查 plan/TASK_REGISTRY.md §4.2 ⇒ 取该模块的下一个可用序号",
  "# 1. 确定本任务改动的模块集合（见编号册 §1.1 索引表 / §1.2 组合索引规则）\n# 2. 得出「编号索引」：单模块 ⇒ 该模块索引；多模块 ⇒ 按模块号升序拼接\n# 3. 查 plan/D-001-TASK_REGISTRY.md §4.2 ⇒ 取该索引号段的下一个可用序号"),
 ("# 6. 在 plan/TASK_REGISTRY.md §4.2 登记一行（编号册即时生效）",
  "# 7. 在 plan/D-001-TASK_REGISTRY.md §4.2 登记一行（编号册即时生效）"),
 ("| `{{该模块号段内的其它任务号，或「无」}}` | §1.0 的**同号段其它任务** | **禁止**省略 |",
  "| `{{该索引号段内的其它任务号，或「无」}}` | §1.0 的**同索引其它任务** | **禁止**省略 |"),
 ("| `{{主改动模块 M-n}}` | 本任务**唯一**服务��模块 | **禁止**平铺两个平级模块 |",
  "| `{{模块 M-n}}`（可多行） | 本任务**改动**的模块 —— 平级模块**全部**进组合索引 | **禁止**把平级模块的工作平铺进一个任务 |"),
 ("**编号逻辑**：{{模块内 / 跨模块}}，按 **`M-n`**（{{模块名}}）的号段编号，模块内第 `{{序}}` 个",
  "**编号逻辑**：{{模块内（单模块索引） / 跨模块（组合索引）}}，编号索引 **`{{索引}}`**，该索引号段内第 `{{序}}` 个"),
])

patch("plan/tasks/P-01-池子重构/D-002-PLAN.md",[
 ("| **二级编号的分组依据** | **各二级任务自己的主改动模块**（见各二级任务 `PLAN.md` §1.0） |",
  "| **二级编号的分组依据** | **各二级任务自己的编号索引**（模块内索引 或 组合索引，见各二级任务 `PLAN.md` §1.0） |"),
 ("2. **二级编号 `T-<索引>.<序>` 按其主改动模块分组**；每个二级任务在**自己的** `PLAN.md` §1.0 声明。",
  "2. **二级编号 `T-<索引>.<序>` 按其编号索引分组**；每个二级任务在**自己的** `PLAN.md` §1.0 声明。"),
])
