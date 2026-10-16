#!/usr/bin/env python3
"""静态自查 qqdeploy.py：未导入的模块、危险的 API 用法、明显笔误。"""
import ast
import sys

PATH = r"D:\QQ Agent\tools\qqdeploy.py"
src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)

# 1) 导入 vs 使用（只查 X.Y 形式的顶层名）
imported = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Import):
        for a in n.names:
            imported.add((a.asname or a.name).split(".")[0])
    elif isinstance(n, ast.ImportFrom):
        for a in n.names:
            if a.name != "*":
                imported.add(a.asname or a.name)

used = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Attribute):
        cur = n
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            used.add(cur.id)

missing = sorted(x for x in used if x not in imported and x not in dir(__builtins__))
print("导入的模块:", ", ".join(sorted(imported)))
print("X.Y 用到的顶层名:", ", ".join(sorted(used)))
print("疑似未导入:", ", ".join(missing) if missing else "无")

# 2) 检查 tarfile.extract 的 filter 参数（3.12+ 才有；3.10/3.11 会 TypeError）
print()
if "filter=" in src and "tf.extract(" in src:
    print("⚠️ 用了 tarfile.extract(filter=...)：Python < 3.12 不支持，需兼容处理")
else:
    print("tarfile filter 用法: 未使用")

# 3) 检查死代码 / 未使用的赋值
print()
fn_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
print("函数数量:", len(fn_names))

# 4) 找 save_state 里的可疑赋值（switch_current 已经切过软链，save_state 里又试一次）
if "os.symlink(rel_dir, CURRENT + \".new\")" in src:
    print("⚠️ save_state 里仍在写 current 软链（与 switch_current 重复，FileExistsError 被吞）")
else:
    print("save_state 中的软链写入: 已清理")

# 5) 检查引号是否闭合（粗略：统计三引号数量）
print()
print("三引号出现次数:", src.count('"""'), "(应为偶数)")
