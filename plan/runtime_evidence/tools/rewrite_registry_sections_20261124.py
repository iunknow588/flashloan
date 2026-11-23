# -*- coding: utf-8 -*-
"""重写编号册 §2（组合索引判定规则）与 §7（迁移记录）。"""
import io,os,re

def find_root():
    d=os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d,"plan")) and os.path.isdir(os.path.join(d,".git")): return d
        p=os.path.dirname(d)
        if p==d: raise SystemExit("找不到仓库根")
        d=p
ROOT=find_root(); REG=os.path.join(ROOT,"plan","D-001-TASK_REGISTRY.md")

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

t,bom,nl=read(REG); flat=t.replace("\r\n","\n")

new2 = """## 2. 编号索引的判定规则（**可复算，不许挂靠**）

一个二级任务的**编号索引**由它的**改动模块集合**唯一决定，**无需人工裁决**：

1. **只改一个模块** ⇒ 索引 = 该模块的索引（模块内索引）；
2. **改多个平级模块** ⇒ 索引 = **各模块索引按模块号 `M-n` 升序拼接**（组合索引）；
3. **改的是平级模块却无法拆开** ⇒ **拆成两个任务**，不要合并；
4. **不允许**「主模块 A ＋ 附带 B」这种**挂靠式**组合 ——
   被强制牵连的模块**也进组合索引**，因为它确实被改动了。

> **为什么不再设「主改动模块」**：旧口径用「主改动模块」决定号段，
> 会让**被强制牵连**的模块**查不到自己的任务**（编号只显示主模块）⇒
> 回归范围与编号**对不上**。
> 组合索引把**所有被改动的模块**都显式写进编号 ⇒ **看编号即知回归范围**。

> **反例（本次已发生）**：`T-储.01`「测试套件生产库零写入」与 `T-储.02`「记录存储剥离」
> 同属 `M-10`，却因旧编号按创建顺序分配而**不相邻**，且与池子类任务混在同一序列里。
> ⇒ 新口径下它们归入 `T-储.xx` 索引段，**与池子类的 `T-方*.xx` 段完全分开**。

---

"""

a=flat.index("## 2. 主改动模块")
b=flat.index("## 3. 一条硬规则",a)
flat=flat[:a]+new2+flat[b:]

# §7 迁移记录
a2=flat.index("## 7. 迁移记录")
new7 = """## 7. 迁移记录

| 日期 | 变更 |
|---|---|
| 2026-11-24 | **建立编号册**；P-01 的 19 个二级任务重编为「按模块分组的 `T-<模块>.<序>`」（含新增 `T-储.03` 复核库分离、`T-储.04` 广播交易记录分离） |
| 2026-11-24 | **引入模块索引与组合索引**：新增 §1.1（模块索引表，11 个模块，各 1 个 ≤2 字符索引）与 §1.2（组合索引规则）。19 个二级任务由「主改动模块决定号段」改为「**编号索引 = 改动模块索引按模块号升序拼接**」，9 个跨模块任务改号 |
| 2026-11-24 | **引入文档编号**：新增 §5（`D-<nnn>-<名字>.md`，编号唯一检索、名字可自由替换；`D-0` 为内部综合文档保留号）。97 份受控文档编号化，文件名与标题行同步，214 条链接全部重指向 |

> **重编不改范围、不改验收、不改测试。** 只改**编号**（纯 ID）与**编号册登记**。
> 旧编号与新编号的对照见 `plan/tasks/P-01-池子重构/docs/D-015-编号对照表_20261124.md`。
"""
flat=flat[:a2]+new7
out=flat.replace("\n","\r\n") if nl=="\r\n" else flat
write(REG,out,bom,nl)
print("§2 / §7 已重写")
