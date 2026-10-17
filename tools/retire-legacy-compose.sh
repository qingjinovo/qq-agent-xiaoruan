#!/usr/bin/env bash
# =============================================================================
#  retire-legacy-compose.sh —— 退役旧 compose 文件（防止误用它部署陈旧代码）
#
#  背景：服务器改成 qqdeploy 部署后，代码来自 /opt/qq/releases/<sha>/，
#        而 /opt/qq/docker-compose.yml 的 build 仍指向已废弃的 /opt/qq/qq-agent。
#        有人顺手跑那条命令，就会用 10-13 的旧源码覆盖线上 —— 静默、无明显报错。
#
#  做法：
#    1) 新建 docker-compose.napcat.yml —— 只管理 napcat，作为它的唯一重启入口
#       （配置逐项对齐现有容器，不重建也能用）
#    2) 把旧的两个 compose 原地改名为 *.legacy（保留内容，可随时查/恢复）
#    3) 全程不重启任何容器 —— napcat 与 qq-agent 继续跑
#
#  幂等：重复执行安全。
# =============================================================================
set -euo pipefail

cd /opt/qq || exit 1

echo "===== 0. 改名前状态 ====="
docker ps --format '  {{.Names}}  {{.Status}}' | grep -E "qq-agent|napcat" || true
echo "  旧文件:"
for f in docker-compose.yml docker-compose.platform.yml; do
  [ -f "$f" ] && echo "    ✅ $f ($(stat -c %s $f) 字节)" || echo "    ✗  $f 不存在"
done

# ── 1. 生成 napcat 专属 compose ───────────────────────────────────────────────
echo
echo "===== 1. 生成 docker-compose.napcat.yml ====="
cat > docker-compose.napcat.yml <<'YAML'
# ---------------------------------------------------------------------------
#  NapCat 容器 —— 唯一的重启/更新入口
#
#  用法：
#    cd /opt/qq
#    docker compose -f docker-compose.napcat.yml up -d     # 启动/更新
#    docker compose -f docker-compose.napcat.yml restart   # 重启
#    docker compose -f docker-compose.napcat.yml logs -f   # 看日志
#
#  说明：
#    · 本文件只管 napcat。qq-agent 由 qqdeploy 管理（代码在 releases/<sha>/），
#      两者通过 external 网络 qq_qqnet 互通，互不干扰。
#    · 配置对齐 2026-10-17 运行中的容器：镜像 / shm 256m / 三个回环端口 /
#      两处 bind 挂载（配置 + QQ 登录态）。
#    · shm_size 必须在：容器里跑的是 Electron 内核的 QQ 客户端，
#      共享内存不足会直接崩且报错难懂（详见 docker-compose.platform.yml.legacy）。
#    · 凭据从 /opt/qq/.env 读（WEBUI_TOKEN / QQ_ACCOUNT），该文件不入版本库。
# ---------------------------------------------------------------------------
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: always
    shm_size: 256m
    environment:
      - NAPCAT_UID=${NAPCAT_UID:-0}
      - NAPCAT_GID=${NAPCAT_GID:-0}
      - WEBUI_TOKEN=${WEBUI_TOKEN:?需要在 /opt/qq/.env 里设置 WEBUI_TOKEN}
      - ACCOUNT=${QQ_ACCOUNT:-}
      - TZ=Asia/Shanghai
    ports:
      - 127.0.0.1:3000:3000     # OneBot HTTP API
      - 127.0.0.1:3001:3001     # OneBot 正向 WebSocket
      - 127.0.0.1:6099:6099     # NapCat WebUI（扫码/改配置）
    volumes:
      - ./napcat/config:/app/napcat/config
      - ./napcat/QQ:/app/.config/QQ     # 登录态；不挂每次重启都要重扫
    networks: [qqnet]

networks:
  qqnet:
    external: true
    name: qq_qqnet
YAML
echo "  已写入 ($(stat -c %s docker-compose.napcat.yml) 字节)"

# ── 2. 校验证书：config 只解析不动作 ──────────────────────────────────────────
echo
echo "===== 2. 校验新 compose（docker compose config，不碰容器）====="
if [ -f .env ]; then
  if docker compose --env-file .env -f docker-compose.napcat.yml config >/dev/null 2>/tmp/cfgerr.txt; then
    echo "  ✅ 解析通过"
    echo "  --- 关键字段（解析结果）---"
    docker compose --env-file .env -f docker-compose.napcat.yml config 2>/dev/null \
      | grep -E "image:|container_name:|shm_size:|host_ip:|published:" | sed 's/^/    /'
  else
    echo "  ❌ 解析失败：" >&2
    cat /tmp/cfgerr.txt | sed 's/^/    /' >&2
    echo "  已中止：改名未执行，旧文件保持原样" >&2
    exit 1
  fi
else
  echo "  ❌ /opt/qq/.env 不存在，无法解析（没有 WEBUI_TOKEN）" >&2
  exit 1
fi

# ── 3. 改名旧文件 ─────────────────────────────────────────────────────────────
echo
echo "===== 3. 退役旧 compose ====="
TS=$(date +%m%d-%H%M)
for f in docker-compose.yml docker-compose.platform.yml; do
  if [ -f "$f" ]; then
    mv "$f" "$f.legacy"
    echo "  ✅ $f → $f.legacy"
  elif [ -f "$f.legacy" ]; then
    echo "  （已退役，跳过）$f.legacy"
  else
    echo "  ⚠️ $f 与 $f.legacy 都不存在"
  fi
done

# 在 .legacy 头部加一句警告，免得有人以为它还能用
for f in docker-compose.yml.legacy docker-compose.platform.yml.legacy; do
  [ -f "$f" ] || continue
  if ! head -3 "$f" | grep -q "已退役"; then
    {
      echo "# ==========================================================================="
      echo "#  ⚠️ 本文件已退役（$(date +%Y-%m-%d)）—— 仅供查阅，不要用它部署！"
      echo "#"
      echo "#  它里面的 build.context 指向 /opt/qq/qq-agent，那是 10-13 的旧源码。"
      echo "#  现在线上代码来自 /opt/qq/releases/<sha>/，由 qqdeploy 管理。"
      echo "#"
      echo "#  · 部署机器人：sudo qqdeploy <分支>   （不是这个文件）"
      echo "#  · 重启 napcat：docker compose -f /opt/qq/docker-compose.napcat.yml restart"
      echo "# ==========================================================================="
      cat "$f"
    } > "$f.tmp" && mv "$f.tmp" "$f"
    echo "  已在 $f 头部加退役警告"
  fi
done

# ── 4. 验证 ───────────────────────────────────────────────────────────────────
echo
echo "===== 4. 改名后验证（容器应完全不受影响）====="
docker ps --format '  {{.Names}}  {{.Status}}' | grep -E "qq-agent|napcat" || true
echo
echo "  旧命令现在会怎样（应立刻报错，而不是静默用旧代码）:"
docker compose -f docker-compose.yml config >/dev/null 2>/tmp/olderr.txt \
  && echo "    ⚠️ 竟然还能用？请检查" \
  || echo "    ✅ 已失效：$(head -1 /tmp/olderr.txt | cut -c1-90)"

echo
echo "  napcat 新入口可用性:"
docker compose --env-file .env -f docker-compose.napcat.yml ps 2>/dev/null | sed 's/^/    /' || echo "    （ps 无输出属正常）"

echo
echo "  机器人健康:"
curl -s -m 8 http://127.0.0.1:3210/api/status | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('    onebot:', d['onebot']['connected'], '|', d['onebot'].get('self'))
" 2>/dev/null || echo "    （控制台无响应）"

curl -s -m 8 http://127.0.0.1:3210/api/skills | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('    插件  :', json.dumps(d.get('summary'), ensure_ascii=False))
" 2>/dev/null || true

echo
echo "===== 5. 目录现状 ====="
ls -la /opt/qq/*.yml* 2>/dev/null | awk '{print "  "$9"  "$5" 字节"}'

echo
echo "完成。"
