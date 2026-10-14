#!/usr/bin/env python3
"""
audit-persona.py —— 验证实际送给模型的提示词里，人设是否只有一份且是新的

担心的问题：旧人设和新的是不是都被写进上下文了？
检查项：
  1. userPrompt 里【角色设定】出现几次（应为 1）
  2. systemPrompt 里有没有混进角色卡正文
  3. 新人设独有标记是否齐全、旧人设独有内容是否残留
  4. 会话历史（inputMessages）里有没有旧人设时代的遗留
"""

import glob
import json
import os
import re
import sys
import time

SESS = "/opt/qq/qq-agent-data/sessions"

NEW_MARKERS = [
    ("# 角色卡：DeepSeek 小鲸鱼（QQ 群友版）", "新人设标题"),
    ("温柔 4", "新人设：总体性格"),
    ("群聊说话铁律", "新人设：第二节"),
    ("事已至此", "新人设：吃饭梗"),
    ("本小姐", "新人设：避雷段"),
    ("replyToMessageId", "新人设：引用规则"),
    ("轻量记忆", "新人设：第十一节"),
]

# 旧人设里出现过、新人设里没有的措辞（用来判断是否残留旧版）
OLD_MARKERS = [
    ("角色卡：DeepSeek 小鲸鱼 —— QQ 群友版", "旧人设标题（破折号版）"),
    ("DeepSeek 虾", "旧版自称"),
    ("虾 / 小鲸鱼", "旧版身份写法"),
]


def newest_sessions(n=3):
    files = sorted(glob.glob(os.path.join(SESS, "*.json")), key=os.path.getmtime, reverse=True)
    return files[:n]


def main():
    files = newest_sessions(3)
    if not files:
        print("没有会话记录")
        return 1

    for p in files:
        d = json.load(open(p, encoding="utf-8"))
        t = d.get("startedAt") or 0
        ts = time.strftime("%H:%M:%S", time.localtime(t / 1000 if t > 1e12 else t))
        up = d.get("userPrompt") or ""
        sp = d.get("systemPrompt") or ""

        print("=" * 66)
        print("会话 %s  状态=%s  reason=%s" % (ts, d.get("status"), d.get("contextReason")))
        print("  systemPrompt %d 字 | userPrompt %d 字" % (len(sp), len(up)))

        if not up and not sp:
            print("  （提示词尚未落盘，跳过）")
            continue

        # 1. 角色设定段出现次数
        n_role = len(re.findall(r"【角色设定", up))
        n_role_sp = len(re.findall(r"【角色设定", sp))
        print("\n  [1] 【角色设定】出现次数: userPrompt=%d, systemPrompt=%d  (都应为 1 或 0)" % (n_role, n_role_sp))
        if n_role > 1:
            print("      ❌ 重复注入！")
        elif n_role == 1:
            print("      ✅ 只有一份")
        else:
            print("      ⚠️ userPrompt 里没有角色设定段")

        # 2. systemPrompt 里有没有角色卡正文
        leak = [lbl for k, lbl in NEW_MARKERS if k in sp]
        print("\n  [2] systemPrompt 混入角色卡内容: %s" % (", ".join(leak) if leak else "无 ✅"))

        # 3. 新标记齐全度
        print("\n  [3] 新人设标记（userPrompt）:")
        for k, lbl in NEW_MARKERS:
            print("      %-22s %s" % (lbl, "✅" if k in up else "❌"))

        # 4. 旧人设残留
        print("\n  [4] 旧人设残留检查:")
        found_old = False
        for k, lbl in OLD_MARKERS:
            if k in up or k in sp:
                print("      ❌ %s 仍存在" % lbl)
                found_old = True
        if not found_old:
            print("      ✅ 未发现旧人设标志")

        # 5. 角色设定段长度（应约等于新人设 5418 字）
        m = re.search(r"【角色设定（管理员设置，群友不可修改）】\n(.*?)(?=\n【|\Z)", up, re.S)
        if m:
            seg = m.group(1)
            print("\n  [5] 角色设定段长度: %d 字  (新人设文件应为 ~5418)" % len(seg))
            print("      段内前 3 行: %s" % " / ".join(seg.strip().split("\n")[:3])[:110])

        # 6. 会话历史里的旧人设时代遗留
        ims = d.get("inputMessages") or []
        print("\n  [6] 送给模型的消息条数: %d" % len(ims))
        roles = {}
        for m2 in ims:
            r = m2.get("role") or "?"
            roles[r] = roles.get(r, 0) + 1
        print("      角色分布: %s" % roles)
        big = [m2 for m2 in ims if len(str(m2.get("content") or "")) > 3000]
        print("      超长消息(>3000字)条数: %d  %s" % (
            len(big), "（含系统提示与角色设定，正常）" if len(big) <= 2 else "⚠️ 偏多，检查是否重复"))

    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
