# -*- coding: utf-8 -*-
"""校验每个任务目录都**自己**声明了编号逻辑（§1.0）。

受控规则（见 plan/README.md §6 固定部分第 5 项）：
  1. 每个**二级**任务的 PLAN.md 必须有 §1.0 章节；
  2. §1.0 必须声明：编号作用域 / 编号所依据的模块 / 模块内序号 / 同号段其它任务；
  3. 编号作用域取值只能是「模块内」或「跨模块」；
  4. 编号所依据的模块必须与该任务实际改动的主模块一致；
  5. 同号段其它任务必须与磁盘上同模块号段的实际任务一致；
  6. 每个任务的 README.md 必须带一行「编号逻辑」；
  7. 编号唯一性：同一个 T-xx.yy 不得在两个目录出现；
  8. 编号册（plan/TASK_REGISTRY.md）必须登记全部任务，且不得出现平铺 `T-nn`。

退出码 0 = 全部通过；1 = 有失败项。
"""
import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS_ROOT = os.path.join(ROOT, "tasks", "P-01-池子重构", "tasks")
REGISTRY = os.path.join(ROOT, "TASK_REGISTRY.md")

SCOPE_RE = re.compile(r"\|\s*\*\*编号作用域\*\*\s*\|\s*\**([^*]+?)\**\s*\|")
BASE_RE = re.compile(r"\|\s*\*\*编号所依据的模块\*\*\s*\|\s*\**`([^`]+)`\*\*")
SEQ_RE = re.compile(r"\|\s*\*\*模块内序号\*\*\s*\|\s*第\s*\**`?([0-9]+)`?")
SIB_RE = re.compile(r"\|\s*\*\*同号段其它任务\*\*\s*\|\s*(.+?)\s*\|")
TASKID_RE = re.compile(r"\|\s*\*\*本任务编号\*\*\s*\|\s*\**`([^`]+)`\*\*")
SECTION_RE = re.compile(r"^##\s*1\.0\s*编号逻辑", re.M)
MOD11_RE = re.compile(r"\|\s*`(M-[0-9]+)`\s*\|")


def read(path):
    return io.open(path, encoding="utf-8").read()


def main():
    failures = []
    dirs = sorted(d for d in glob.glob(os.path.join(TASKS_ROOT, "*")) if os.path.isdir(d))
    if not dirs:
        print("no task directories found under %s" % TASKS_ROOT)
        return 1

    ids = {}
    module_of = {}
    for d in dirs:
        name = os.path.basename(d)
        plan = os.path.join(d, "PLAN.md")
        readme = os.path.join(d, "README.md")
        if not os.path.isfile(plan):
            failures.append("%s: missing PLAN.md" % name)
            continue
        if not os.path.isfile(readme):
            failures.append("%s: missing README.md" % name)
            continue
        text = read(plan)
        if not SECTION_RE.search(text):
            failures.append("%s: PLAN.md has no '## 1.0 编号逻辑' section" % name)
            continue
        m = TASKID_RE.search(text)
        if not m:
            failures.append("%s: §1.0 missing 本任务编号" % name)
            continue
        tid = m.group(1).strip()
        ids.setdefault(tid, []).append(name)

        scope = SCOPE_RE.search(text)
        if not scope:
            failures.append("%s: §1.0 missing 编号作用域" % name)
        else:
            s = scope.group(1).strip()
            # 允许带限定语（如「跨模块（主改动模块决定号段）」）
            head = s.split("（", 1)[0].split("(", 1)[0].strip()
            if head not in ("模块内", "跨模块"):
                failures.append("%s: 编号作用域 illegal value %r" % (name, s))

        base = BASE_RE.search(text)
        if not base:
            failures.append("%s: §1.0 missing 编号所依据的模块" % name)
        else:
            base_mod = base.group(1).strip()
            module_of[tid] = base_mod
            # 主改动模块必须是 §1.1 的第一行
            i11 = text.find("### 1.1")
            if i11 >= 0:
                rows = MOD11_RE.findall(text[i11:i11 + 1500])
                if rows:
                    if rows[0] != base_mod:
                        failures.append(
                            "%s: 编号所依据的模块 %s != §1.1 主改动模块 %s"
                            % (name, base_mod, rows[0])
                        )
                    if base_mod not in rows:
                        failures.append(
                            "%s: 编号所依据的模块 %s 不在 §1.1 改动模块表中" % (name, base_mod)
                        )

        if not SEQ_RE.search(text):
            failures.append("%s: §1.0 missing 模块内序号" % name)

        if not SIB_RE.search(text):
            failures.append("%s: §1.0 missing 同号段其它任务" % name)

        rtext = read(readme)
        if "编号逻辑" not in rtext:
            failures.append("%s: README.md has no 编号逻辑 line" % name)

    for tid, owners in sorted(ids.items()):
        if len(owners) > 1:
            failures.append("duplicate task id %s in %s" % (tid, ", ".join(owners)))

    # sibling consistency: same module segment -> sibling list must match disk
    for tid, mod in sorted(module_of.items()):
        seg = tid.split("-", 1)[1].split(".", 1)[0]
        actual_set = set(
            t for t in ids
            if t.split("-", 1)[1].split(".", 1)[0] == seg and t != tid
        )
        owners = ids[tid]
        if len(owners) != 1:
            continue
        plan = os.path.join(TASKS_ROOT, owners[0], "PLAN.md")
        text = read(plan)
        m = SIB_RE.search(text)
        if not m:
            continue
        declared = set(re.findall(r"T-[0-9]+\.[0-9]+", m.group(1)))
        if declared != actual_set:
            failures.append(
                "%s: 同号段其它任务 declared=%s but on disk=%s"
                % (owners[0], sorted(declared), sorted(actual_set))
            )

    reg = read(REGISTRY)
    for tid, owners in sorted(ids.items()):
        if tid not in reg:
            failures.append("%s: not registered in TASK_REGISTRY.md" % tid)
    flat = sorted(set(re.findall(r"(?<![.\d])T-[0-9]{2}(?!\d|\.)", reg)))
    if flat:
        failures.append("TASK_REGISTRY.md contains flat task ids: %s" % flat)

    print("task directories checked : %d" % len(dirs))
    print("unique task ids           : %d" % len(ids))
    print("numbering-logic failures  : %d" % len(failures))
    for f in failures:
        print("  FAIL %s" % f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
