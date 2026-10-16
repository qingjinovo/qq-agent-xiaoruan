#!/usr/bin/env python3
"""
export-config-template.py —— 把（服务器上）真实运行的配置导出成可入库的模板

用途：让协作的人知道"这个机器人到底该配哪些项"。纯靠文档描述容易漏，
直接给一份**脱敏后的真实配置**最省事。

脱敏规则（**新增敏感字段时必须同步这里**）：
  · 一切密钥类：换成 "<在此填你的 ...>"
      api.key / providers.*.apiKey /*.token / *.cookie / dshProviderKeys
  · QQ 号：selfId、selfNickname 换成占位
  · 群号：白名单 / 分群档位 / 分群概率里的群号换成 111111111 / 222222222 之类的占位
  · 本地绝对路径、用户名：换成占位

用法（在服务器上跑，输出到标准输出，重定向到文件带回本机）：
    python3 export-config-template.py /opt/qq/qq-agent-data/config.json > config.example.json
"""

import json
import re
import sys

# ── 逐字段脱敏 ──
SECRET_PLACEHOLDER = {
    "key": "<在此填你的模型 API Key>",
}
PLACEHOLDER_TEXT = "<在此填你的值>"

# 键名命中这些词 → 一律当密文处理（防止新增密钥字段时漏改）
SECRET_NAME_RE = re.compile(r"(api_?key|apikey|token|secret|password|passwd|cookie|credential)", re.I)

# ★ 第二层保险：**值形态**匹配。
# 只靠键名一定会漏 —— 实测漏过 `dshProviderKeys: { "<providerId>": "sk-..." }`：
# 那是个映射表，键名是 provider id，值的键名里没有 key 这个词。
# 所以凡是"长得像凭据"的值，无论出现在哪个键下都替换掉。
CREDENTIAL_VALUE_RE = re.compile(
    r"""^(?:
        sk-[A-Za-z0-9_\-]{16,}          # OpenAI / DeepSeek 风格
      | ghp_[A-Za-z0-9]{20,}            # GitHub PAT
      | gho_[A-Za-z0-9]{20,}
      | xox[baprs]-[A-Za-z0-9\-]{10,}   # Slack
      | AIza[A-Za-z0-9_\-]{30,}         # Google
      | [A-Fa-f0-9]{32,}                # 长十六进制串（token / 密钥）
      | [A-Za-z0-9_\-]{40,}             # 超长随机串
    )$""", re.X)

# 群号替换表：按出现顺序给稳定占位，便于对照
GROUP_MAP = {}
GROUP_SEQ = [111111111, 222222222, 333333333, 444444444, 555555555]

# 自检白名单：这些值允许原样保留（公众信息，不敏感）
SELF_CHECK_ALLOW = re.compile(
    r"^(https?://|ws://|wss://|<.*>|deepseek|openai|anthropic|bing|google)")


def looks_like_credential(s):
    """值形态是否像凭据。"""
    if not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 16:
        return False
    if SELF_CHECK_ALLOW.match(s):
        return False
    return bool(CREDENTIAL_VALUE_RE.match(s))


def map_group(gid):
    gid = str(gid)
    if gid not in GROUP_MAP:
        n = len(GROUP_MAP)
        GROUP_MAP[gid] = str(GROUP_SEQ[n] if n < len(GROUP_SEQ) else 999999999 + n)
    return GROUP_MAP[gid]


def sanitize(obj, path=""):
    """递归脱敏。path 用于判断当前在哪个字段下。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            p = "%s.%s" % (path, k) if path else k
            key_l = str(k).lower()

            # 1) 键名像密钥 → 直接占位（字符串值才处理）
            if isinstance(v, str) and v and SECRET_NAME_RE.search(key_l):
                # 已知的 model api key 给更明确的提示
                if "api" in key_l and "key" in key_l:
                    out[k] = SECRET_PLACEHOLDER["key"]
                else:
                    out[k] = PLACEHOLDER_TEXT
                continue

            # 1b) ★ 值形态像凭据 → 无论键名叫什么，一律占位
            #     （这是补 dshProviderKeys 那种"映射表"漏网的关键一层）
            if looks_like_credential(v):
                out[k] = PLACEHOLDER_TEXT
                continue

            # 1c) 纯映射表（键是 id、值是凭据）也要处理：递归进去后由 1b 兜住，
            #     但这里额外确保 map 的值都被检查过（sanitize 递归会走到）

            # 2) 群号类字段
            if k in ("groups", "groupSliderPos", "groupPeakPos", "chatWindowProbByGroup") and \
                    isinstance(v, (list, dict)):
                if isinstance(v, list):
                    out[k] = [map_group(x) for x in v]
                else:
                    out[k] = {map_group(kk): vv for kk, vv in v.items()}
                continue

            # 3) 机器人自身身份
            if k in ("selfId", "selfNickname", "botName") and isinstance(v, str) and v:
                out[k] = {"selfId": "<机器人QQ号>",
                          "selfNickname": "<机器人群昵称>",
                          "botName": "<机器人名字>"}[k]
                continue

            # 3b) 人格正文：不入模板。
            #     它由 persona-*.md 单独维护，放进配置模板会变成两份副本，
            #     以后改一处忘一处就不同步了。这里只留占位与指路。
            if k == "roleText" and isinstance(v, str) and v:
                out[k] = ""
                continue

            # 3c) 屏蔽名单：里面的号码属于第三方（广告号等），不该进公开仓库。
            #     blocklist 是按会话记的映射，globalBlocklist 是全局列表。
            if k == "globalBlocklist" and isinstance(v, list):
                out[k] = []
                continue
            if k == "blocklist" and isinstance(v, dict):
                out[k] = {}
                continue

            # 4) 本地绝对路径
            if isinstance(v, str) and re.match(r"^(/|[A-Za-z]:\\)", v):
                out[k] = PLACEHOLDER_TEXT
                continue

            out[k] = sanitize(v, p)
        return out

    if isinstance(obj, list):
        return [sanitize(x, path) for x in obj]

    return obj


def find_credential_leaks(obj, path=""):
    """在脱敏结果里找"还像凭据的值"，返回 [(路径, 值前缀)]。用于导出前自检。"""
    leaks = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (path, k) if path else str(k)
            leaks.extend(find_credential_leaks(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaks.extend(find_credential_leaks(v, "%s[%d]" % (path, i)))
    elif isinstance(obj, str) and looks_like_credential(obj):
        leaks.append((path, obj[:12] + "…"))
    return leaks


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "/opt/qq/qq-agent-data/config.json"
    with open(src, encoding="utf-8") as fh:
        cfg = json.load(fh)

    # 覆盖会让人误用的字段
    cfg = sanitize(cfg)

    # ── 导出前自检：宁可不出文件，也不把凭据写出去 ──
    leaks = find_credential_leaks(cfg)
    # 已知的安全占位符不算泄漏
    leaks = [(p, v) for p, v in leaks if not v.startswith("<")]
    if leaks:
        sys.stderr.write("❌ 自检失败：脱敏结果里仍有疑似凭据，已中止导出。\n")
        for p, v in leaks[:20]:
            sys.stderr.write("   %s = %s\n" % (p, v))
        sys.stderr.write(
            "   处理：把这些字段加进 SECRET_NAME_RE，或在 looks_like_credential() "
            "的形态规则里覆盖。修好后重新运行。\n")
        return 2

    # 白名单/分群那些占位群号，在文件里给一句说明（JSON 不支持注释，
    # 所以把说明放进一个顶层特殊键，导入前删掉即可）
    cfg["_说明"] = (
        "本文件是脱敏后的配置模板，来自实际运行的实例。"
        "所有密钥、QQ号、群号、本地路径都已替换成占位值，导入前请自行填写。"
        "带下划线前缀的键（如本键）是说明，不是真实配置项，可删除。"
    )
    cfg["_需要你自己填的"] = [
        "api.apiKey / providers[].apiKey / dshProviderKeys.*  模型 API Key",
        "snowluma.accessToken / httpAccessToken                 OneBot 访问令牌（需与协议端一致）",
        "persona.botName / persona.selfNickname                 机器人名字与群昵称",
        "persona.roleText                                       角色卡正文 —— 从 persona-*.md 复制，本模板故意留空避免两处副本",
        "allow.groups                                           允许响应的群号（其余群一律不理）",
        "store.chatWindowProbByGroup                            分群接话概率的群号",
        "server.token                                           控制台访问口令（留空=不校验，仅限本机访问时可用）",
    ]
    cfg["_注意"] = (
        "allow.groups 为空且 allowAllWhenEmpty=false 时，机器人不会响应任何群。"
        "globalBlocklist / blocklist 已清空 —— 原值是第三方号码，不入仓库。"
    )

    # 群号对照，方便阅读时对应
    if GROUP_MAP:
        cfg["_群号对照"] = {v: "原群号已脱敏" for v in GROUP_MAP.values()}

    json.dump(cfg, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
