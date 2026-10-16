#!/usr/bin/env python3
"""
qqdeploy —— 把 GitHub 仓库的某个分支/提交部署到本机（QQ Agent 服务器）

设计要点（都是踩过坑才这么写的）：

1. **不走 git 协议**。本机 git clone over HTTPS 被墙，但 api.github.com 可用，
   所以直接用 API 下载 tarball。按分支名或 commit sha 都能取。
   代价是没有 git 历史 —— 因此提交 sha 会写进版本记录，回滚靠 releases/ 目录。

2. **每次部署独立目录** /opt/qq/releases/<sha>/，current 是指向它的软链。
   回滚 = 把软链切回上一个 release + 重建，不需要重新下载。

3. **先验证再切换**：语法检查 + 模块依赖检查不过就中止，线上容器一个字节都不动。

4. **失败自动回滚**：重建后健康检查不过，自动切回 deploy 前的 release 并再重建。

5. **防并发**：flock 独占锁。build --no-cache 要 3~4 分钟，两个人同时部署会互相覆盖，
   而服务器只有 1.6G 内存，也跑不了两个实例。

6. **不碰 data**：只更新代码；/opt/qq/qq-agent-data 与 /opt/qq/qq-agent-plugins 始终是
   独立 bind mount，重建容器不影响。

用法（需要 sudo，已配 NOPASSWD 免密）：
    sudo qqdeploy main                 # 部署 main 分支
    sudo qqdeploy feat/xxx             # 部署某个分支
    sudo qqdeploy a334bdc              # 部署指定 commit
    sudo qqdeploy --status             # 看当前部署了什么
    sudo qqdeploy --list               # 列出保留的版本
    sudo qqdeploy --rollback           # 回滚到上一个部署过的版本
    sudo qqdeploy --rollback <sha>     # 回滚到指定版本（需仍在 releases/ 里）
"""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

# ── 配置 ──────────────────────────────────────────────────────────────────────
REPO = "viuuro/qq-agent-xiaoruan"
ROOT = "/opt/qq"
RELEASES = os.path.join(ROOT, "releases")
CURRENT = os.path.join(ROOT, "current")          # 软链 -> releases/<sha>
STATE = os.path.join(ROOT, ".deployed.json")     # 当前部署记录
LOCK = os.path.join(ROOT, ".deploy.lock")
ENV_FILE = os.path.join(ROOT, ".env")            # NAPCAT_UID/GID/WEBUI_TOKEN/QQ_ACCOUNT
KEEP_RELEASES = 8                                # 保留多少个历史版本
AGENT_CONTAINER = "qq-agent"
HEALTH_TIMEOUT = 180                             # 重建后等待健康的秒数

# ★ compose 项目名必须与既有部署一致，否则 docker compose 会把容器当成"新的"，
#   于是 attempt 创建第二个同名容器 → 报
#     Conflict. The container name "/qq-agent" is already in use
#   而不会去替换它。线上原本由 /opt/qq/docker-compose.yml 管理，项目名是 qq
#   （工作目录名），这里显式固定，避免随 release 目录名漂移。
COMPOSE_PROJECT = os.environ.get("QQ_COMPOSE_PROJECT", "qq")

API = "https://api.github.com/repos/%s" % REPO


# ── 小工具 ────────────────────────────────────────────────────────────────────
def sh(cmd, check=False, timeout=None, stream=False):
    """跑命令。stream=True 时输出直通终端（给 docker build 这种长输出用）。"""
    if stream:
        p = subprocess.run(cmd, shell=isinstance(cmd, str))
        if check and p.returncode != 0:
            raise SystemExit("命令失败(%d): %s" % (p.returncode, cmd))
        return p.returncode
    p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                       text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise SystemExit("命令失败(%d): %s\n%s" % (p.returncode, cmd, p.stderr[-800:]))
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def c_ok(t): return "\033[32m%s\033[0m" % t
def c_warn(t): return "\033[33m%s\033[0m" % t
def c_err(t): return "\033[31m%s\033[0m" % t
def c_dim(t): return "\033[2m%s\033[0m" % t


def log(msg):
    print("  " + msg, flush=True)


def head(msg):
    print("\n" + "=" * 62)
    print("  " + msg)
    print("=" * 62, flush=True)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def git_json(url):
    """调 GitHub API 取 JSON。带 UA，否则 403。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "qqdeploy/1.0",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


# ── 健康检查 ──────────────────────────────────────────────────────────────────
def container_up():
    rc, out, _ = sh(["docker", "inspect", AGENT_CONTAINER, "--format",
                     "{{.State.Running}} {{.RestartCount}}"])
    if rc != 0:
        return False, "容器不存在"
    parts = out.strip().split()
    if parts and parts[0] == "true":
        restarts = parts[1] if len(parts) > 1 else "?"
        return True, "运行中（重启次数 %s）" % restarts
    return False, "未在运行"


def agent_api(path="/api/status"):
    rc, out, _ = sh(["curl", "-s", "-m", "8", "http://127.0.0.1:3210" + path])
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def health_check(verbose=True):
    """返回 (ok, 说明列表)。三项：容器在跑、onebot 连上、插件还在。"""
    notes, ok = [], True

    up, why = container_up()
    notes.append(("容器", up, why))
    ok = ok and up

    st = agent_api("/api/status")
    if st is None:
        notes.append(("控制台 API", False, "无响应"))
        ok = False
    else:
        ob = (st.get("onebot") or {}).get("connected")
        notes.append(("OneBot", bool(ob), "已连接" if ob else "未连接"))
        ok = ok and bool(ob)

    sk = agent_api("/api/skills")
    if sk is None:
        notes.append(("插件", False, "读不到 /api/skills"))
        ok = False
    else:
        total = (sk.get("summary") or {}).get("total", "?")
        broken = (sk.get("summary") or {}).get("broken", 0)
        good = broken in (0, "0")
        notes.append(("插件", good, "共 %s 个，异常 %s 个" % (total, broken)))
        ok = ok and good

    if verbose:
        for name, good, why in notes:
            log("  %-10s %s  %s" % (name, c_ok("OK") if good else c_err("FAIL"), c_dim(why)))
    return ok, notes


# ── 下载与解压 ────────────────────────────────────────────────────────────────
def resolve_ref(ref):
    """把分支名/短 sha 解析成完整 sha。返回 (sha, 显示名)。"""
    if re.fullmatch(r"[0-9a-f]{7,40}", ref):
        return ref, ref[:12]
    # 当作分支/标签处理
    try:
        j = git_json("%s/commits/%s" % (API, urllib.parse.quote(ref)))
        return j["sha"], j["sha"][:12]
    except Exception as exc:
        raise SystemExit("无法解析 %r：%s\n"
                         "（分支名拼错？或 API 限流？未认证时每小时 60 次）" % (ref, exc))


def download(sha, dest_dir):
    """下载指定 commit 的 tarball 并解压到 dest_dir（内容直接落在 dest_dir 下）。"""
    url = "%s/tarball/%s" % (API, sha)
    tmp = tempfile.mktemp(suffix=".tar.gz", dir=ROOT)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "qqdeploy/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        size = os.path.getsize(tmp)
        if size < 100_000:
            raise SystemExit("下载内容过小（%d 字节），可能是错误响应" % size)
        log("下载完成 %s" % c_dim("%.1f MB" % (size / 1048576)))

        os.makedirs(dest_dir, exist_ok=True)
        with tarfile.open(tmp, "r:gz") as tf:
            members = tf.getmembers()
            # tarball 顶层是 <owner>-<repo>-<sha>/，剥掉这一层
            roots = {m.name.split("/")[0] for m in members if m.name}
            if len(roots) != 1:
                raise SystemExit("tarball 结构异常：顶层目录不唯一 %s" % roots)
            root = roots.pop()
            # filter="data" 是 Python 3.12+ 才有的参数；本机实测 Python 3.10，
            # 传了会 TypeError，所以按版本探测后再决定要不要带。
            use_filter = sys.version_info >= (3, 12)
            for m in members:
                if not m.name.startswith(root + "/"):
                    continue
                m.name = m.name[len(root) + 1:]
                if m.name:
                    if use_filter:
                        tf.extract(m, dest_dir, filter="data")
                    else:
                        tf.extract(m, dest_dir)
        log("解压完成 → %s" % c_dim(dest_dir))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── 校验 ──────────────────────────────────────────────────────────────────────
BIND_PATCH_OLD = "server.listen(port, '127.0.0.1', () => {"
BIND_PATCH_NEW = "server.listen(port, process.env.QQ_AGENT_BIND_HOST || '127.0.0.1', () => {"


def ensure_bind_patch(rel_dir):
    """
    保证 app/src/app.js 读 QQ_AGENT_BIND_HOST。
    容器里必须监听 0.0.0.0，否则宿主机端口映射连不上。
    仓库里的版本已经打好补丁；这里只是兜底（部署老 commit 时会用到）。
    """
    p = os.path.join(rel_dir, "app", "src", "app.js")
    if not os.path.exists(p):
        raise SystemExit("缺少 app/src/app.js，这不是一个有效的 release")
    with open(p, encoding="utf-8") as fh:
        t = fh.read()
    if "QQ_AGENT_BIND_HOST" in t:
        return "已有补丁"
    if t.count(BIND_PATCH_OLD) != 1:
        raise SystemExit("app.js 绑定地址那行锚点不唯一，拒绝部署")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(t.replace(BIND_PATCH_OLD, BIND_PATCH_NEW, 1))
    return "已补补丁"


def detect_live_project():
    """
    读线上 qq-agent 容器的 compose 项目名。
    首次接管（还没有部署记录）时用它校准 COMPOSE_PROJECT，
    免得项目名不对导致"已存在同名容器"的冲突。
    """
    rc, out, _ = sh(["docker", "inspect", AGENT_CONTAINER, "--format",
                     '{{index .Config.Labels "com.docker.compose.project"}}'])
    if rc == 0 and out.strip() and out.strip() != "<no value>":
        return out.strip()
    return None


def verify(rel_dir):
    """语法检查 + 模块依赖检查。任一不过就中止（线上不动）。"""
    src = os.path.join(rel_dir, "app", "src")
    js = sorted(f for f in os.listdir(src) if f.endswith(".js"))
    log("语法检查（%d 个文件）…" % len(js))

    # 用宿主上的 node 跑：镜像里的 node 要起容器，太重。
    # 宿主没 node 就退回容器跑（用当前镜像）。
    rc, _, _ = sh(["bash", "-lc", "command -v node"])
    if rc == 0:
        bad = []
        for f in js:
            rc, _, err = sh(["node", "--check", os.path.join(src, f)])
            if rc != 0:
                bad.append((f, err.strip().splitlines()[:2]))
        if bad:
            for f, err in bad:
                log(c_err("语法错误 %s" % f))
                for e in err:
                    log("    " + e)
            raise SystemExit("语法检查未通过，已中止（线上未改动）")
        log(c_ok("语法全部通过"))
    else:
        log(c_warn("宿主没有 node，跳过语法检查（模块依赖检查仍会跑）"))

    # 模块依赖检查：找出 import 了不存在的文件 / 未导出的符号
    checker = os.path.join(rel_dir, "tools", "check-modules.py")
    if os.path.exists(checker):
        rc, out, err = sh(["python3", checker, src])
        tail = [l for l in (out or "").splitlines() if l.strip()][-3:]
        for l in tail:
            log("  " + l)
        if rc != 0:
            raise SystemExit("模块依赖图不完整，已中止（线上未改动）")
    else:
        log(c_warn("release 里没有 tools/check-modules.py，跳过依赖检查"))


# ── docker 操作 ───────────────────────────────────────────────────────────────
def write_release_compose(rel_dir):
    """
    每个 release 目录里生成自己的 compose 文件，指向本 release 作为构建上下文。
    这样 current 软链一换，重建用的就是新代码。
    配置从宿主 /opt/qq/.env 读取（该文件不入版本库）。

    ⚠️ 这里**不叠加** /opt/qq/docker-compose.platform.yml：
       docker compose 会把 -f 列出的文件合并后整体校验，而 platform.yml 里带着
       napcat 的 shm_size 覆盖 —— 本文件不定义 napcat，合并后就会报
       "service napcat has neither an image nor a build context specified"，
       整个构建直接失败（dry-run 时实测撞到）。
       所以 napcat 需要的 shm_size 等设置在本文件里内联，保持自包含。
    """
    compose = """# 本文件由 qqdeploy 生成（每个 release 一份，勿手改）
#
# 只管理 qq-agent。napcat 由 /opt/qq/docker-compose.yml 单独管理，两者共存：
#   · 本文件用 external 网络 qq_qqnet（napcat 建的），所以能互相访问
#   · 环境变量来自 /opt/qq/.env（--env-file 传入）
services:
  qq-agent:
    build:
      # ★ context 必须是**仓库根**（不是 app/）：deploy/Dockerfile 里写的是
      #   COPY app/src/ ./src/ 这类路径，相对 context 解析。
      #   最初误写成 context: <rel>/app + dockerfile: Dockerfile，报
      #   "failed to read dockerfile: open Dockerfile: no such file or directory"
      #   —— dry-run 实测撞到过，别改回去。
      context: %s
      dockerfile: deploy/Dockerfile
    image: qq-agent:local
    container_name: qq-agent
    restart: always
    environment:
      - TZ=Asia/Shanghai
      - NODE_ENV=production
      - QQ_AGENT_DATA_DIR=/app/data
      - QQ_AGENT_BIND_HOST=0.0.0.0
    ports:
      - 127.0.0.1:3210:3210
    volumes:
      - %s/qq-agent-data:/app/data
      - %s/qq-agent-plugins:/app/plugins
    networks: [qqnet]

networks:
  qqnet:
    external: true
    name: qq_qqnet
""" % (rel_dir, ROOT, ROOT)
    with open(os.path.join(rel_dir, "compose.release.yml"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(compose)


def compose_files(rel_dir):
    """release 自带的 compose + 显式项目名。"""
    return ["-p", COMPOSE_PROJECT, "-f", os.path.join(rel_dir, "compose.release.yml")]


def rebuild(rel_dir, quiet=False, dry_run=False):
    """无缓存重建 + 切换容器。返回是否成功（只看命令是否跑通，健康另测）。"""
    base = ["docker", "compose"] + compose_files(rel_dir)
    env = ["--env-file", ENV_FILE] if os.path.exists(ENV_FILE) else []
    if dry_run:
        # --dry-run：完整构建镜像（验证代码真的能构建），但**不切换容器** ——
        # 线上继续跑旧版本。用于首次迁移或大改动前的演练。
        log(c_warn("dry-run：只构建镜像，不切换容器"))
        rc = sh(base + env + ["build", "--no-cache", "qq-agent"], stream=not quiet)
        if rc != 0:
            log(c_err("构建失败"))
            return False
        log("镜像构建成功（线上容器未被触碰）")
        return True
    log("重建镜像（--no-cache，约 3~4 分钟）…")
    rc = sh(base + env + ["build", "--no-cache", "qq-agent"], stream=not quiet)
    if rc != 0:
        log(c_err("构建失败"))
        return False
    log("切换容器…")
    rc = sh(base + env + ["up", "-d", "--force-recreate", "qq-agent"],
            stream=not quiet)
    return rc == 0


def wait_healthy(seconds=HEALTH_TIMEOUT):
    """轮询等待健康，超时返回 False。"""
    deadline = time.time() + seconds
    last = []
    while time.time() < deadline:
        ok, notes = health_check(verbose=False)
        last = notes
        if ok:
            return True, notes
        time.sleep(6)
    return False, last


# ── 部署记录 ──────────────────────────────────────────────────────────────────
def load_state():
    return read_json(STATE, {}) or {}


def save_state(ref, sha, rel_dir, who, note=""):
    """记录部署历史。current 软链由 switch_current() 负责，这里不再重复写。"""
    prev = load_state()
    history = prev.get("history") or []
    entry = {"sha": sha, "ref": ref, "dir": rel_dir,
             "at": time.strftime("%Y-%m-%d %H:%M:%S"), "by": who, "note": note}
    history.append(entry)
    write_json(STATE, {
        "current": entry,
        "previous": prev.get("current"),
        "history": history[-50:],
    })


def switch_current(rel_dir):
    """原子切换 current 软链。"""
    tmp = CURRENT + ".tmp"
    if os.path.islink(tmp) or os.path.exists(tmp):
        os.remove(tmp)
    os.symlink(rel_dir, tmp)
    os.replace(tmp, CURRENT)


def prune_releases(keep=KEEP_RELEASES):
    """只保留最近 N 个 release（按目录名排序不可靠，用 mtime）。"""
    if not os.path.isdir(RELEASES):
        return
    entries = []
    for name in os.listdir(RELEASES):
        p = os.path.join(RELEASES, name)
        if os.path.isdir(p):
            entries.append((os.path.getmtime(p), p))
    entries.sort(reverse=True)
    cur = os.path.realpath(CURRENT) if os.path.islink(CURRENT) else ""
    prev = (load_state().get("previous") or {}).get("dir", "")
    for _, p in entries[keep:]:
        if os.path.realpath(p) in (cur, os.path.realpath(prev) if prev else ""):
            continue
        log("清理旧版本 %s" % c_dim(os.path.basename(p)))
        shutil.rmtree(p, ignore_errors=True)


def whoami():
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"


# ── 子命令 ────────────────────────────────────────────────────────────────────
def cmd_status():
    st = load_state()
    cur = st.get("current")
    if not cur:
        log(c_warn("还没有部署记录"))
    else:
        log("当前部署 : %s%s" % (c_ok(cur["sha"][:12]),
                                ("  (%s)" % cur["ref"]) if cur.get("ref") else ""))
        log("部署时间 : %s" % cur.get("at"))
        log("部署人   : %s" % cur.get("by"))
        log("目录     : %s" % cur.get("dir"))
    prev = st.get("previous")
    if prev:
        log("上一个   : %s  (%s by %s)" % (prev["sha"][:12], prev.get("at"), prev.get("by")))
    if os.path.islink(CURRENT):
        log("current  : %s" % os.path.realpath(CURRENT))
    log("")
    log("健康检查：")
    health_check()
    return 0


def cmd_list():
    st = load_state()
    cur = (st.get("current") or {}).get("sha", "")
    hist = st.get("history") or []
    if not hist:
        log("（无部署历史）")
        return 0
    for e in reversed(hist):
        mark = c_ok("← 当前") if e["sha"] == cur else ""
        log("%s  %-10s %s  by %-8s %s" % (
            e["sha"][:12], e.get("ref", "")[:10], e.get("at", ""), e.get("by", ""), mark))
    log("")
    log("磁盘上的 release 目录：")
    if os.path.isdir(RELEASES):
        for name in sorted(os.listdir(RELEASES)):
            p = os.path.join(RELEASES, name)
            if os.path.isdir(p):
                sz = sum(os.path.getsize(os.path.join(r, f))
                         for r, _, fs in os.walk(p) for f in fs) / 1048576
                log("  %-14s %6.1f MB" % (name, sz))
    return 0


def cmd_rollback(target=None):
    st = load_state()
    cur = st.get("current") or {}
    prev = st.get("previous") or {}

    if target:
        rel_dir = os.path.join(RELEASES, target) if not os.path.isabs(target) else target
        if not os.path.isdir(rel_dir):
            # 允许传完整 sha
            cands = [d for d in os.listdir(RELEASES) if d.startswith(target)]
            if len(cands) != 1:
                raise SystemExit("找不到 release：%s（可用 --list 查看）" % target)
            rel_dir = os.path.join(RELEASES, cands[0])
        sha = os.path.basename(rel_dir)
        ref = "rollback:%s" % target
    else:
        if not prev:
            raise SystemExit("没有上一个版本可回滚（--list 看看有哪些）")
        rel_dir = prev["dir"]
        sha = prev["sha"]
        ref = "rollback:%s" % prev.get("ref", "")

    head("回滚到 %s" % sha[:12])
    log("目标目录: %s" % rel_dir)
    if not os.path.isdir(rel_dir):
        raise SystemExit("目标 release 目录不存在（可能已被清理）：%s" % rel_dir)

    write_release_compose(rel_dir)
    switch_current(rel_dir)
    log("current 已切到 %s" % c_dim(rel_dir))

    if not rebuild(rel_dir):
        # 回滚途中构建失败 → 尽力切回原版本
        log(c_err("重建失败！尝试切回原版本 %s" % cur.get("sha", "?")[:12]))
        if cur.get("dir") and os.path.isdir(cur["dir"]):
            switch_current(cur["dir"])
            rebuild(cur["dir"], quiet=True)
        raise SystemExit("回滚失败")

    ok, notes = wait_healthy()
    if not ok:
        for name, good, why in notes:
            log("  %-10s %s  %s" % (name, c_ok("OK") if good else c_err("FAIL"), why))
        raise SystemExit("回滚后健康检查未通过，请人工介入")

    save_state(ref, sha, rel_dir, whoami(), note="rollback")
    log(c_ok("回滚完成：%s" % sha[:12]))
    health_check()
    return 0


def cmd_deploy(ref, dry_run=False):
    st = load_state()
    before = st.get("current") or {}

    head("部署 %s%s" % (ref, "（dry-run）" if dry_run else ""))
    sha, short = resolve_ref(ref)
    log("提交: %s" % short)

    if before.get("sha") == sha and not dry_run:
        log(c_warn("当前已经部署的就是这个提交（%s）。" % short))
        log("如需强制重建，先 --rollback 到别的版本，或改一个提交再部署。")
        return 0

    rel_dir = os.path.join(RELEASES, sha[:12])

    # 1) 准备代码（已存在则复用，避免重复下载）
    if os.path.isdir(rel_dir) and os.path.exists(os.path.join(rel_dir, "app", "src", "app.js")):
        log("复用已有 release 目录 %s" % c_dim(rel_dir))
    else:
        log("从 GitHub 下载…")
        download(sha, rel_dir)

    # 2) 校验（不过就中止，线上不动）
    log("补丁检查: %s" % ensure_bind_patch(rel_dir))
    verify(rel_dir)

    # 3) 生成该 release 的 compose
    write_release_compose(rel_dir)

    if dry_run:
        # 演练：只构建，不切 current、不动容器、不写部署记录
        log("dry-run：不切换 current 软链、不写部署记录")
        if not rebuild(rel_dir, dry_run=True):
            raise SystemExit("dry-run 构建失败（线上未受影响）")
        log(c_ok("dry-run 通过：代码可构建，可以直接正式部署"))
        log("正式部署命令: sudo qqdeploy %s" % ref)
        return 0

    # 4) 切换 + 重建
    switch_current(rel_dir)
    log("current → %s" % c_dim(rel_dir))
    if not rebuild(rel_dir):
        log(c_err("构建/切换失败，回滚到上一个版本"))
        if before.get("dir") and os.path.isdir(before["dir"]):
            switch_current(before["dir"])
            rebuild(before["dir"], quiet=True)
        raise SystemExit("部署失败，已回滚")

    # 5) 健康检查，不过就自动回滚
    log("等待健康检查（最多 %ds）…" % HEALTH_TIMEOUT)
    ok, notes = wait_healthy()
    if not ok:
        for name, good, why in notes:
            log("  %-10s %s  %s" % (name, c_ok("OK") if good else c_err("FAIL"), why))
        log(c_err("健康检查未通过，自动回滚到上一版本"))
        if before.get("dir") and os.path.isdir(before["dir"]):
            switch_current(before["dir"])
            rebuild(before["dir"], quiet=True)
            wait_healthy(90)
            raise SystemExit("部署失败（已回滚到 %s）" % before.get("sha", "?")[:12])
        raise SystemExit("部署失败，且没有可回滚的版本 —— 请人工检查")

    save_state(ref, sha, rel_dir, whoami())
    prune_releases()
    log(c_ok("部署成功：%s (%s)" % (short, ref)))
    health_check()
    log("")
    log("回滚命令: sudo qqdeploy --rollback")
    return 0


def main():
    ap = argparse.ArgumentParser(
        prog="qqdeploy",
        description="把 GitHub 仓库的分支/提交部署到本机（QQ Agent）")
    ap.add_argument("ref", nargs="?", help="分支名或 commit sha")
    ap.add_argument("--status", action="store_true", help="看当前部署状态")
    ap.add_argument("--list", action="store_true", help="列出历史与保留的版本")
    ap.add_argument("--dry-run", action="store_true",
                    help="演练：下载+校验+构建镜像，但不切换容器、不写部署记录")
    ap.add_argument("--rollback", nargs="?", const="", metavar="SHA",
                    help="回滚（默认回到上一个部署过的版本）")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("需要 root：请用 sudo qqdeploy ...")

    os.makedirs(RELEASES, exist_ok=True)

    # 首次接管：线上容器可能是用别的 compose 文件（因而别的项目名）建的。
    # 项目名不对，docker compose 就不会去替换它，而是报"容器名已被占用"。
    # 这里读实际标签校准一次（无部署记录时才做，之后以记录为准）。
    if not load_state().get("current"):
        live = detect_live_project()
        if live and live != COMPOSE_PROJECT:
            globals()["COMPOSE_PROJECT"] = live
            log("检测到线上容器属于 compose 项目 %r，本次按该项目接管"
                % live)

    # 免锁的子命令
    if args.status:
        return cmd_status()
    if args.list:
        return cmd_list()

    # 需要锁的：部署 / 回滚（都会重建容器，不能并发）
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(
                "另一个部署正在进行中（锁 %s 被占用）。\n"
                "build --no-cache 要 3~4 分钟，请等它结束再试。\n"
                "若确认没人部署，可查看：ps aux | grep qqdeploy" % LOCK)
        lock.write("%s %s\n" % (whoami(), time.strftime("%Y-%m-%d %H:%M:%S")))
        lock.flush()

        if args.rollback is not None:
            return cmd_rollback(args.rollback or None)
        if not args.ref:
            ap.print_help()
            return 1
        return cmd_deploy(args.ref, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
