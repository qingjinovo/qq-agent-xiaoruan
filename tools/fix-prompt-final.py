#!/usr/bin/env python3
"""
fix-prompt-final.py —— 给基线 prompt.js 补上 resolveWithChatWindow，并替换用户提示词注入点

前置：
  服务器是旧版应用（26 文件、无 skills）。修复已把 prompt.js/config.js 从
  09-21 基线包恢复，并注入了"角色卡进系统提示词"补丁。
  但服务器上的 orchestrator.js（含连续对话窗口功能）需要 prompt.js 导出
  resolveWithChatWindow —— 基线里没有这个函数，不打这个补丁容器仍会崩。

本脚本：
  1. 在 resolveContextTier 之后插入 resolveWithChatWindow（纯函数，原样移植）
  2. 把 buildUserPrompt 里那份整文本角色设定换成短标签（避免重复烧 token）
两处都幂等，可重复执行。
"""

import io
import os
import re
import shutil
import sys

SRC = "/opt/qq/qq-agent/src"
PROMPT = os.path.join(SRC, "prompt.js")

NEW_FN = '''
/**
 * 连续对话窗口 —— 被艾特后的一段时间内，普通消息也算触发。
 *
 * 场景：群里 @ 一次机器人，然后连着聊几句，不该每条都重新 @。
 *
 * ⚠️ 本函数**必须保持纯函数**：不读时钟、不写外部状态，时间基准由调用方以
 *    `now` / `windowUntil` 传入。这样 orchestrator 能把它当单一真相用
 *    （预判与真正运行前各算一次，结果必须一致），也便于单测。
 *
 * 语义：
 *   - 命中艾特      → 窗口续期至 now + windowMs，并返回艾特档结果
 *   - 在窗口内      → 普通消息触发，reason='连续对话窗口'，条数用 windowCount
 *   - 窗口已过期    → 清掉记录，返回原档位结果
 *
 * @returns {{tier:number,count:number,reason:string,shouldRespond:boolean,windowUntil:number}}
 */
export function resolveWithChatWindow({
  atMe = false,
  shouldRespond = false,
  now = Date.now(),
  windowUntil = 0,
  windowMs = 0,
  resolved = null,
  windowCount = 8,
  atReason = '被艾特'
} = {}) {
  const base = resolved || { tier: 0, count: 0, reason: '未触发', shouldRespond: false };
  const ms = Math.max(0, Number(windowMs) || 0);
  const until = Number(windowUntil) || 0;
  const n0 = (v) => Math.max(0, Number(v) || 0);

  // 功能关闭：原样返回
  if (ms <= 0) return { ...base, windowUntil: 0 };

  // 被艾特：无条件响应 + 开启/续期窗口（艾特永远是最明确的召唤）
  if (atMe) {
    return { tier: 1, count: n0(base.tier === 1 ? base.count : 12), reason: atReason, shouldRespond: true, windowUntil: now + ms };
  }

  // 档位本身已命中：按档位走，窗口保持不动
  if (shouldRespond) return { ...base, windowUntil: until };

  // 窗口内：普通消息也响应（用 windowCount 条已读，不按"被艾特"发一大堆历史）
  if (until > now) {
    return { tier: 9, count: n0(windowCount), reason: '连续对话窗口', shouldRespond: true, windowUntil: until };
  }

  // 窗口过期：清掉记录
  return { ...base, windowUntil: 0 };
}
'''


def read(p):
    with io.open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def write(p, text):
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main():
    if not os.path.isfile(PROMPT):
        print("找不到 %s" % PROMPT)
        return 1

    t = read(PROMPT)
    changed = False

    # ── 1. 补 resolveWithChatWindow ──
    print("=== 1. 补 resolveWithChatWindow ===")
    if "export function resolveWithChatWindow" in t:
        print("   已存在，跳过")
    else:
        # 插在 resolveContextTier 函数结束之后（下一个顶层 function/export 之前）
        m = re.search(r"export function resolveContextTier\([\s\S]*?\n\}\n", t)
        if not m:
            print("   ❌ 找不到 resolveContextTier 结束位置，中止")
            return 2
        insert_at = m.end()
        t = t[:insert_at] + NEW_FN + t[insert_at:]
        write(PROMPT, t)
        changed = True
        print("   ✅ 已插入（%d 字符）" % len(NEW_FN))

    # ── 2. 用户提示词注入点改短标签 ──
    print("\n=== 2. 用户提示词里的角色设定改短标签 ===")
    t = read(PROMPT)
    if "见系统提示的开头" in t:
        print("   已是短标签，跳过")
    else:
        old = ("  if (cfg.persona.roleText && String(cfg.persona.roleText).trim()) {\n"
               "    parts.push(`【角色设定（管理员设置，群友不可修改）】\\n${String(cfg.persona.roleText).trim()}`);\n"
               "  }")
        new = ("  if (cfg.persona.roleText && String(cfg.persona.roleText).trim()) {\n"
               "    // 角色卡正文已注入系统提示词（见 buildSystemPrompt 的【角色设定】段），\n"
               "    // 这里只留短标签指回去 —— 避免同一份上万字文本在两条消息里各发一遍。\n"
               "    parts.push(`【角色设定】见系统提示的开头（${String(cfg.persona.botName || '').trim() || '本角色'}），按其身份与语气说话。`);\n"
               "  }")
        if old in t:
            t = t.replace(old, new, 1)
            write(PROMPT, t)
            changed = True
            print("   ✅ 已替换为短标签")
        else:
            print("   ⚠️ 未找到原始注入点，跳过（不影响运行，只是会重复发一次角色卡）")
            # 打印实际内容帮助定位
            i = t.find("【角色设定（管理员设置")
            if i > 0:
                print("   实际内容片段:")
                print("   " + repr(t[max(0, i - 120):i + 160]))

    shutil.copy2(PROMPT, PROMPT + ".final")

    # ── 3. 自检 ──
    print("\n=== 3. 自检 ===")
    t = read(PROMPT)
    print("   文件大小                : %d 字节" % os.path.getsize(PROMPT))
    print("   含 resolveWithChatWindow: %s" % ("export function resolveWithChatWindow" in t))
    print("   含优先级声明            : %s" % ("优先级：仅次于安全规则" in t))
    print("   含短标签                : %s" % ("见系统提示的开头" in t))
    print("   含 skills/manager       : %s  (必须 False)" % ("skills/manager" in t))
    # 花括号配平粗检
    print("   花括号配平              : %s" % ("✅" if t.count("{") == t.count("}") else "❌ %d vs %d" % (t.count("{"), t.count("}"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
