# -*- coding: utf-8 -*-
"""一次性迁移脚本：把二级任务编号从 `T-n.nn` / `AC-n.nn` 迁移到「模块索引 + 组合索引」编号。

用法：
    python plan/runtime_evidence/tools/migrate_task_ids_to_index_20261124.py --dry-run
    python plan/runtime_evidence/tools/migrate_task_ids_to_index_20261124.py --apply

幂等：已迁移过的文件不含任何旧编号键，再次运行不会产生改动。
"""
import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAN = os.path.join(ROOT, "plan")
TASKS_DIR = os.path.join(PLAN, "tasks", "P-01-池子重构", "tasks")
REGISTRY = os.path.join(PLAN, "TASK_REGISTRY.md")

MOD_RE = re.compile(r"\|\s*`(M-\d+)`\s*\|([^|]*)\|\s*`([^`]+)`\s*\|")
SECTION_1_1 = "### 1.1"
SECTION_1_2 = "### 1.2"
MOD_ROW_RE = re.compile(r"\|\s*`(M-\d+)`\s*\|")


def mod_num(mod_id):
    return int(mod_id.split("-")[1])


def read_text(path):
    """读文件，返回 (文本, has_bom, newline)。newline 为 '\\r\\n' 或 '\\n'。"""
    raw = open(path, "rb").read()
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, has_bom, newline


def write_text(path, text, has_bom, newline):
    """按原 BOM 与行尾写回，避免污染受控基线。"""
    if newline == "\r\n":
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8")
    if has_bom:
        data = b"\xef\xbb\xbf" + data
    with io.open(path, "wb") as handle:
        handle.write(data)


def load_module_index():
    text, _, _ = read_text(REGISTRY)
    modules = {}
    for line in text.split("\n"):
        match = MOD_RE.match(line)
        if match:
            modules[match.group(1)] = (match.group(2).strip(), match.group(3).strip())
    if not modules:
        raise SystemExit("模块索引表为空：请检查 %s §1.1" % REGISTRY)
    return modules


def load_task_module_sets():
    """从每个任务自己的 PLAN.md §1.1 改动模块表读取真实模块集合。"""
    sets = {}
    for name in sorted(os.listdir(TASKS_DIR)):
        task_dir = os.path.join(TASKS_DIR, name)
        plan_path = os.path.join(task_dir, "PLAN.md")
        if not os.path.isdir(task_dir) or not os.path.exists(plan_path):
            continue
        text, _, _ = read_text(plan_path)
        if SECTION_1_1 not in text:
            raise SystemExit("%s 缺少 §1.1 改动模块节" % name)
        section = text.split(SECTION_1_1, 1)[1]
        if SECTION_1_2 in section:
            section = section.split(SECTION_1_2, 1)[0]
        mods = []
        for line in section.split("\n"):
            match = MOD_ROW_RE.match(line)
            if match and match.group(1) not in mods:
                mods.append(match.group(1))
        sets[name] = sorted(mods, key=mod_num)
    return sets


def build_mapping(modules, mod_sets):
    """组合索引 = 涉及模块的索引按模块号升序拼接；组内序号按旧编号升序分配。"""
    groups = {}
    for name, mods in mod_sets.items():
        index = "".join(modules[m][1] for m in mods)
        groups.setdefault(index, []).append(name)

    mapping = {}
    for index, names in groups.items():
        def sort_key(task_id):
            major, minor = task_id.split("-", 1)[1].split(".")
            return (int(major), int(minor))
        for seq, name in enumerate(sorted(names, key=sort_key), 1):
            mapping[name] = {
                "index": index,
                "seq": "%02d" % seq,
                "new": "T-%s.%s" % (index, "%02d" % seq),
                "acc": "AC-%s.%s" % (index, "%02d" % seq),
                "mods": mod_sets[name],
            }
    values = [item["new"] for item in mapping.values()]
    if len(values) != len(set(values)):
        raise SystemExit("编号碰撞：新编号不唯一")
    return mapping


def iter_live_docs():
    """受控的 live 文档：plan/ 下，排除 archive（冻结）与 back（临时通道）。"""
    for root, dirs, files in os.walk(PLAN):
        dirs[:] = [d for d in dirs if d not in ("archive", "back", "__pycache__", "node_modules")]
        for name in sorted(files):
            if name.endswith(".md"):
                yield os.path.join(root, name)


def rewrite(text, mapping):
    """按最长键优先替换，避免 T-1.0 误匹配 T-10.01。"""
    pairs = []
    for old, item in mapping.items():
        pairs.append((old, item["new"]))
        pairs.append(("AC-" + old.split("-", 1)[1], item["acc"]))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    for old, new in pairs:
        text = re.sub(re.escape(old) + r"(?![0-9A-Za-z一-鿿])", new, text)
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正落盘；默认只预演")
    parser.add_argument("--report", default="", help="把映射表写到指定 JSON")
    args = parser.parse_args()

    modules = load_module_index()
    mod_sets = load_task_module_sets()
    mapping = build_mapping(modules, mod_sets)

    print("模块索引：%d 个" % len(modules))
    print("二级任务：%d 个" % len(mapping))
    print()
    for name in sorted(mapping, key=lambda n: (mod_num(mapping[n]["mods"][0]), n)):
        item = mapping[name]
        print("  %-9s -> %-12s mods=%s" % (name, item["new"], ",".join(item["mods"])))
    print()

    changed_docs = []
    for path in iter_live_docs():
        text, has_bom, newline = read_text(path)
        new_text = rewrite(text, mapping)
        if new_text != text:
            changed_docs.append((path, has_bom, newline, new_text))

    renames = []
    for name, item in mapping.items():
        old_dir = os.path.join(TASKS_DIR, name)
        new_dir = os.path.join(TASKS_DIR, item["new"])
        if os.path.isdir(old_dir):
            renames.append((old_dir, new_dir))
        elif not os.path.isdir(new_dir):
            raise SystemExit("任务目录缺失：%s" % old_dir)

    print("需改写文档：%d 个" % len(changed_docs))
    print("需重命名目录：%d 个" % len(renames))
    for old_dir, new_dir in renames:
        print("  %s -> %s" % (os.path.basename(old_dir), os.path.basename(new_dir)))

    if args.report:
        with io.open(args.report, "w", encoding="utf-8") as handle:
            json.dump(mapping, handle, ensure_ascii=False, indent=1, sort_keys=True)

    if not args.apply:
        print("\n（预演模式，未落盘）加 --apply 执行")
        return 0

    for path, has_bom, newline, new_text in changed_docs:
        write_text(path, new_text, has_bom, newline)
    print("\n已改写 %d 个文档" % len(changed_docs))

    for old_dir, new_dir in renames:
        if os.path.isdir(old_dir) and not os.path.exists(new_dir):
            os.rename(old_dir, new_dir)
    print("已重命名 %d 个目录" % len(renames))
    return 0


if __name__ == "__main__":
    sys.exit(main())
