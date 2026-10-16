// ============================================================================
//  窗口计时语义测试（重演 orchestrator #resolveTier 的状态机）
//
//  要证明的行为：
//    · 窗口从"最后一次召唤（@ / 关键词）"起算，固定 N 秒
//    · 窗口内的【普通消息】只受益、**不续期** —— 群里一直有人说话也不会延长窗口
//    · 窗口内【再次召唤】才重新计时
//    · 过期后普通消息不再触发
//
//  这里用假时钟重演真实调用序列，不依赖系统时间。
//
//  运行：node tools/test-window-timing.mjs
//  路径自适应：本文件既能在仓库里跑（app/src/），也能在本机开发时跑
//  （resources/app/src/）—— 写死其中一种，换台机器就挂。
// ============================================================================

import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CANDIDATES = [
  '../app/src/prompt.js',              // 仓库结构：<root>/app/src/
  '../resources/app/src/prompt.js',    // 本机开发结构：<root>/resources/app/src/
  './app/src/prompt.js',               // 从仓库根目录调用时
  './resources/app/src/prompt.js',
];
const rel = CANDIDATES.find((p) => existsSync(path.resolve(__dirname, p)));
if (!rel) {
  console.error('找不到 prompt.js。已尝试：\n  ' + CANDIDATES.join('\n  '));
  console.error('请在仓库根目录或 tools/ 目录下运行本测试。');
  process.exit(2);
}
const { resolveContextTier, resolveWithChatWindow, isAtMe } = await import(rel);

const WINDOW_MS = 120_000;          // 2 分钟
const T0 = 1_700_000_000_000;

let pass = 0, fail = 0;
function check(name, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { pass++; console.log(`  [OK]   ${name}`); }
  else {
    fail++;
    console.log(`  [FAIL] ${name}\n         期望: ${e}\n         实际: ${a}`);
  }
}

// ── 重演 orchestrator #resolveTier 的状态机 ──
// 身份用占位值：本仓库是公开的，测试数据里不必出现真实的 QQ 号/昵称。
const atCtx = { selfNickname: '小软', botName: '小软', selfId: '10000' };
const chatCfg = {
  contextTier: 2,          // 2 档：被 @ 或命中关键词才召唤
  randomPercent: 0,
  keywords: ['小软'], historyCount: 80,
  chatWindowMs: WINDOW_MS,
  chatWindowProb: 100,     // 本文件只测"窗口计时"，概率设为 100 以隔离变量
};

function makeMachine() {
  const windows = new Map();     // 对应 this.chatWindowUntil
  let clock = T0;
  return {
    get clock() { return clock; },
    setClock(t) { clock = t; },
    /** 模拟一条消息进来，返回 {shouldRespond, reason, windowUntil} */
    incoming(text) {
      const base = resolveContextTier({ triggerEntries: [{ text }], ...atCtx, cfg: chatCfg });
      const windowMs = chatCfg.chatWindowMs;
      const untilIn = windows.get('group:1') || 0;
      const atMe = isAtMe(text, atCtx) || base.reason === '关键词命中';

      const out = resolveWithChatWindow({
        atMe, windowOpen: untilIn > clock, resolved: base,
        windowMs, windowProb: chatCfg.chatWindowProb, roll: null,
        equalCount: base.count, windowCount: chatCfg.historyCount,
      });

      // ★ 与 orchestrator 修正后的逻辑逐字对应
      if (out.opened) windows.set('group:1', clock + windowMs);
      else if (atMe) windows.set('group:1', clock + windowMs);
      else if (!out.windowOpen) windows.delete('group:1');

      return { shouldRespond: out.shouldRespond, reason: out.reason, until: windows.get('group:1') || 0 };
    },
  };
}

console.log('== 1. 不召唤：普通消息不响应，也不开窗 ==');
{
  const m = makeMachine();
  const r = m.incoming('今天天气不错');
  check('不响应', r.shouldRespond, false);
  check('没有窗口', r.until, 0);
}

console.log('\n== 2. 关键词召唤 → 开窗，到期时间 = 召唤时刻 + 2 分钟 ==');
{
  const m = makeMachine();
  const r = m.incoming('小软在吗');
  check('响应', r.shouldRespond, true);
  check('reason=关键词命中', r.reason, '关键词命中');
  check('窗口到期 = T0 + 120s', r.until, T0 + WINDOW_MS);
}

console.log('\n== 3. ★核心：窗口内普通消息【不延长】窗口 ==');
{
  const m = makeMachine();
  m.incoming('小软在吗');                       // T0 召唤，窗口到 T0+120s
  const deadline = T0 + WINDOW_MS;

  for (const offset of [30_000, 60_000, 90_000, 119_000]) {
    m.setClock(T0 + offset);
    const r = m.incoming(`普通消息 ${offset / 1000}s`);
    check(`${offset / 1000}s 时普通消息仍响应`, r.shouldRespond, true);
    check(`${offset / 1000}s 时到期时间未被推后`, r.until, deadline);
  }
}

console.log('\n== 4. 到期后（再多说一句也不再响应）==');
{
  const m = makeMachine();
  m.incoming('小软在吗');
  m.setClock(T0 + WINDOW_MS + 1);               // 刚过 2 分钟
  const r = m.incoming('还在聊吗');
  check('不响应（窗口已关）', r.shouldRespond, false);
  check('窗口记录被清掉', r.until, 0);
}

console.log('\n== 5. 窗口内再次召唤 → 重新计时（唯一允许续期的情况）==');
{
  const m = makeMachine();
  m.incoming('小软在吗');                        // T0 开窗
  m.setClock(T0 + 90_000);
  const r = m.incoming('@小软 再问一句');         // 90s 时再次召唤
  check('响应', r.shouldRespond, true);
  check('到期时间 = 再次召唤 + 120s', r.until, T0 + 90_000 + WINDOW_MS);
  m.setClock(T0 + 150_000);                     // 原窗口(120s)之外的 150s
  const r2 = m.incoming('继续聊');
  check('原窗口已过，但新窗口内 → 仍响应', r2.shouldRespond, true);
}

console.log('\n== 6. 边界：恰好到期那一刻 ==');
{
  const m = makeMachine();
  m.incoming('小软在吗');
  m.setClock(T0 + WINDOW_MS);                   // 正好到点
  const r = m.incoming('卡点消息');
  check('到点即失效（不响应）', r.shouldRespond, false);
}

console.log('\n== 7. 长时间安静后：不会因为"群里有历史消息"而误开窗 ==');
{
  const m = makeMachine();
  m.incoming('小软在吗');
  m.setClock(T0 + WINDOW_MS + 600_000);         // 10 分钟后
  m.incoming('闲聊一句');
  const r = m.incoming('再闲聊一句');
  check('连续普通消息都不响应', r.shouldRespond, false);
  check('始终没有窗口', r.until, 0);
}

console.log(`\n===== 结果：${pass} 项通过，${fail} 项失败 =====`);
process.exit(fail === 0 ? 0 : 1);
