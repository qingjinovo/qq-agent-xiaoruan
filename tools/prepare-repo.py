#!/usr/bin/env python3
"""
prepare-repo.py —— 准备要发布到 GitHub 的仓库内容

原则：
  · 只收源码与文档，不收二进制（Electron 运行时、snowluma 的 Node/DLL、node_modules）
  · **绝不收** data/ 下的任何东西（config.json 含明文 API Key、还有聊天存档/记忆/会话记录）
  · 保留上游 MIT LICENSE 与版权声明（这是硬性要求）
  · app/src/app.js 打上"控制台绑定地址可配置"补丁，否则容器里端口映射不通

用法: python tools/prepare-repo.py [输出目录]
"""

import io
import os
import re
import shutil
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(WS, "resources", "app")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(WS, "repo")

OLD_LISTEN = "server.listen(port, '127.0.0.1', () => {"
NEW_LISTEN = "server.listen(port, process.env.QQ_AGENT_BIND_HOST || '127.0.0.1', () => {"


def read(p):
    with io.open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def write(p, text):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def copy_tree(src, dst, skip=()):
    """复制目录，跳过指定名字的条目"""
    os.makedirs(dst, exist_ok=True)
    n = 0
    for name in os.listdir(src):
        if name in skip:
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            n += copy_tree(s, d, skip)
        else:
            shutil.copy2(s, d)
            n += 1
    return n


def main():
    if not os.path.isdir(APP):
        print("找不到应用目录: %s" % APP)
        return 1

    print("=== 0. 安全检查：确认不碰 data 与二进制 ===")
    data_dir = os.path.join(WS, "data")
    if os.path.isdir(data_dir):
        print("   ✅ 已确认 data/ 不在收集范围内（含 API Key 与聊天记录）")

    if os.path.isdir(OUT):
        # ⚠️ 不能整目录删 —— repo/ 是 git 仓库，rmtree 会连 .git 一起删掉，
        #    而且递归强删在 Windows 上常因只读属性失败。
        #    这里逐个删"顶层条目"，显式跳过 .git。
        print("   清理旧内容（保留 .git）: %s" % OUT)
        for name in os.listdir(OUT):
            if name == ".git":
                continue
            p = os.path.join(OUT, name)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
    os.makedirs(OUT, exist_ok=True)

    # ── 1. 应用源码（跳过 snowluma / node_modules / electron） ──
    print("\n=== 1. 收集应用源码 ===")
    app_out = os.path.join(OUT, "app")
    os.makedirs(app_out, exist_ok=True)
    for item in ["src", "ui", "assets"]:
        s = os.path.join(APP, item)
        if os.path.isdir(s):
            n = copy_tree(s, os.path.join(app_out, item))
            print("   %-8s %d 个文件" % (item + "/", n))
    for f in ["package.json", "README.md", "LICENSE", "启动QQ机器人.bat"]:
        s = os.path.join(APP, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(app_out, f))
            print("   %-14s %d 字节" % (f, os.path.getsize(s)))

    # ── 2. app.js 绑定地址补丁（容器部署必需） ──
    print("\n=== 2. app/src/app.js 绑定地址补丁 ===")
    appjs = os.path.join(app_out, "src", "app.js")
    t = read(appjs)
    if "QQ_AGENT_BIND_HOST" in t:
        print("   ⚠️ 已含补丁")
    elif t.count(OLD_LISTEN) == 1:
        write(appjs, t.replace(OLD_LISTEN, NEW_LISTEN, 1))
        print("   ✅ 已应用（容器里监听 0.0.0.0，宿主机端口映射才通）")
    else:
        print("   ❌ 锚点不唯一（出现 %d 次），中止" % t.count(OLD_LISTEN))
        return 2

    # ── 3. 我自己的工具与文档 ──
    print("\n=== 3. 收集自研工具与文档 ===")
    tools_out = os.path.join(OUT, "tools")
    n = copy_tree(os.path.join(WS, "tools"), tools_out, skip=("__pycache__",))
    print("   tools/  %d 个文件（已跳过 __pycache__）" % n)

    # 交付物源码也要进仓库（prepare-repo 自己刚才还没被复制进去，补一次）
    me = os.path.abspath(__file__)
    dst_me = os.path.join(tools_out, os.path.basename(me))
    if not os.path.exists(dst_me):
        shutil.copy2(me, dst_me)
        print("   补入 %s" % os.path.basename(me))

    for f in ["persona-deepseek-xiaojingyu.md", "persona-xiaoruan-software-association.md"]:
        s = os.path.join(WS, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(OUT, f))
            print("   %s  %d 字节" % (f, os.path.getsize(s)))

    # ── 4. 部署配置（compose + Dockerfile） ──
    print("\n=== 4. 生成部署配置 ===")
    deploy = os.path.join(OUT, "deploy")
    os.makedirs(deploy, exist_ok=True)

    # Dockerfile：从 app 目录构建
    dockerfile = """# QQ Agent 无头容器镜像
#
# 构建上下文 = 仓库根目录（因为要 COPY app/ 下的源码）
#   docker build -f deploy/Dockerfile -t qq-agent:local .
#
# 说明：src/app.js 在入仓前已打补丁读 QQ_AGENT_BIND_HOST，
#      容器里必须监听 0.0.0.0 才能让宿主机的端口映射生效。
FROM node:22-slim

WORKDIR /app

# 依赖层单独缓存（本项目生产依赖只有三个纯 JS 包，无原生编译）
COPY app/package.json ./
RUN npm install --omit=dev --no-audit --no-fund

# 应用本体
COPY app/src/ ./src/
COPY app/ui/ ./ui/
COPY app/assets/ ./assets/
COPY app/README.md ./

ENV QQ_AGENT_DATA_DIR=/app/data
ENV QQ_AGENT_BIND_HOST=0.0.0.0
RUN mkdir -p /app/data

EXPOSE 3210

# 无头入口（不带 Electron 窗口，控制台走浏览器）
CMD ["node", "src/server.js"]
"""
    write(os.path.join(deploy, "Dockerfile"), dockerfile)
    print("   deploy/Dockerfile")

    compose = """# QQ Agent + NapCat 容器编排
#
#   启动：docker compose -f deploy/docker-compose.yml up -d
#   日志：docker compose -f deploy/docker-compose.yml logs -f
#   停止：docker compose -f deploy/docker-compose.yml down
#
# ⚠️ 所有端口只绑 127.0.0.1：控制台与 WebUI 一律走 SSH 隧道访问，不暴露公网。
# ⚠️ 两个数据/插件目录必须保留挂载，否则重建容器会丢配置与插件：
#      data/    配置、聊天存档、记忆、会话记录、用量、插件数据
#      plugins/ 市场口令安装的插件本体（放在容器可写层里会在重建时丢失）
services:

  # ── 协议端：NapCat（官方镜像，负责登 QQ 并提供 OneBot v11）──
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: always
    environment:
      - NAPCAT_UID=${NAPCAT_UID:-1000}
      - NAPCAT_GID=${NAPCAT_GID:-1000}
      - WEBUI_TOKEN=${WEBUI_TOKEN:-change-me}
      - ACCOUNT=${QQ_ACCOUNT:-}
    ports:
      - 127.0.0.1:3000:3000     # OneBot HTTP API
      - 127.0.0.1:3001:3001     # OneBot 正向 WebSocket（本项目主要用这个）
      - 127.0.0.1:6099:6099     # NapCat WebUI（扫码登录、网络配置）
    volumes:
      - ./napcat/config:/app/napcat/config
      - ./napcat/QQ:/app/.config/QQ     # 登录态；不挂会每次重启都要重扫
    shm_size: 256m                      # QQ 客户端是 Chromium 内核，64M 会崩
    networks: [qqnet]

  # ── 大脑：QQ Agent ──
  qq-agent:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    image: qq-agent:local
    container_name: qq-agent
    restart: always
    depends_on: [napcat]
    environment:
      - TZ=Asia/Shanghai
      - NODE_ENV=production
      - QQ_AGENT_DATA_DIR=/app/data
    ports:
      - 127.0.0.1:3210:3210     # 会话式控制台
    volumes:
      - ./data:/app/data
      - ./plugins:/app/plugins
    networks: [qqnet]

networks:
  qqnet:
    driver: bridge
"""
    write(os.path.join(deploy, "docker-compose.yml"), compose)
    print("   deploy/docker-compose.yml")

    # ── 4b. 运维脚本（已脱敏：凭据从 .env 读，不写死在文件里） ──
    print("\n=== 4b. 收集运维脚本 ===")
    ops_src = os.path.join(WS, "ops-sanitized", "ops-sanitized")
    if os.path.isdir(ops_src):
        ops_out = os.path.join(deploy, "ops")
        sysd_out = os.path.join(deploy, "systemd")
        os.makedirs(ops_out, exist_ok=True)
        os.makedirs(sysd_out, exist_ok=True)
        for f in ["napcat-guard.sh", "qr-server.py"]:
            s = os.path.join(ops_src, f)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(ops_out, f))
                print("   deploy/ops/%s" % f)
        for f in ["napcat-guard.service", "napcat-guard.timer", "qr-server.service"]:
            s = os.path.join(ops_src, "systemd", f)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(sysd_out, f))
                print("   deploy/systemd/%s" % f)
    else:
        print("   ⚠️ 找不到 %s，跳过运维脚本" % ops_src)
        print("      （先在服务器上跑 tools/ops-sanitize.py 生成脱敏版）")

    # ── 5. .gitignore ──
    print("\n=== 5. 写 .gitignore ===")
    gitignore = """# ── 运行时数据：绝不能入库 ──
# data/ 含明文 API Key（config.json）、聊天存档、群友记忆、会话记录
data/
napcat/
plugins/
*.lock

# ── 二进制与依赖 ──
node_modules/
app/snowluma/
*.exe
*.dll
*.node
*.pak
*.bin
*.dat
*.tar.gz
locales/

# ── 个人信息 ──
.env
*.pem
*.key
config.json

# ── 编辑器/系统 ──
.vscode/
.idea/
.DS_Store
Thumbs.db
__pycache__/
"""
    write(os.path.join(OUT, ".gitignore"), gitignore)
    print("   .gitignore（已排除 data/、二进制、密钥）")

    # ── 5b. .gitattributes：强制 LF ──
    # 本仓库的脚本要传进 Linux 容器执行，若被 Windows 的 core.autocrlf 改成 CRLF，
    # 远端会报 $'\r': command not found。
    gitattributes = """# 一律用 LF 入库（脚本要传进 Linux 容器执行）
* text=auto eol=lf

# Windows 批处理必须是 CRLF（cmd.exe 对 LF 结尾的标签跳转有已知问题）
*.bat text eol=crlf
*.cmd text eol=crlf

# 二进制：避免 Git 做换行转换与文本 diff
*.png binary
*.jpg binary
*.jpeg binary
*.gif binary
*.webp binary
*.ico binary
*.woff binary
*.woff2 binary
*.ttf binary
*.otf binary
*.zip binary
*.gz binary
"""
    write(os.path.join(OUT, ".gitattributes"), gitattributes)
    print("   .gitattributes（强制 LF，.bat 用 CRLF）")

    # ── 5c. README ──
    # 与源码一起生成，避免"重新生成内容时把文档删掉"这类事故。
    here = os.path.dirname(os.path.abspath(__file__))
    tmpl = os.path.join(here, "repo-readme.md")
    if os.path.exists(tmpl):
        shutil.copy2(tmpl, os.path.join(OUT, "README.md"))
        print("   README.md（来自 tools/repo-readme.md，%d 字节）" % os.path.getsize(tmpl))
    else:
        print("   ⚠️ 找不到 tools/repo-readme.md，README 未生成")

    # ── 6. 汇总 ──
    print("\n=== 6. 汇总 ===")
    total = 0
    for root, dirs, files in os.walk(OUT):
        # 统计交付内容时不算 .git 内部文件（否则会把几万个对象文件算进来）
        dirs[:] = [d for d in dirs if d != ".git"]
        total += len(files)
    print("   输出目录: %s" % OUT)
    print("   交付文件数: %d（不含 .git）" % total)

    # 二次确认：目录里不能出现 data/、API Key、真实服务器地址
    print("\n   === 安全检查 ===")
    bad = []
    # 注意：不要把真实 IP 写在这里当检测模式 —— 这个脚本本身也会入仓。
    # 只按"私网段以外的公网 IPv4 + 明显密钥前缀"做启发式检查。
    # 只按"私网段/特殊地址以外的公网 IPv4 + 明显密钥前缀"做启发式检查。
    # 排除：0.0.0.0（绑定地址）、127.、10.、192.168.、172.16-31.
    ip_re = re.compile(
        r"\b(?!0\.0\.0\.0)(?!127\.)(?!10\.)(?!192\.168\.)(?!172\.(?:1[6-9]|2\d|3[01])\.)"
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
    )
    self_path = os.path.abspath(__file__)
    for root, dirs, files in os.walk(OUT):
        for f in files:
            p = os.path.join(root, f)
            if f == "config.json":
                bad.append(p + " (config.json 不应入仓)")
            if os.path.getsize(p) < 2_000_000:
                try:
                    c = read(p)
                except Exception:
                    continue
                if re.search(r"sk-[a-zA-Z0-9]{20,}", c):
                    bad.append(p + " (含 sk- 密钥)")
                # 检测脚本自己会包含 IP 正则的匹配样例，跳过自身
                if os.path.abspath(p) != self_path:
                    m = ip_re.search(c)
                    if m:
                        bad.append(p + " (疑似公网 IP: %s)" % m.group(0))
    if bad:
        print("   ❌ 发现问题文件:")
        for b in bad:
            print("      " + b.replace(OUT, "."))
        return 3
    print("   ✅ 无 API Key、无公网 IP、无 data/ 内容")
    return 0


if __name__ == "__main__":
    sys.exit(main())
