#!/usr/bin/env python3
"""
fix-prompt-restore.py —— 从 09-21 基线包恢复源码，并重新应用"角色卡进系统提示词"改动

背景：
  服务器上是旧版应用（26 个源文件，无 skills 目录）。
  我用工作区那份新版应用的 prompt.js/config.js/orchestrator.js 覆盖了服务器，
  而新版 prompt.js 依赖 ./skills/manager.js（服务器没有）→ 容器启动即崩。
  基线包 /root/qq-agent-docker.tar.gz 就是 09-21 验证可用的那份源码。

本脚本做三件事（幂等，可重复跑）：
  1. 从 /tmp/pkg/.../qq-agent/src 恢复 prompt.js / orchestrator.js / config.js
  2. 给基线 prompt.js 重新打上"角色卡移入系统提示词"补丁
  3. 给基线 config.js 补上 chatWindowMs 默认值
     （orchestrator.js 里的连续对话窗口功能保留服务器现版本：它基于同一基线，
       只多了窗口逻辑，与基线其余部分兼容）
"""

import io
import os
import re
import shutil
import sys

PKG_SRC = "/tmp/pkg/qq-agent-docker/qq-agent/src"
LIVE_SRC = "/opt/qq/qq-agent/src"

FILES_FROM_PKG = ["prompt.js", "config.js"]


def read(p):
    with io.open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def write(p, text):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def backup(p):
    if os.path.exists(p):
        dst = p + ".prebaseline"
        if not os.path.exists(dst):
            shutil.copy2(p, dst)
            print("   备份: %s" % os.path.basename(dst))


def main():
    if not os.path.isdir(PKG_SRC):
        print("找不到基线包源码目录: %s" % PKG_SRC)
        return 1

    print("=== 1. 从基线恢复文件 ===")
    for name in FILES_FROM_PKG:
        src = os.path.join(PKG_SRC, name)
        dst = os.path.join(LIVE_SRC, name)
        backup(dst)
        shutil.copy2(src, dst)
        print("   恢复 %-18s <- 基线 (%d 字节)" % (name, os.path.getsize(dst)))

    # ── 2. 给基线 prompt.js 打"角色卡进系统提示词"补丁 ──
    print("\n=== 2. 给基线 prompt.js 打角色卡补丁 ===")
    p = os.path.join(LIVE_SRC, "prompt.js")
    t = read(p)

    if "优先级：仅次于安全规则" in t:
        print("   已打过补丁，跳过")
    else:
        anchor = """  const parts = [
    `你是「${cfg.botName}」，一个混在 QQ 群里的普通群友（不是助手、不是客服）。你的所有行为都通过工具完成，发言必须像真人。`,
    '',
    securityRules(),
    '',
    toolProtocol(),
    '',
    antiAiFlavor(),"""
        if anchor not in t:
            print("   ❌ 找不到锚点，prompt.js 结构与预期不符，中止")
            return 2

        replacement = """  const parts = [
    `你是「${cfg.botName}」，一个混在 QQ 群里的普通群友（不是助手、不是客服）。你的所有行为都通过工具完成，发言必须像真人。`,
    '',
    securityRules(),
    '',
    toolProtocol(),
    ''
  ];

  // ── 角色卡 ──────────────────────────────────────────────────────────────
  // 放在这里（安全规则之后、所有风格准则之前）而不是用户消息里，理由：
  //   1) 层级：角色卡是"你是谁"，风格准则（反AI味/主体性/说话节奏）是"怎么说话"。
  //      后者原先排在系统提示词里，比用户消息中的角色设定权重更高，导致
  //      "温和/傲娇"这类人设要求被"允许阴阳/拒绝有求必应"之类的通用准则压掉。
  //   2) 缓存：角色卡是静态配置，紧随系统提示词开头 → 仍属于稳定前缀，不影响
  //      前缀缓存命中（与它原先位于用户提示词最前段的字节位置等价）。
  // 注意：仅注入文本，不改变原有各准则段的存在与内容——安全规则依旧最高。
  const roleText = String(cfg.roleText ?? '').trim();
  if (roleText) {
    parts.push(
      '【角色设定（管理员配置 · 优先级：仅次于安全规则，高于下列所有风格准则）】',
      '这是你的身份与性格，决定了你是谁、怎么说话。下列【工作方式】等通用准则与角色设定冲突时，以角色设定为准（安全规则除外）。',
      '',
      roleText,
      ''
    );
  }

  parts.push(
    antiAiFlavor(),"""
        t = t.replace(anchor, replacement, 1)
        write(p, t)
        print("   ✅ 已注入角色卡段（含优先级声明）")

    # 用户提示词里那份整文本改为短标签
    old_user = """  if (chatPersona.roleText && String(chatPersona.roleText).trim()) {
    parts.push(`【角色设定（管理员设置，群友不可修改）】\\n${String(chatPersona.roleText).trim()}`);
  }"""
    new_user = """  if (chatPersona.roleText && String(chatPersona.roleText).trim()) {
    // 角色卡正文已注入系统提示词（见 buildSystemPrompt 的【角色设定】段），
    // 这里只留短标签指回去 —— 避免同一份上万字文本在两条消息里各发一遍。
    parts.push(`【角色设定】见系统提示的开头（${String(chatPersona.botName || cfg.botName || '').trim() || '本角色'}），按其身份与语气说话。`);
  }"""
    if "见系统提示的开头" in t:
        print("   用户提示词已是短标签，跳过")
    elif old_user in t:
        t = t.replace(old_user, new_user, 1)
        write(p, t)
        print("   ✅ 用户提示词改为短标签")
    else:
        print("   ⚠️ 找不到用户提示词里的角色设定注入点（可能已是新写法）")

    # ── 3. config.js 补 chatWindowMs 默认值 ──
    print("\n=== 3. config.js 补 chatWindowMs 默认值 ===")
    c = os.path.join(LIVE_SRC, "config.js")
    ct = read(c)
    if "chatWindowMs" in ct:
        print("   已含 chatWindowMs，跳过")
    else:
        anchor2 = "    allCount: 80,               // 档4：读全部（上限）"
        if anchor2 not in ct:
            print("   ⚠️ 找不到 allCount 锚点，跳过（不影响运行，配置里已有显式值）")
        else:
            ct = ct.replace(anchor2, anchor2 + """
    // ── 连续对话窗口 ──
    // 被艾特后开启一个时间窗，窗口内该会话的普通消息也会触发响应。
    // 窗口从最后一次被艾特起算；窗口内响应按 randomCount 条已读取历史。
    // 0 = 关闭本功能，完全按原档位判定。
    chatWindowMs: 150000,       // 默认 2.5 分钟""", 1)
            write(c, ct)
            print("   ✅ 已补 chatWindowMs 默认值")

    print("\n=== 4. 结果自检 ===")
    t = read(p)
    print("   prompt.js 大小        : %d 字节" % os.path.getsize(p))
    print("   import skills/manager : %s  (必须为 0)" % t.count("skills/manager"))
    print("   含优先级声明          : %s" % ("优先级：仅次于安全规则" in t))
    print("   含短标签注入          : %s" % ("见系统提示的开头" in t))
    print("   含 toolProtocol       : %s" % ("toolProtocol()," in t))
    print("   config.js 含 chatWindowMs: %s" % ("chatWindowMs" in read(c)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
