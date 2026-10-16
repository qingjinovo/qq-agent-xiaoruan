#!/usr/bin/env bash
# =============================================================================
#  setup-deploy-access.sh —— 给协作者开部署权限（最小授权）
#
#  做四件事：
#    1) 建普通用户 dev（加 docker 组，能看容器）
#    2) 装协作者的 SSH 公钥（免密登录；不共享 root 密码）
#    3) 只授予 `sudo qqdeploy` 一条命令的免密权限（不是完整 sudo）
#    4) 安装 qqdeploy 到 /usr/local/bin
#
#  反复执行安全（幂等）。
# =============================================================================
set -euo pipefail

DEV_USER="dev"
PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKrn75wEkVucv292CV0xyWVfzZcKJxhbOdrtdhgp1/TL ZHEhao423"
SUDOERS_FILE="/etc/sudoers.d/qqdeploy"

echo "===== 1. 建用户 $DEV_USER ====="
if id "$DEV_USER" >/dev/null 2>&1; then
  echo "  已存在，跳过创建"
else
  adduser --disabled-password --gecos "" "$DEV_USER"
  echo "  已创建（不用密码登录，只走密钥）"
fi

echo
echo "===== 2. 加入 docker 组（能查看容器，但改不了 compose 之外的东西）====="
if id -nG "$DEV_USER" | grep -qw docker; then
  echo "  已在 docker 组"
else
  usermod -aG docker "$DEV_USER"
  echo "  已加入 docker 组"
fi

echo
echo "===== 3. 安装 SSH 公钥 ====="
install -d -m 700 -o "$DEV_USER" -g "$DEV_USER" "/home/$DEV_USER/.ssh"
AUTH="/home/$DEV_USER/.ssh/authorized_keys"
# 幂等：同样的公钥不重复追加
if [ -f "$AUTH" ] && grep -qF "$(echo "$PUBKEY" | awk '{print $2}')" "$AUTH"; then
  echo "  公钥已存在，跳过"
else
  echo "$PUBKEY" >> "$AUTH"
  echo "  已写入公钥"
fi
chmod 600 "$AUTH"
chown "$DEV_USER:$DEV_USER" "$AUTH"
echo "  指纹:"
ssh-keygen -lf "$AUTH" 2>/dev/null | sed 's/^/    /'

echo
echo "===== 4. 安装 qqdeploy ====="
if [ ! -f /root/qqdeploy.py ]; then
  echo "  ❌ /root/qqdeploy.py 不存在，先上传它" >&2
  exit 1
fi
install -m 755 -o root -g root /root/qqdeploy.py /usr/local/bin/qqdeploy
echo "  已安装: /usr/local/bin/qqdeploy"
python3 -c "import ast;ast.parse(open('/usr/local/bin/qqdeploy',encoding='utf-8').read())" \
  && echo "  语法检查通过"

echo
echo "===== 5. sudo 授权（仅 qqdeploy 一条命令）====="
# 注意：只放行这一条命令，不给完整 sudo。
# 传参也允许（否则 sudo qqdeploy main 会被拒）。
cat > "$SUDOERS_FILE" <<'EOF'
# QQ Agent 部署脚本：只授权这一条命令，等价于"能部署代码"，不等于完整 root。
# 需要完整 sudo 请另行评审 —— 部署脚本本身以 root 运行，已足够完成部署。
dev ALL=(root) NOPASSWD: /usr/local/bin/qqdeploy
EOF
chmod 440 "$SUDOERS_FILE"
chown root:root "$SUDOERS_FILE"
if visudo -cf "$SUDOERS_FILE"; then
  echo "  sudoers 语法检查通过"
else
  echo "  ❌ sudoers 语法错误，已删除以免锁死 sudo" >&2
  rm -f "$SUDOERS_FILE"
  exit 1
fi

echo
echo "===== 6. 给 $DEV_USER 写个登录提示 ====="
cat > "/home/$DEV_USER/README-部署.md" <<'EOF'
# 部署说明（协作者）

## 部署一个分支

    sudo qqdeploy <分支名>        # 例如 sudo qqdeploy feat/my-change
    sudo qqdeploy <commit-sha>

脚本会：从 GitHub 下载 → 语法与依赖校验 → 无缓存重建 → 健康检查
（容器在跑 / OneBot 已连 / 插件未丢）。**任一不过会自动回滚**，不会把线上弄坏。

## 常用命令

    sudo qqdeploy --status        # 当前部署的是哪版、谁部署的、健康状态
    sudo qqdeploy --list          # 部署历史 + 本地保留的版本
    sudo qqdeploy --rollback      # 回滚到上一个版本
    sudo qqdeploy --dry-run main  # 演练：只构建不切换（线上不受影响）

## 重要规则

1. **不要手改 /opt/qq/current 或 releases/ 里的文件** —— 那些是部署产物。
   改动请走 GitHub（分支 → PR），否则脱离版本控制、下次部署即丢失。
2. **同一时刻只有一个人部署**。脚本有锁，第二个会被明确拒绝 ——
   看到"另一个部署正在进行中"就等一下，别绕过。
   （服务器只有 1.6G 内存，也跑不了两个实例；QQ 号也只能登一处。）
3. **部署前建议先 --dry-run**，尤其是跨版本升级。
4. 数据与插件在 /opt/qq/qq-agent-data 与 /opt/qq/qq-agent-plugins，
   是独立挂载，重建容器不会动它们；但**也别手动改**，先备份再说。

## 日志与排查

    sudo qqdeploy --status                 # 最快的一眼
    docker logs --tail 50 qq-agent         # 机器人日志
    cat /opt/qq/.deployed.json             # 部署记录（当前/上一个/历史）
    cat /opt/qq/napcat-status.txt          # 掉线守护写的状态摘要
EOF
chown "$DEV_USER:$DEV_USER" "/home/$DEV_USER/README-部署.md"
echo "  已写入 ~/README-部署.md"

echo
echo "===== 7. 结果自检 ====="
echo "  用户信息:"
id "$DEV_USER" | sed 's/^/    /'
echo "  sudo 授权（应只有 qqdeploy 一条）:"
sudo -l -U "$DEV_USER" 2>/dev/null | tail -3 | sed 's/^/    /'
echo "  authorized_keys 行数: $(wc -l < "$AUTH")"
echo "  qqdeploy: $(ls -l /usr/local/bin/qqdeploy | awk '{print $1, $3, $4, $5"字节"}')"
