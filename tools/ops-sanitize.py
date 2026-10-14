#!/usr/bin/env python3
"""
ops-sanitize.py —— 生成脱敏后的运维脚本（入库用）

背景：服务器上的 napcat-guard.sh / qr-server.py / qr-server.service 里
硬编码了 NapCat WebUI 的真实 token。这些文件要进公开仓库，必须先把
凭据抽出去，改成从 /opt/qq/.env（该文件已在 .gitignore 中，绝不入库）读取。

本脚本产出到 /tmp/ops-sanitized/，保持 UTF-8 + LF。
"""

import io
import os
import re

OUT = "/tmp/ops-sanitized"

# ── napcat-guard.sh ──
# 与原版的唯一差别：WEBUI_TOKEN 不再硬编码，改为从 .env 读取。
GUARD = r'''#!/usr/bin/env bash
# =============================================================================
#  napcat-guard.sh  ——  NapCat 掉线守护 + 告警
#
#  每 30 秒检查一次，判定顺序（从最可靠到兜底）：
#    1) napcat 容器是否在运行
#    2) 宿主机 127.0.0.1:3001 是否在监听（OneBot WS 端口已发布到回环）
#    3) 通过 NapCat WebUI 接口问账号是否真的登录着  ← 最关键的判据
#    4) 日志里最后一次「账号状态变更」是否已被后续登录覆盖
#
#  ★ 不要用 `docker exec napcat ss/netstat` 判断端口：
#    NapCat 镜像里没有这两个工具，那样会永远误报 DOWN（v1 版本踩过这个坑）。
#
#  ★ WebUI 登录接口有每分钟 3 次的限流，而本脚本每 30 秒跑一次，
#    所以凭据必须缓存复用，只在失效（401）时才重新登录一次。
#    （v2 的坑：每次跑都重新登录 → 触发限流 → 返回空 → 状态误判成 KICKED）
#
#  发现异常时：往 journald 写 [NAPWARN]，并把摘要写进 /opt/qq/napcat-status.txt
#
#  安装：
#    sudo install -m 755 napcat-guard.sh /opt/qq/napcat-guard.sh
#    sudo install -m 644 napcat-guard.service napcat-guard.timer /etc/systemd/system/
#    sudo systemctl daemon-reload && sudo systemctl enable --now napcat-guard.timer
#
#  凭据：从 COMPOSE_DIR/.env 读 WEBUI_TOKEN（该文件不入版本库）
# =============================================================================

set -uo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/qq}"
STATUS_FILE="$COMPOSE_DIR/napcat-status.txt"
STATE_FILE="$COMPOSE_DIR/.napcat-guard-state"
CONTAINER="napcat"
WS_PORT=3001
WEBUI_PORT=6099

# ── 从 .env 读 WebUI token（不再硬编码）──
if [ -f "$COMPOSE_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$COMPOSE_DIR/.env"
  set +a
fi
WEBUI_TOKEN="${WEBUI_TOKEN:-}"
if [ -z "$WEBUI_TOKEN" ]; then
  echo "napcat-guard: 未找到 WEBUI_TOKEN（请检查 $COMPOSE_DIR/.env）" >&2
fi

last_state=""
[ -f "$STATE_FILE" ] && last_state="$(cat "$STATE_FILE" 2>/dev/null || true)"

cd "$COMPOSE_DIR" || exit 1

# ---- 1) 容器 ----
container_running="no"
docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER" && container_running="yes"
restart_count="$(docker inspect "$CONTAINER" --format '{{.RestartCount}}' 2>/dev/null || echo '?')"
started_at="$(docker inspect "$CONTAINER" --format '{{.State.StartedAt}}' 2>/dev/null || echo '?')"

# ---- 2) 宿主机回环端口（不用进容器）----
port_ok="no"
ss -ltn 2>/dev/null | grep -qE "127\.0\.0\.1:${WS_PORT}\b" && port_ok="yes"

# ---- 3) 账号真实登录状态（走 WebUI 接口）----
CRED_FILE="$COMPOSE_DIR/.napcat-guard-cred"
login_state="unknown"      # logged_in | not_logged_in | unknown
login_detail=""
if command -v curl >/dev/null 2>&1 && [ "$container_running" = "yes" ] && [ -n "$WEBUI_TOKEN" ]; then

  ask_login_status() {
    # $1 = 凭据；成功则输出响应体，失败输出空
    curl -s -m 8 -X POST "http://127.0.0.1:${WEBUI_PORT}/api/QQLogin/CheckLoginStatus" \
      -H "Authorization: Bearer $1" -H 'Content-Type: application/json' -d '{}' 2>/dev/null
  }

  cred=""
  [ -f "$CRED_FILE" ] && cred="$(cat "$CRED_FILE" 2>/dev/null || true)"

  # 缓存凭据先试一次
  resp=""
  if [ -n "$cred" ]; then
    resp="$(ask_login_status "$cred")"
    if printf '%s' "$resp" | grep -q 'Unauthorized'; then
      resp=""     # 凭据过期，下面重新登录
      cred=""
    fi
  fi

  # 缓存不可用时才登录（有限流，尽量少调）
  if [ -z "$cred" ]; then
    hash="$(printf '%s' "${WEBUI_TOKEN}.napcat" | sha256sum | awk '{print $1}')"
    cred="$(curl -s -m 8 -X POST "http://127.0.0.1:${WEBUI_PORT}/api/auth/login" \
              -H 'Content-Type: application/json' \
              -d "{\"hash\":\"${hash}\"}" 2>/dev/null \
            | grep -oE '"Credential":"[^"]+"' | head -1 | sed 's/"Credential":"//;s/"//')"
    if [ -n "$cred" ]; then
      printf '%s' "$cred" > "$CRED_FILE" 2>/dev/null || true
      chmod 600 "$CRED_FILE" 2>/dev/null || true
      resp="$(ask_login_status "$cred")"
    else
      login_detail="WebUI 登录被拒（可能触发每分钟 3 次限流）"
    fi
  fi

  if printf '%s' "$resp" | grep -q '"isLogin":true'; then
    login_state="logged_in"
  elif printf '%s' "$resp" | grep -q '"isLogin":false'; then
    login_state="not_logged_in"
    login_detail="$(printf '%s' "$resp" | grep -oE '"loginPhase":"[^"]+"' | head -1 | cut -d'"' -f4)"
    err="$(printf '%s' "$resp" | grep -oE '"loginError":"[^"]*"' | head -1 | cut -d'"' -f4)"
    [ -n "$err" ] && login_detail="$login_detail / $err"
  fi
fi

# ---- 4) 最近日志关键行 ----
logs="$(docker logs --tail 400 "$CONTAINER" 2>&1 || true)"
last_kick="$(printf '%s\n' "$logs" | grep -E 'KickedOffLine|下线通知|登录已失效' | tail -1 | cut -c1-140)"
last_login_err="$(printf '%s\n' "$logs" | grep -E 'Login Error' | tail -1 | cut -c1-140)"
last_sent="$(printf '%s\n' "$logs" | grep -E '发送 -> ' | tail -1 | cut -c1-120)"
last_recv="$(printf '%s\n' "$logs" | grep -E '接收 <- ' | tail -1 | cut -c1-120)"

# 在线态：只看「最后一次被踢之后」的状态变更，否则会把历史上的"变更为离线"当成当前状态
# （v1 的坑：刚登录成功却显示"离线"，因为被踢那次的记录还在日志里）
kick_line_no="$(printf '%s\n' "$logs" | grep -nE 'KickedOffLine|登录已失效' | tail -1 | cut -d: -f1)"
if [ -n "$kick_line_no" ]; then
  tail_after_kick="$(printf '%s\n' "$logs" | tail -n +$((kick_line_no + 1)))"
else
  tail_after_kick="$logs"
fi
online_state="$(printf '%s\n' "$tail_after_kick" | grep -E '账号状态变更为' | tail -1 | sed 's/.*账号状态变更为//' | tr -d '\r' | xargs || true)"
# 被踢之后的日志里若出现了「登录成功 / 收到消息」，那就是已经在线的铁证，
# 比可能缺失的"状态变更"记录更可靠（v2 的坑：登录成功但日志没有状态变更行 → 误显示离线）
if printf '%s\n' "$tail_after_kick" | grep -qE '登录成功|接收 <- '; then
  online_state="在线"
fi
[ -z "$online_state" ] && online_state="（本次会话内无状态变更记录）"

# ---- 判定 ----
state="OK"
detail=""
if [ "$container_running" != "yes" ]; then
  state="DOWN"; detail="容器未运行"
elif [ "$port_ok" != "yes" ]; then
  state="DOWN"; detail="宿主机 ${WS_PORT} 未监听（NapCat 可能正在重启）"
elif [ "$login_state" = "not_logged_in" ]; then
  state="KICKED"; detail="账号未登录${login_detail:+（$login_detail）}，需要扫码"
elif [ "$login_state" = "unknown" ] && [ -n "$last_kick" ]; then
  # 问不到登录状态时的兜底：被踢过且之后没有登录成功记录
  kick_line="$(printf '%s\n' "$logs" | grep -nE 'KickedOffLine|登录已失效' | tail -1 | cut -d: -f1)"
  ready_line="$(printf '%s\n' "$logs" | grep -nE '登录成功' | tail -1 | cut -d: -f1)"
  if [ -z "$ready_line" ] || [ "${ready_line:-0}" -lt "${kick_line:-0}" ]; then
    state="KICKED"; detail="日志显示被踢下线，需重新扫码"
  fi
fi

now="$(date '+%Y-%m-%d %H:%M:%S')"

{
  echo "================ NapCat 守护状态 ================"
  echo "检查时间   : $now"
  echo "总体状态   : $state ${detail:+($detail)}"
  echo "容器运行   : $container_running    （重启次数 $restart_count）"
  echo "OneBot 端口: $port_ok              （宿主机 127.0.0.1:${WS_PORT}）"
  echo "账号登录   : $login_state ${login_detail:+（$login_detail）}"
  echo "容器启动于 : $started_at"
  echo "日志在线态 : $online_state   （仅统计最近一次被踢之后的记录）"
  echo
  echo "--- 最近一次被踢 ---"
  echo "${last_kick:-（无记录）}"
  echo "--- 最近一次登录错误 ---"
  echo "${last_login_err:-（无）}"
  echo "--- 最近一次收到消息 ---"
  echo "${last_recv:-（无）}"
  echo "--- 最近一次发送消息 ---"
  echo "${last_sent:-（无）}"
  echo
  echo "状态含义:"
  echo "  OK     = 容器在跑、端口在听、账号已登录"
  echo "  KICKED = 账号被腾讯踢下线，NapCat 停在二维码页，需要人工扫码"
  echo "  DOWN   = 容器或端口异常（NapCat 通常会自动重启，稍等再看）"
  echo "重新扫码: 先开 SSH 隧道，再访问"
  echo "          http://127.0.0.1:6099/webui  （token 见 $COMPOSE_DIR/.env 的 WEBUI_TOKEN）"
  echo "================================================="
} > "$STATUS_FILE" 2>/dev/null || true

if [ "$state" != "$last_state" ]; then
  echo "$state" > "$STATE_FILE" 2>/dev/null || true
  if [ "$state" = "KICKED" ]; then
    logger -t napcat-guard -p daemon.warning "[NAPWARN] 账号被踢下线，机器人已停止响应，需重新扫码 ($now)"
    echo "[NAPWARN] $now 账号被踢下线 —— $detail" >&2
  elif [ "$state" = "DOWN" ]; then
    logger -t napcat-guard -p daemon.err "[NAPWARN] NapCat 异常：$detail ($now)"
    echo "[NAPWARN] $now NapCat 异常 —— $detail" >&2
  elif [ "$state" = "OK" ] && [ -n "$last_state" ]; then
    logger -t napcat-guard -p daemon.info "[napcat-guard] 已恢复正常 ($now)"
    echo "[napcat-guard] $now 已恢复正常" >&2
  fi
fi

exit 0
'''

# ── qr-server.py ──
# 与原版的唯一差别：NAPCAT_WEBUI_TOKEN 不再有硬编码默认值，缺失时明确报错。
# ⚠️ 不要把真实 token 写在这里当"查找模式" —— 本脚本会入公开仓库，
#    那等于把凭据一起公开。改为按代码结构匹配（字面量默认值）。
QR_LITERAL_DEFAULT = re.compile(
    r'''WEBUI_TOKEN\s*=\s*os\.environ\.get\(\s*["']NAPCAT_WEBUI_TOKEN["']\s*,\s*["'][^"']+["']\s*\)''')
QR_PATCH_NEW = '''WEBUI_TOKEN = os.environ.get("NAPCAT_WEBUI_TOKEN", "").strip()
if not WEBUI_TOKEN:
    import sys
    sys.exit("qr-server: 缺少环境变量 NAPCAT_WEBUI_TOKEN（systemd 单元里用 EnvironmentFile 从 .env 引入）")'''

# ── systemd 单元 ──
GUARD_SERVICE = '''[Unit]
Description=NapCat 掉线守护（被踢下线 / 容器异常时告警）
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/qq
ExecStart=/opt/qq/napcat-guard.sh
'''

GUARD_TIMER = '''[Unit]
Description=每 30 秒运行一次 NapCat 掉线守护

[Timer]
OnBootSec=60
OnUnitActiveSec=30
AccuracySec=5s
Unit=napcat-guard.service

[Install]
WantedBy=timers.target
'''

QR_SERVICE = '''[Unit]
Description=NapCat 实时登录二维码网页
After=docker.service
Requires=docker.service

[Service]
Type=simple
# 凭据从 .env 读，不写在本文件里（本仓库是公开的）
EnvironmentFile=/opt/qq/.env
ExecStart=/usr/bin/python3 /opt/qq/qr-server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
'''


def write(name, text):
    p = os.path.join(OUT, name)
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  %-28s %6d 字节" % (name, os.path.getsize(p)))


def main():
    os.makedirs(OUT, exist_ok=True)
    print("=== 生成脱敏文件到 %s ===" % OUT)
    write("napcat-guard.sh", GUARD)

    # qr-server.py：读原文件、把"带字面量默认值的凭据行"换成"必须由环境变量提供"
    src = "/root/ops-collect/qr-server.py"
    with io.open(src, "r", encoding="utf-8") as fh:
        qr = fh.read()
    if not QR_LITERAL_DEFAULT.search(qr):
        print("  ⚠️ qr-server.py 未找到硬编码凭据行（可能已脱敏），原样复制")
        write("qr-server.py", qr)
    else:
        write("qr-server.py", QR_LITERAL_DEFAULT.sub(QR_PATCH_NEW, qr, count=1))

    write("systemd/napcat-guard.service", GUARD_SERVICE)
    write("systemd/napcat-guard.timer", GUARD_TIMER)
    write("systemd/qr-server.service", QR_SERVICE)

    print("\n=== 校验：脱敏文件里不应再出现硬编码凭据 ===")
    # 不把真实 token 写在这里当检测模式 —— 本脚本自己也会入公开仓库。
    # 改为启发式：揪出「WEBUI_TOKEN 直接赋值为字面量」这种写法。
    literal_assign = re.compile(
        r'''(?i)(WEBUI_TOKEN|NAPCAT_WEBUI_TOKEN)["']?\s*[:=]\s*["'][A-Za-z0-9_\-]{16,}["']''')
    bad = []
    for root, _d, files in os.walk(OUT):
        for f in files:
            p = os.path.join(root, f)
            with io.open(p, "r", encoding="utf-8") as fh:
                c = fh.read()
            m = literal_assign.search(c)
            if m:
                bad.append((p, m.group(0)[:60]))
    if bad:
        for p, sample in bad:
            print("  ❌ 仍有硬编码凭据: %s  →  %s" % (p, sample))
        return 1
    print("  ✅ 干净（凭据一律从 .env / 环境变量读取）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
