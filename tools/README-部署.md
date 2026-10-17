# 部署说明（协作者）

服务器：`<服务器IP>`　端口 `22`
登录：`ssh -i ~/.ssh/id_ed25519 dev@<服务器IP>`（首次会问 host key，输 `yes`；不需要密码）

> 你的账号是 `dev`（普通用户）。`ZHEhao423` 只是 SSH 密钥的备注名，不是登录用户名。
> 你**没有完整 sudo**，只能执行下面的 `qqdeploy`。

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

## 目录结构（看懂这个就不容易踩坑）

    /opt/qq/                         部署根目录
      current -> releases/<sha>/     ★ 软链，指向当前部署的版本
      releases/<sha>/                每次部署的独立目录（保留最近 8 个）
      qq-agent-data/                 ★ 数据：配置/存档/记忆/会话（独立挂载，重建不丢）
      qq-agent-plugins/              ★ 插件本体（同上）
      napcat/                        NapCat 配置与 QQ 登录态
      .deployed.json                 部署记录
      .env                           凭据（WEBUI_TOKEN 等），不入版本库
      docker-compose.napcat.yml      ★ NapCat 的唯一入口（见下）
      docker-compose.yml.legacy      ⚠️ 已退役，别看别用
      qq-agent/                      ⚠️ 旧源码目录，已废弃，不参与部署

**现在跑的是哪版**：`sudo qqdeploy --status`，或 `ls -l /opt/qq/current`。

## 重要规则

### 1. 同一时刻只有一个人部署

脚本有 `flock` 独占锁，第二个会被明确拒绝 ——
看到"另一个部署正在进行中"就等一下，**别绕过**。
（服务器只有 1.6G 内存，跑不了两个实例；同一个 QQ 号也只能登一处。）

### 2. 不要手改 `/opt/qq/current` 或 `releases/` 里的文件

那些是部署产物。手改脱离版本控制，下次部署即丢失。改动请走 GitHub 分支 → PR。

### 3. ⚠️ 旧 compose 文件已退役，别用

    ❌ docker compose -f /opt/qq/docker-compose.yml up -d

这条命令会用 `/opt/qq/qq-agent`（10-13 的旧源码）覆盖线上，且**没有明显报错**。
旧的 `docker-compose.yml` / `docker-compose.platform.yml` 已改名为 `*.legacy`，
直接跑会报 "compose file is invalid"，这是刻意的保护。

### 4. 重启 NapCat 只用这个方式

    docker restart napcat        # 最简单，只影响 napcat

    # 或走 compose（改配置后用 up -d）
    cd /opt/qq
    docker compose --env-file .env -f docker-compose.napcat.yml up -d
    docker compose --env-file .env -f docker-compose.napcat.yml logs -f

> ❌ **绝对不要** `docker compose -f docker-compose.napcat.yml down`
> 它与 qq-agent 共用项目名 `qq`，`down` 会把 qq-agent 一起删掉。

### 5. 数据与插件目录别手改

`/opt/qq/qq-agent-data` 与 `/opt/qq/qq-agent-plugins` 是独立挂载，重建容器不会动它们。
但别直接编辑里面的东西 —— 先备份再说。

## 日志与排查

    sudo qqdeploy --status                 # 最快的一眼（含健康检查）
    docker logs --tail 50 qq-agent         # 机器人日志
    docker logs --tail 50 napcat           # 协议端日志
    cat /opt/qq/.deployed.json             # 部署记录（当前/上一个/历史）
    cat /opt/qq/napcat-status.txt          # 掉线守护写的状态摘要
    journalctl -t napcat-guard -n 20       # 掉线告警历史

## 出了事怎么办

    sudo qqdeploy --rollback               # 回到上一个版本（约 4 分钟）

部署脚本对"构建失败"和"健康检查不过"都会**自动回滚**，所以正常情况下你不需要手动操作。
`--rollback` 是给"部署成功但行为不对"这种情况用的。

如果 NapCat 被踢下线（腾讯侧问题，脚本无法绕过）：

    cat /opt/qq/napcat-status.txt          # 会显示 KICKED
    # 然后开隧道扫码：http://127.0.0.1:6098/ （实时二维码页）
    ssh -N -L 6098:127.0.0.1:6098 dev@<服务器IP>
