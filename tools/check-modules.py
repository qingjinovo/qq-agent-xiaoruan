#!/usr/bin/env python3
"""
check-modules.py —— 系统性校验 src/ 的模块依赖图

逐个 import 检查：目标文件是否存在、被导入的具名符号是否真的被导出。
避免"改一个崩一个"的反复试错。

用法: python3 check-modules.py [src目录]
"""

import os
import re
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/opt/qq/qq-agent/src"


def strip_comments(text):
    # 去掉行注释和块注释，避免注释里的 import 被误判
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return text


def exports_of(path):
    """收集一个模块导出的具名符号"""
    try:
        t = strip_comments(open(path, encoding="utf-8").read())
    except Exception:
        return set()
    names = set()
    names |= set(re.findall(r"export\s+(?:async\s+)?function\s+(\w+)", t))
    names |= set(re.findall(r"export\s+(?:const|let|var|class)\s+(\w+)", t))
    # export { a, b as c }  /  export { a as b } from './x'
    for grp in re.findall(r"export\s*\{([^}]*)\}", t):
        for item in grp.split(","):
            item = item.strip()
            if not item:
                continue
            name = item.split(" as ")[-1].strip() if " as " in item else item
            if re.fullmatch(r"\w+", name):
                names.add(name)
    if re.search(r"export\s+default", t):
        names.add("default")
    return names


def imports_of(path):
    """返回 [(目标相对路径, [具名符号...], 是否为命名空间导入)]"""
    t = strip_comments(open(path, encoding="utf-8").read())
    out = []
    for m in re.finditer(r"import\s+([^;]+?)\s+from\s+'(\.[^']+)'", t, re.S):
        clause, rel = m.group(1), m.group(2)
        clause = " ".join(clause.split())
        if clause.startswith("{"):
            names = [x.strip().split(" as ")[0].strip()
                     for x in clause.strip("{} ").split(",") if x.strip()]
            out.append((rel, names, False))
        elif "*" in clause:
            out.append((rel, [], True))
        else:
            out.append((rel, ["default"], False))
    return out


def main():
    if not os.path.isdir(SRC):
        print("目录不存在: %s" % SRC)
        return 1

    files = sorted(f for f in os.listdir(SRC) if f.endswith(".js"))
    print("检查目录: %s  (%d 个 .js 文件)" % (SRC, len(files)))
    print()

    missing_files = []
    missing_names = []

    for f in files:
        p = os.path.join(SRC, f)
        t = strip_comments(open(p, encoding="utf-8").read())
        is_module = ("import " in t) or ("export " in t)
        for rel, names, ns in imports_of(p):
            target = os.path.normpath(os.path.join(SRC, rel))
            if not os.path.exists(target):
                missing_files.append((f, rel))
                continue
            if ns:
                continue
            exp = exports_of(target)
            for n in names:
                if n not in exp:
                    missing_names.append((f, rel, n))

    print("─" * 62)
    print("[A] 指向不存在的文件")
    if missing_files:
        for f, rel in missing_files:
            print("   ❌ %-20s -> %s" % (f, rel))
    else:
        print("   ✅ 无")

    print()
    print("[B] 导入了目标未导出的符号")
    if missing_names:
        seen = set()
        for f, rel, n in missing_names:
            key = (f, rel, n)
            if key in seen:
                continue
            seen.add(key)
            print("   ❌ %-20s 从 %-18s 导入 %s" % (f, rel, n))
    else:
        print("   ✅ 无")

    print()
    print("─" * 62)
    bad = len(missing_files) + len(missing_names)
    if bad:
        print("结论: ❌ 发现 %d 处不兼容，容器会启动失败" % bad)
        return 1
    print("结论: ✅ 模块依赖图完整，可以重建")
    return 0


if __name__ == "__main__":
    sys.exit(main())
