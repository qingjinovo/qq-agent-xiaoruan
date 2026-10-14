#!/usr/bin/env python3
"""
prepare-upgrade.py —— 准备升级到新版应用的候选源码

做四件事（都在本机暂存目录，不动工作区原文件）：
  1. 从工作区新版 app 复制 src / ui / package.json 到 stage-upgrade/
  2. 给 src/app.js 打"绑定地址可配置"补丁（容器里必须监听 0.0.0.0，否则端口映射不通）
  3. 自检补丁是否生效、模块依赖图是否完整
  4. 输出待上传清单与统计

用法: python tools/prepare-upgrade.py
"""

import io
import os
import re
import shutil
import sys

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(WORKSPACE, "resources", "app")
STAGE = os.path.join(WORKSPACE, "stage-upgrade")

OLD_LISTEN = "server.listen(port, '127.0.0.1', () => {"
NEW_LISTEN = "server.listen(port, process.env.QQ_AGENT_BIND_HOST || '127.0.0.1', () => {"


def read(p):
    with io.open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def write(p, text):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main():
    if not os.path.isdir(APP):
        print("找不到工作区 app: %s" % APP)
        return 1

    # ── 1. 暂存目录 ──
    if os.path.isdir(STAGE):
        print("暂存目录已存在，重建: %s" % STAGE)
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)

    print("=== 1. 复制新版源码 ===")
    for item in ["src", "ui"]:
        src = os.path.join(APP, item)
        dst = os.path.join(STAGE, item)
        if not os.path.isdir(src):
            print("   ❌ 缺少 %s" % src)
            return 1
        shutil.copytree(src, dst)
        n = sum(len(f) for _, _, f in os.walk(dst))
        print("   %-6s -> stage/%s  (%d 个文件)" % (item, item, n))

    for f in ["package.json", "README.md"]:
        s = os.path.join(APP, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(STAGE, f))
            print("   %-12s -> stage/%s" % (f, f))

    assets = os.path.join(APP, "assets")
    if os.path.isdir(assets):
        shutil.copytree(assets, os.path.join(STAGE, "assets"))
        print("   assets       -> stage/assets")

    # ── 2. 打绑定地址补丁 ──
    print("\n=== 2. 给 app.js 打绑定地址补丁 ===")
    appjs = os.path.join(STAGE, "src", "app.js")
    t = read(appjs)
    cnt = t.count(OLD_LISTEN)
    print("   原始行出现次数: %d" % cnt)
    if "QQ_AGENT_BIND_HOST" in t:
        print("   ⚠️ 已含补丁，跳过")
    elif cnt == 1:
        t = t.replace(OLD_LISTEN, NEW_LISTEN, 1)
        write(appjs, t)
        print("   ✅ 补丁已应用")
    else:
        print("   ❌ 出现次数不是 1，结构不符，中止（避免改错）")
        return 2

    # ── 3. 自检 ──
    print("\n=== 3. 自检 ===")
    t = read(appjs)
    print("   app.js 含 QQ_AGENT_BIND_HOST : %s" % ("QQ_AGENT_BIND_HOST" in t))
    print("   app.js 大小                  : %d 字节" % os.path.getsize(appjs))

    # 模块依赖图（复用 check-modules 的逻辑）
    SRC = os.path.join(STAGE, "src")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cm", os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-modules.py"))
        cm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cm)
    except Exception as exc:
        print("   （无法加载 check-modules.py: %s，跳过依赖检查）" % exc)
        cm = None

    if cm:
        files = sorted(f for f in os.listdir(SRC) if f.endswith(".js"))
        missing_files, missing_names = [], []
        for f in files:
            p = os.path.join(SRC, f)
            t2 = cm.strip_comments(open(p, encoding="utf-8").read())
            for rel, names, ns in cm.imports_of(p):
                target = os.path.normpath(os.path.join(SRC, rel))
                if not os.path.exists(target):
                    missing_files.append((f, rel))
                    continue
                if ns:
                    continue
                exp = cm.exports_of(target)
                for n in names:
                    if n not in exp:
                        missing_names.append((f, rel, n))
        print("   src 文件数                   : %d" % len(files))
        print("   指向不存在的文件             : %d" % len(missing_files))
        for f, rel in missing_files[:8]:
            print("      ❌ %s -> %s" % (f, rel))
        print("   导入了未导出的符号           : %d" % len(missing_names))
        for f, rel, n in missing_names[:8]:
            print("      ❌ %s 从 %s 导入 %s" % (f, rel, n))
        if missing_files or missing_names:
            print("\n   ❌ 依赖图不完整，先修再升级")
            return 3

    # ── 4. 摘要 ──
    print("\n=== 4. 待上传内容 ===")
    total = 0
    for root, _dirs, fs in os.walk(STAGE):
        for f in fs:
            total += 1
    print("   暂存目录: %s" % STAGE)
    print("   文件总数: %d" % total)
    pkg = os.path.join(STAGE, "package.json")
    if os.path.exists(pkg):
        import json
        d = json.load(open(pkg, encoding="utf-8"))
        print("   版本    : %s" % d.get("version"))
        print("   依赖    : %s" % list((d.get("dependencies") or {}).keys()))
    print("\n   ✅ 候选代码已就绪，可以上传")
    return 0


if __name__ == "__main__":
    sys.exit(main())
