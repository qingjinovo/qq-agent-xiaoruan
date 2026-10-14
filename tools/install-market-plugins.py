#!/usr/bin/env python3
"""
install-market-plugins.py —— 通过市场口令安装插件

流程（照 routes.js 的实现）：
  1. POST /api/market/verify   {codes:[...]}  -> 拿到每个口令的条目(entry)
  2. POST /api/market/install  {code, entry}  -> 按 entry.type 装进 plugins/ 或 skills/
  3. 安装后 app 会自动 reloadSkills，直接读 /api/skills 确认

用法: python3 install-market-plugins.py [code1 code2 ...]
"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:3210"
CODES = sys.argv[1:] or ["3XT9L6", "PR3P6D", "BS4XZ3"]


def post(path, payload, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return {"ok": False, "httpError": exc.code, "raw": raw[:400]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main():
    print("=== 1. 校验口令 ===")
    v = post("/api/market/verify", {"codes": CODES})
    if not v.get("ok"):
        print("   校验失败:", json.dumps(v, ensure_ascii=False)[:400])
        return 1

    entries = v.get("results") or []
    for e in entries:
        print("   %-8s %-22s %-20s v%-7s %s" % (
            e.get("code"), e.get("name"), e.get("id"), e.get("version"),
            "✅ 可用" if e.get("ok") else "❌ 不可用"))

    ok_entries = [e for e in entries if e.get("ok")]
    if not ok_entries:
        print("   没有可安装的条目")
        return 1

    print()
    print("=== 2. 逐个安装 ===")
    results = []
    for e in ok_entries:
        code = e.get("code")
        r = post("/api/market/install", {"code": code, "entry": e})
        if r.get("ok"):
            print("   ✅ %-8s %-22s -> %s (%s)" % (
                code, e.get("name"), r.get("id"), r.get("kind")))
        else:
            print("   ❌ %-8s %-22s 失败: %s" % (
                code, e.get("name"), (r.get("error") or json.dumps(r, ensure_ascii=False))[:200]))
        results.append({"code": code, "name": e.get("name"), "result": r})

    print()
    print("=== 3. 已安装的 Skill/插件清单 ===")
    try:
        with urllib.request.urlopen(BASE + "/api/skills", timeout=20) as resp:
            s = json.load(resp)
        print("   summary:", json.dumps(s.get("summary"), ensure_ascii=False))
        for item in (s.get("skills") or []):
            print("   - %-22s v%-8s %s  enabled=%s" % (
                item.get("id"), item.get("version"),
                item.get("name") or "", item.get("enabled")))
        caps = s.get("capabilities") or []
        if caps:
            print("   提供的能力:", ", ".join(caps))
    except Exception as exc:
        print("   读取 /api/skills 失败:", exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
