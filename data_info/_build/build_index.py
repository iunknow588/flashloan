#!/usr/bin/env python3
"""
通用文档索引构建脚本
===================
动态检测上级目录及所有子目录，自动生成分组标签。
支持任意目录结构，无需硬编码分组规则。

特性:
- 自动扫描脚本所在目录的上级目录作为文档根目录
- 动态发现所有子目录并生成对应分组
- 根目录文件归入 "docs" 分组
- 输出 UTF-8 无 BOM 格式
- 支持 --json 参数仅生成索引，或直接运行启动 HTTP 服务器

用法:
  python build_index.py           # 生成索引并启动 HTTP 服务器
  python build_index.py --json    # 仅生成 _file_index.json
"""

import os
import re
import json
import sys
import socket
import subprocess
import webbrowser
import time
from pathlib import Path


# ──────────────────────────────────────────────
# 1.  路径配置（固定为上级目录）
# ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DISPLAY_ROOT = SCRIPT_DIR.parent  # 固定为脚本所在目录的上一级
OUTPUT_FILE = SCRIPT_DIR / "_file_index.json"

# 固定扫描根目录为上一级目录，不递归检测更上级
SERVE_ROOT = DISPLAY_ROOT

# 排除的文件/目录模式（不区分大小写）
EXCLUDE_PATTERNS = [
    r"^_",           # 以下划线开头的文件/目录
    r"^build_index", # 构建脚本自身
    r"^_file_index", # 索引文件
]


def should_exclude(name: str) -> bool:
    """判断文件/目录名是否应被排除"""
    lower = name.lower()
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, lower):
            return True
    return False


# ──────────────────────────────────────────────
# 2.  元数据提取
# ──────────────────────────────────────────────

def extract_title_from_content(lines):
    """从文件内容找第一个 Markdown 标题"""
    for line in lines:
        m = re.match(r'^(#{1,6})\s+(.+)', line.strip())
        if m:
            return m.group(2).strip()
    return None


def extract_frontmatter(lines):
    """
    解析 markdown 文件头部的 blockquote（> 开头段落）。
    连续空行不中断解析。
    返回 dict: { title, version, date, desc, doc_id }
    """
    result = {
        "title": None,
        "version": None,
        "date": None,
        "desc": None,
        "doc_id": None,
    }

    buf = []
    in_quote = False
    for line in lines[:60]:
        stripped = line.strip()
        if stripped.startswith(">"):
            in_quote = True
            buf.append(stripped.lstrip(">").strip())
        elif in_quote and stripped == "":
            continue  # 空行不中断
        elif in_quote:
            break

    combined = " ".join(buf)

    # 标题
    title_m = re.search(r'#\s*([^\n#]+)', combined)
    if title_m:
        result["title"] = title_m.group(1).strip()

    # 版本
    v_m = re.search(
        r'[*_]{0,2}版本[*_]{0,2}\s*[:：]?\s*(v?[\d.]+(?:\s*\([^)]\))?)',
        combined, re.IGNORECASE
    )
    if v_m:
        result["version"] = v_m.group(1).strip()

    # 日期
    d_m = re.search(r'(\d{4}[-/]\d{2}[-/]\d{2}|\d{4}年\d{1,2}月\d{1,2}日)', combined)
    if d_m:
        result["date"] = d_m.group(1).strip()

    # 文档编号
    id_m = re.search(r'[*_]{0,2}文档编号[*_]{0,2}\s*[:：]?\s*([^\n]+)', combined, re.IGNORECASE)
    if id_m:
        result["doc_id"] = id_m.group(1).strip()

    # 描述
    clean = re.sub(
        r'[*_]{0,2}(版本|文档编号|文档版本|日期|定位|路径)[*_]{0,2}\s*[:：]?\s*\S+',
        '', combined
    )
    clean = re.sub(r'#\s*\S+', '', clean).strip()
    if clean and len(clean) > 5:
        result["desc"] = clean[:120]

    return result


def extract_group(rel_path_str):
    """
    动态分组：根据第一级目录名自动分组。
    根目录文件归入 "docs"，子目录按目录名分组。
    """
    parts = [part for part in rel_path_str.replace("\\", "/").split("/") if part not in ("", ".", "..")]
    
    if not parts:
        return "docs"
    
    # 如果只有一级（文件名），归入 docs
    if len(parts) == 1:
        return "docs"
    
    # 否则按第一级子目录分组
    return parts[0]


def build_group_label(group_id):
    """
    根据分组 ID 生成显示标签。
    优先查找 GROUP_LABELS 映射，否则自动格式化目录名。
    """
    # 内置映射表（可扩展）
    BUILTIN_LABELS = {
        "docs": "概观层",
        "data": "数据层",
        "dataset": "数据层",
        "design": "设计层",
        "tech": "技术层",
        "technical": "技术层",
        "ref": "参考 · 研究 · 归档",
        "reference": "参考 · 研究 · 归档",
        "archive": "参考 · 研究 · 归档",
        "research": "参考 · 研究 · 归档",
        "BNB": "BNB",
        "AVALANCHE": "AVALANCHE",
    }
    
    if group_id == "doc":
        group_id = "docs"

    if group_id in BUILTIN_LABELS:
        return BUILTIN_LABELS[group_id]
    
    # 自动格式化：下划线/连字符转空格，首字母大写
    label = group_id.replace("_", " ").replace("-", " ")
    return label.title()


def build_label(rel_path_str, title, doc_id):
    """
    生成导航栏显示标签。
    优先级: doc_id > title > 文件名
    """
    if doc_id:
        name = doc_id.replace(".md", "")
        m = re.match(r'^(\d+)[_](.+)$', name)
        if m:
            return f"{m.group(1)} · {m.group(2)}"
        return name

    if title:
        t = re.sub(r'^[#\s]+', '', title).strip()
        if t:
            return t

    name = os.path.splitext(os.path.basename(rel_path_str))[0]
    name = re.sub(r'^\d+_', '', name)
    return name


def is_star(rel_path_str):
    """判断是否为重点文档"""
    path_lower = rel_path_str.lower()
    stars = [
        "readme.md",
        "operationalsemantics",
        "项目概述",
    ]
    return any(s in path_lower for s in stars)


# ──────────────────────────────────────────────
# 3.  扫描逻辑
# ──────────────────────────────────────────────

def scan_docs():
    """扫描所有 Markdown 文件"""
    files = []

    # 扫描所有支持的文档类型
    SUPPORTED_EXTS = {".md", ".json"}
    
    # 只扫描 SERVE_ROOT 目录下的一级子目录和根目录文件
    # 不递归扫描更深层级
    scan_targets = [SERVE_ROOT]  # 根目录
    # 添加一级子目录
    for subdir in sorted(SERVE_ROOT.iterdir()):
        if subdir.is_dir() and not should_exclude(subdir.name):
            scan_targets.append(subdir)
    
    for scan_dir in scan_targets:
        for file_path in sorted(scan_dir.iterdir()):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            
            md_path = file_path
            rel_path = os.path.relpath(md_path, DISPLAY_ROOT).replace("\\", "/")
            
            # 排除自身生成的文件
            if md_path.name.startswith("_") or md_path.name.startswith("build_index"):
                continue
            
            # 排除 _build 目录下的文件
            if "_build" in rel_path.split("/"):
                continue

            size = md_path.stat().st_size

            # JSON文件特殊处理
            is_json = md_path.suffix.lower() == ".json"
            
            try:
                raw_lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception as e:
                print(f"  [WARN] 无法读取 {rel_path}: {e}", file=sys.stderr)
                raw_lines = []

            if is_json:
                # JSON文件：尝试提取标题和描述
                title = None
                desc = None
                try:
                    json_data = json.loads('\n'.join(raw_lines))
                    # 尝试从JSON结构中提取信息
                    if isinstance(json_data, dict):
                        title = json_data.get("title") or json_data.get("name")
                        desc = json_data.get("description") or json_data.get("desc")
                        # 如果有report标题字段
                        if not title and "report" in json_data:
                            title = "数据报告"
                except:
                    pass
                
                if not title:
                    title = os.path.splitext(os.path.basename(rel_path))[0]
                if not desc:
                    desc = f"JSON数据文件 ({format_size(size)})"
                
                fm = {"title": title, "version": None, "date": None, "desc": desc, "doc_id": None}
                group = extract_group(rel_path)
                label = title
                star = False
            else:
                fm = extract_frontmatter(raw_lines)
                title = fm["title"] or extract_title_from_content(raw_lines)
                group = extract_group(rel_path)
                label = build_label(rel_path, title, fm["doc_id"])
                star = is_star(rel_path)
                desc = fm["desc"] or (title if title else "")

            file_entry = {
                "path": rel_path,
                "group": group,
                "label": label,
                "title": title or label,
                "size": size,
                "size_str": format_size(size),
                "version": fm["version"],
                "date": fm["date"],
                "star": star,
                "desc": desc,
            }

            files.append(file_entry)
            print(f"  ✓ {rel_path}")

    return files


def format_size(n):
    if n < 1024:
        return f"{n}B"
    elif n < 10240:
        return f"{n/1024:.1f}KB"
    else:
        return f"{round(n/1024)}KB"


# ──────────────────────────────────────────────
# 4.  生成索引
# ──────────────────────────────────────────────

def generate_json_only():
    """仅生成 _file_index.json"""
    import datetime
    print(f"\n📚 扫描文档目录: {SERVE_ROOT}\n")

    files = scan_docs()

    # 动态收集所有分组
    all_groups = set(f["group"] for f in files)
    
    # 分组排序: docs 优先，其余按字母序
    def group_sort_key(g):
        if g == "docs":
            return (0, g)
        return (1, g)
    
    sorted_groups = sorted(all_groups, key=group_sort_key)
    
    # 构建 groups 映射
    groups = {}
    for g in sorted_groups:
        groups[g] = build_group_label(g)
    
    # 文件排序: 按分组优先级 + 路径
    GROUP_ORDER = {g: i for i, g in enumerate(sorted_groups)}
    files.sort(key=lambda f: (GROUP_ORDER.get(f["group"], 99), f["path"]))

    output = {
        "generated": str(Path(__file__).resolve()),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total_files": len(files),
        "groups": groups,
        "files": files,
    }

    # UTF-8 无 BOM 写入
    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"\n✅ 已生成: {OUTPUT_FILE.name}  ({len(files)} 个文档)")
    print(f"\n📊 分组统计:")
    for grp, label in groups.items():
        count = sum(1 for f in files if f["group"] == grp)
        if count > 0:
            print(f"   {label}: {count} 个文件")


# ──────────────────────────────────────────────
# 5.  HTTP 服务器
# ──────────────────────────────────────────────

def find_free_port(start=8080, limit=20):
    for port in range(start, start + limit):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("", port))
            sock.close()
            return port
        except OSError:
            continue
    raise RuntimeError(f"找不到空闲端口 ({start}-{start + limit - 1})")


def wait_until_listening(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect(("localhost", port))
            sock.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def start_server():
    """启动 HTTP 服务器并打开浏览器"""
    port = find_free_port()
    index_rel = SCRIPT_DIR.relative_to(SERVE_ROOT).as_posix() + "/index.html"
    print(f"\n🌐 启动文档服务器: http://localhost:{port}/{index_rel}")
    print("   按 Ctrl+C 停止服务器\n")

    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port)],
        cwd=str(SERVE_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not wait_until_listening(port):
        print(f"⚠ server 启动超时，端口 {port} 可能未就绪")
        return

    webbrowser.open(f"http://localhost:{port}/{index_rel}?v={int(time.time())}")
    proc.wait()


# ──────────────────────────────────────────────
# 6.  主入口
# ──────────────────────────────────────────────

def main():
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        generate_json_only()
        return

    generate_json_only()
    start_server()


if __name__ == "__main__":
    main()
