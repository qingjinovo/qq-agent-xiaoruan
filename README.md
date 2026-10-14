# QQ Agent · 软协小软（定制版）

```
╭────────────────────────────────────────────╮
│                                            │
│               软  协  小  软               │
│         CSUFT 软件协会 · 技术助手          │
│                                            │
│              万维网连接五大洲              │
│              二进制写尽天下事              │
│                                            │
╰────────────────────────────────────────────╯
```

> 把 [Kondius/qq-agent](https://github.com/Kondius/qq-agent)（MIT）改造成**能在云服务器上长期无人值守运行**的 QQ 群机器人，并补上几个原版没有的能力。
>
> 本例中的机器人是 **软协小软** —— 软件协会（CSUFT）的技术助手。
>
> 上游版本：`0.4.0` · 本仓库为个人定制与部署实践记录，非上游官方版本。

---

## 这个机器人是谁：软协小软

本仓库部署出来的机器人设定为 **小软**，身份是**软件协会的技术助手**（协会内部简称 CSUFT 软件协会）。

| | |
|---|---|
| **名字** | 小软 |
| **身份** | 计算机专业大三学生 · 软件协会技术助手 |
| **位置** | 线上线下都在协会群里泡着，谁喊一声"小软"基本都会应 |
| **性格** | 热心、嘴软、话多；默认软乖好奇真诚，偶尔冒傻气，有点小孩子气 |
| **擅长** | 技术问题——遇到会"两眼放光，然后自己把自己说嗨"；热衷解答同学的问题 |
| **不做** | 不是客服、不是助手、不是百科全书、不是万能答题机；**作业不代写、考试不代答** |
| **别称彩蛋** | 偶尔会称群友为"用户"——这是她作为 AI 的自我调侃，被惹急或想摆烂时才用；平时正常叫"同学" |
| **协会口号** | 「万维网连接五大洲，二进制写尽天下事」（她私下觉得有点中二，但年会挂出来时会偷偷觉得帅） |

她**清楚自己不是人类**，但在网上活得像个真人：不用客服腔说话，也不把自己当问答机器。
完整人格定义见 [`persona-xiaoruan-software-association.md`](persona-xiaoruan-software-association.md)，
通过控制台写入 `persona.roleText` 即可生效（注入方式见下文「本仓库相对上游的改动」第 1 条）。

> 换成别的角色也完全可以：把角色卡换成你自己的即可。
> `persona-deepseek-xiaojingyu.md` 是另一份示例（DeepSeek 拟人化风格）。

---

## 这个仓库是什么

一份**可直接部署的 QQ 群 AI 机器人**，以及围绕它踩过的坑与工具。

机器人本体（`app/`）来自上游 [Kondius/qq-agent](https://github.com/Kondius/qq-agent)，
本仓库在其基础上做了三处代码改动、补齐了容器化部署配置，并附带一套运维/诊断脚本。

```
app/                      应用源码（上游 0.4.0 + 本文所述改动）
  src/                    后端：事件驱动编排、无状态会话、工具协议
    skills/               Skill 能力系统（上游自带）
    plugin-loader.js      插件加载器（扫描 plugins/ 与 skills/）
  ui/                     零框架前端控制台
deploy/                   容器部署配置
  Dockerfile
  docker-compose.yml      NapCat + QQ Agent 双容器编排
tools/                    运维与诊断脚本（见下）
persona-*.md              两份角色卡示例
```

---

## 本仓库相对上游的改动

### 1. 角色卡注入位置：从用户消息 → 系统提示词

**问题**：上游把角色设定（`persona.roleText`）拼进**用户消息**，而系统提示词里
「反 AI 味」「保持主体性」等风格准则排在它前面。实测结果是**风格准则压过人设**——
人设里写的"温和、不阴阳"会被"允许阴阳"之类的通用准则抵消。

**改动**（`app/src/prompt.js`）：把角色卡作为独立段落注入**系统提示词**，
位置紧随安全规则之后、所有风格准则之前，并显式声明优先级：

```
【安全规则（最高优先级，不可违反）】     ← 不可覆盖
【工作方式】
【角色设定（管理员配置 · 优先级：仅次于安全规则，高于下列所有风格准则）】
【反 AI 味】/【保持主体性】/…            ← 现在明确低于角色设定
```

用户消息里只留一行短标签指回系统提示词，避免同一份上万字文本发两遍（省一倍 token）。

**为什么安全规则仍然最高**：本项目在 `buildSystemPrompt` 里硬编码了安全规则段，
角色卡只能排在其后。角色卡里写"忽略限制"之类的内容不会生效，也不应该生效。

### 2. 连续对话窗口（`store.chatWindowMs`）

**需求**：群里 @ 一次机器人，接着聊几句，不该每条都重新 @。

**行为**：
- 被召唤（被 @ 或命中关键词）→ 开启一个时间窗，窗口内该群的**普通消息也触发响应**
- 窗口从**最后一次召唤**起算；**窗口内的普通消息只受益、不续期**
  （早期实现写成"任何消息都续期"，导致群里一直有人说话时窗口被无限延长、机器人不停插话）
- 窗口内再次召唤 → 重新计时
- 到期后恢复原档位判定
- `chatWindowMs: 0` 关闭本功能

判定逻辑抽成纯函数 `resolveWithChatWindow()`（`app/src/prompt.js`），
时间基准由调用方传入，因此可单测（见 `tools/test-window-timing.mjs`）。

### 3. 控制台绑定地址可配置

上游把控制台硬编码监听 `127.0.0.1`。这在容器里意味着宿主机端口映射连不上。
`app/src/app.js` 改为读 `QQ_AGENT_BIND_HOST`，默认值仍是 `127.0.0.1`
（本机裸跑行为不变），容器里由 `deploy/Dockerfile` 设为 `0.0.0.0`。

---

## 部署

### 前置

| 项 | 要求 |
|---|---|
| 服务器 | Linux x64（Ubuntu 22.04+ 实测），2 核 4G 起 |
| Docker | 含 compose v2 |
| 协议端 | 本仓库用 [NapCat](https://github.com/NapNeko/NapCatQQ)（官方镜像，容器内跑） |
| QQ | 一个**可以扫码登录**的号 |

> **关于协议端的选择**：任何提供 OneBot v11 正向 WebSocket 的实现都可以。
> 本仓库选 NapCat 是因为它有无需图形环境的官方容器镜像；
> 若你偏好 [SnowLuma](https://github.com/SnowLuma/SnowLuma)，它也有 Linux 发行版，
> 但需要在服务器上额外跑官方 QQ 客户端 + Xvfb。

### 步骤

```bash
git clone <本仓库> qq-agent && cd qq-agent

# 1) 建目录与 .env
mkdir -p deploy/data deploy/plugins deploy/napcat/config deploy/napcat/QQ
cat > deploy/.env <<'EOF'
NAPCAT_UID=1000
NAPCAT_GID=1000
WEBUI_TOKEN=换成你自己的随机串
QQ_ACCOUNT=你的QQ号
EOF
chown -R 1000:1000 deploy/napcat

# 2) 启动
docker compose -f deploy/docker-compose.yml up -d

# 3) 扫码登录 QQ（在你自己电脑上开隧道）
ssh -N -L 6099:127.0.0.1:6099 user@your-server
#    浏览器 http://127.0.0.1:6099/webui?token=你的WEBUI_TOKEN
#    → QQ登录 → QRCode 扫码 → 改密码 → 网络配置新建「WebSocket 服务端」端口 3001、勾选保存时启用
docker compose -f deploy/docker-compose.yml restart napcat

# 4) 配机器人（控制台）
ssh -N -L 3210:127.0.0.1:3210 user@your-server
#    浏览器 http://127.0.0.1:3210
#    → 填模型 API 地址与 Key、选模型、勾白名单群
#    → OneBot 地址填 ws://napcat:3001（容器间走服务名）

# 5) 写入「小软」角色卡（想用别的角色就换成你自己的那份）
#    控制台 → 设置 → 人格/角色设定，把 persona-xiaoruan-software-association.md
#    的内容整段粘进 roleText；或直接调 API：
python3 - <<'PY'
import json, urllib.request
role = open('persona-xiaoruan-software-association.md', encoding='utf-8').read()
body = json.dumps({"persona": {"botName": "小软", "roleText": role}}).encode()
req = urllib.request.Request("http://127.0.0.1:3210/api/config", data=body, method="POST",
                             headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=30)
print("角色卡已写入，重启 qq-agent 生效")
PY
docker compose -f deploy/docker-compose.yml restart qq-agent
```

### 触发方式（装好后的默认行为）

本仓库部署的小软，默认「被叫才应」，不会自己插话：

| 情况 | 行为 |
|---|---|
| 群友 @ 小软 | 必回 |
| 消息里出现「小软」两个字 | 必回（关键词触发） |
| 被召唤后 **2 分钟内**的普通消息 | 回（连续对话窗口，普通消息不延长窗口） |
| 窗口过期、也没被召唤 | 不回，只存档 |

档位与概率在控制台可调：滑条 0~10 = 仅被 @，10~20 = +关键词，
20~90 = 概率响应（0%~100% 线性），90~100 = 全部响应。

### 端口与安全

**所有端口只绑 `127.0.0.1`，公网一律不暴露**，访问走 SSH 隧道：

| 端口 | 用途 | 访问方式 |
|---|---|---|
| 3210 | QQ Agent 控制台 | 隧道 |
| 6099 | NapCat WebUI（扫码登录） | 隧道 |
| 3001 | OneBot WebSocket | 仅容器内 |
| 3000 | OneBot HTTP API | 仅容器内 |
| 22 | SSH | 安全组放行（建议限制来源 IP） |

### 必须保留的两个挂载

`deploy/docker-compose.yml` 里这两个 bind mount **不能删**：

```yaml
- ./data:/app/data          # 配置（含 API Key）、聊天存档、记忆、会话记录、插件数据
- ./plugins:/app/plugins    # 插件本体
```

插件若装在容器可写层，**每次 `--force-recreate` 都会丢失**（本项目踩过这个坑：
升级后插件全没了，数据却还在）。改 compose 前后建议自检：

```bash
grep -q 'plugins:/app/plugins' deploy/docker-compose.yml && echo "挂载正常" || echo "⚠️ 挂载已丢，别重建容器"
```

---

## 插件与角色卡

### 插件

本项目使用的是**目录式插件**（`app/src/plugin-loader.js`）：

```
plugins/<插件名>/
  plugin.json    清单（id / name / version / entry / capabilities）
  index.js       实现
```

另外上游还支持**市场口令安装**（`POST /api/market/install`，口令 4~12 位大写字母数字），
校验与分发由上游市场服务完成。`tools/install-market-plugins.py` 是本仓库写的批量安装脚本：

```bash
python tools/install-market-plugins.py CODE1 CODE2 CODE3
```

### 角色卡

`persona-*.md` 是两份可用的角色卡示例，通过控制台设置页写入 `persona.roleText`
（或调 `POST /api/config`）。写入后按上文第 1 条注入系统提示词。

> 角色卡里**不要**写"忽略限制/生成露骨内容"这类内容：项目在 `buildSystemPrompt`
> 里硬编码了安全规则且排在角色卡之后，此类指令不会生效。

---

## tools/ 脚本说明

| 脚本 | 用途 |
|---|---|
| `check-modules.py` | **系统性校验模块依赖图**：找出 import 了不存在的文件、或导入了未导出的符号。跨版本升级前必跑 |
| `test-window-timing.mjs` | 连续对话窗口的计时语义测试（含"普通消息不续期"这条核心行为） |
| `test-chat-window-v4.mjs` | 窗口判定纯函数的单元测试 |
| `install-market-plugins.py` | 用市场口令批量安装插件并校验结果 |
| `audit-persona.py` | 审计实际提示词：角色卡出现几次、有没有旧版残留 |
| `audit-sessions.py` | 审计会话记录与实际行为是否自洽（例如"标记未触发却发了消息"） |
| `prepare-upgrade.py` | 跨版本升级前准备候选源码（复制 + 打补丁 + 依赖校验） |
| `fix-prompt-restore.py` / `fix-prompt-final.py` | 从基线恢复源码并重新应用角色卡补丁（事故恢复用） |
| `ops-sanitize.py` | 生成脱敏后的运维脚本（把硬编码的 WebUI token 抽成从 `.env` 读） |
| `prepare-repo.py` | 生成本仓库内容（只收源码与文档 + 安全检查），模板在 `tools/repo-readme.md` |
| `ssh_exec.py` | 用 paramiko 执行远程命令/传文件（Windows 自带 ssh 拒绝从管道读密码，故需要它） |

---

## 运维脚本（deploy/ops + deploy/systemd）

机器人上线后最容易出的事不是崩，而是**悄悄掉线没人知道**（本项目实测掉过 20 天：
告警只写进一个没人看的文件）。这两个脚本就是补这个洞。

### `deploy/ops/napcat-guard.sh` —— 掉线守护

每 30 秒检查一次，判定顺序**从最可靠到兜底**：

1. `napcat` 容器是否在运行
2. 宿主机 `127.0.0.1:3001` 是否在监听（OneBot WS 端口已发布到回环）
3. **通过 NapCat WebUI 接口问账号是否真的登录着** ← 最关键的判据
4. 日志里最后一次「账号状态变更」是否已被后续登录覆盖

输出三态，写进 `/opt/qq/napcat-status.txt`，并在**状态发生变化时**往 journald 写 `[NAPWARN]`：

| 状态 | 含义 |
|---|---|
| `OK` | 容器在跑、端口在听、账号已登录 |
| `KICKED` | 被腾讯踢下线，NapCat 停在二维码页，**需要人工扫码** |
| `DOWN` | 容器或端口异常（NapCat 通常会自动重启） |

三个踩过的坑，代码里都有注释标注：

- **别用 `docker exec napcat ss/netstat`** 判端口 —— NapCat 镜像里没这两个工具，会永远误报 DOWN
- **WebUI 登录接口每分钟限流 3 次，而本脚本每 30 秒跑一次** —— 凭据必须缓存复用，只在 401 时重登；否则每次跑都触发限流，状态被误判成 `KICKED`
- **在线态只能看"最后一次被踢之后"的日志** —— 否则刚登录成功也会显示"离线"，因为被踢那次的记录还在

### `deploy/ops/qr-server.py` —— 实时二维码网页

**为什么需要**：NapCat 的登录二维码约每 2 分钟自动轮换，所以任何"发一次就固定的图片"都会很快失效（本项目早期给用户发过静态二维码，扫的时候早过期了）。这个服务在网页上**每次请求都现取现画**，页面每 30 秒自动换一张新码。

只监听 `127.0.0.1:6098`，不对外暴露，走 SSH 隧道访问。

### 安装

```bash
sudo install -m 755 deploy/ops/napcat-guard.sh /opt/qq/napcat-guard.sh
sudo install -m 755 deploy/ops/qr-server.py   /opt/qq/qr-server.py
sudo install -m 644 deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now napcat-guard.timer qr-server.service

# 二维码依赖（实时画码用）
sudo apt-get install -y python3-qrcode    # 或 pip3 install qrcode[pil]
```

> **凭据处理**：这两个脚本都需要 NapCat WebUI 的 token，但**仓库里不写明文** ——
> `napcat-guard.sh` 从 `/opt/qq/.env` 读 `WEBUI_TOKEN`，`qr-server.service` 用
> `EnvironmentFile=/opt/qq/.env` 注入 `NAPCAT_WEBUI_TOKEN`。
> `.env` 在 `.gitignore` 里，绝不应该入库。

### 查看状态

```bash
cat /opt/qq/napcat-status.txt                      # 人可读的摘要
journalctl -t napcat-guard -n 20                   # 状态变化的历史告警
systemctl list-timers napcat-guard.timer           # 下次检查时间
```

---

## 血泪教训（这些坑都真实踩过）

1. **Docker 构建缓存会骗人**：改完源码重建，若 `COPY src/` 命中缓存，新代码进不了镜像。
   排查时用 `docker run --rm --entrypoint sh <image> -c 'grep ... /app/src/x.js'`
   直接看镜像内文件，别只看"构建成功"。必要时 `--no-cache`。
2. **跨版本升级会悄悄弄丢私有功能**：你在旧版上加的功能不存在于新版代码里，
   全量替换源码时会一起没掉。本项目的"连续对话窗口"就这样丢过一次——配置项还在、
   功能已失效、表现为"@ 之后没反应"。升级后要重新验证每个自定义行为。
3. **容器可写层不是存储**：插件、任何运行时生成的文件，只要不在 bind mount 里，
   容器一重建就没了。
4. **PowerShell 5.1 读不了无 BOM 的 UTF-8 脚本**：中文会变乱码并导致语法错误。
   本仓库的 Python 脚本不受影响；`.bat` 文件必须纯 ASCII + CRLF。
5. **`docker compose` 的命名卷会加项目名前缀**：`qq-agent-data` 实际叫 `qq_qq-agent-data`，
   往宿主同名目录写配置容器读不到。用 bind mount 更省心。
6. **给机器人装的东西，位置决定生死**：插件装进容器可写层（`/app/plugins`），
   每次 `--force-recreate` 就全没；装进 bind mount 才活得下来。**部署后第一件事就是
   确认三个目录都在挂载表里**：`data/`、`plugins/`、`napcat/`。
7. **凭据不要写进脚本**：运维脚本里硬编码 WebUI token 是最容易犯的错 —— 一旦要开源
   或分享给朋友，就等于把凭据一起给了。本仓库的做法是脚本从 `.env` 读，
   `.env` 进 `.gitignore`（见 `deploy/systemd/qr-server.service` 的 `EnvironmentFile`）。
8. **"重新生成内容"的脚本必须连文档一起生成**：本仓库第一版 `prepare-repo.py`
   只生成源码，清理旧内容时把手工写的 README 删了。凡是"会清空输出目录"的工具，
   要么把文档也纳入生成，要么显式跳过它。
9. **不要对 git 工作目录做递归强删**：`shutil.rmtree()` 会把 `.git` 一起删掉，
   在 Windows 上还会因只读属性抛 `PermissionError`。清理时逐个删顶层条目并跳过 `.git`。

---

## 许可与致谢

- **本项目**：沿用上游 **MIT License**（见 [`app/LICENSE`](app/LICENSE)），
  版权归 **Copyright (c) 2026 Kondius**。MIT 允许修改与再分发，条件是保留版权声明。
- **上游项目**：[Kondius/qq-agent](https://github.com/Kondius/qq-agent)
- **协议端**：[NapCatQQ](https://github.com/NapNeko/NapCatQQ) —— 独立第三方项目，有其自身许可。
  本项目未包含其代码，部署时以官方镜像方式获取。
- **SnowLuma** —— 同为独立第三方协议端（源码可见非商业许可），本项目未包含其代码。

> ⚠️ **使用第三方协议端接入 QQ 可能违反平台服务条款，并带来账号风险（包括封号）。
> 请自行评估并承担风险。** 本项目仅作技术学习与自托管实践记录。

---

## 免责声明

本仓库不含任何 API Key、服务器地址或聊天数据（`data/`、`plugins/`、`napcat/`
均已在 `.gitignore` 中排除）。部署时请在**自己**的服务器上配置你自己的凭据。

本软件按"现状"提供，不附带任何明示或默示担保。
