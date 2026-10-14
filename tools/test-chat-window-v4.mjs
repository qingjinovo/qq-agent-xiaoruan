// ============================================================================
//  resolveWithChatWindow 单测（适配 0.4.0 移植版）
//
//  运行：node tools/test-chat-window-v4.mjs
//  纯函数测试：不碰网络、不碰真实 QQ、不读系统时钟（时间由调用方传入）。
// ============================================================================

import { resolveWithChatWindow } from '../resources/app/src/prompt.js';

const T0 = 1_700_000_000_000;
let pass = 0, fail = 0;

function check(name, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { pass++; console.log(`  [OK]   ${name}`); }
  else {
    fail++;
    console.log(`  [FAIL] ${name}`);
    console.log(`         期望: ${e}`);
    console.log(`         实际: ${a}`);
  }
}

const NO_TRIGGER = { tier: 0, count: 0, reason: '未触发', shouldRespond: false };
const KEYWORD_HIT = { tier: 2, count: 15, reason: '关键词命中', shouldRespond: true };
const AT_HIT = { tier: 1, count: 12, reason: '被艾特', shouldRespond: true };

const base = (over = {}) => ({
  atMe: false, windowOpen: false, resolved: NO_TRIGGER,
  windowMs: 120000, equalCount: 12, windowCount: 8,
  ...over,
});

console.log('== 1. 功能关闭（windowMs <= 0）==');
// 注意：真实调用点（orchestrator #resolveTier）在 windowMs<=0 时根本不会进入本函数，
// 所以这里的期望是"原样返回调用方给的判定，且绝不碰窗口状态"。
const off = resolveWithChatWindow(base({ windowMs: 0, atMe: true, resolved: AT_HIT, equalCount: 12 }));
check('原判定原样返回（命中就是命中）', { r: off.shouldRespond, t: off.tier }, { r: true, t: 1 });
check('不开启窗口', { o: off.opened, w: off.windowOpen }, { o: false, w: false });
const offMiss = resolveWithChatWindow(base({ windowMs: 0, atMe: true, resolved: NO_TRIGGER }));
check('未命中时即使 atMe 也不响应（窗口关闭）', offMiss.shouldRespond, false);

console.log('\n== 2. 召唤（atMe）→ 必须响应 + 开窗 ==');
const summon = resolveWithChatWindow(base({ atMe: true, resolved: AT_HIT }));
check('响应', summon.shouldRespond, true);
check('标记为"新开窗"', { o: summon.opened, w: summon.windowOpen }, { o: true, w: true });
check('条数沿用召唤档', summon.count, 12);

console.log('\n== 3. 窗口内：普通消息也触发 ==');
const inWin = resolveWithChatWindow(base({ windowOpen: true, resolved: NO_TRIGGER }));
check('响应', inWin.shouldRespond, true);
check('reason=连续对话窗口', inWin.reason, '连续对话窗口');
check('tier=9', inWin.tier, 9);
check('条数用 windowCount', inWin.count, 8);
check('不算新开窗（用于续期而非日志）', { o: inWin.opened, w: inWin.windowOpen }, { o: false, w: true });

console.log('\n== 4. 无窗口 + 未命中 → 不响应 ==');
const none = resolveWithChatWindow(base({ windowOpen: false, resolved: NO_TRIGGER }));
check('不响应', none.shouldRespond, false);
check('无窗口', none.windowOpen, false);
check('未开窗', none.opened, false);

console.log('\n== 5. 原档位已命中（如关键词）→ 按原档走 ==');
const kw = resolveWithChatWindow(base({ windowOpen: false, resolved: KEYWORD_HIT }));
check('响应', kw.shouldRespond, true);
check('保留关键词档位', { t: kw.tier, r: kw.reason, c: kw.count }, { t: 2, r: '关键词命中', c: 15 });
check('窗口状态不变（没被误开）', { o: kw.opened, w: kw.windowOpen }, { o: false, w: false });

console.log('\n== 6. 已在窗口内再次召唤 → 续期（不是新开窗）==');
const re = resolveWithChatWindow(base({ atMe: true, windowOpen: true, resolved: AT_HIT }));
check('不是新开窗（调用方据此续期而非重复日志）', re.opened, false);
check('仍在窗口', re.windowOpen, true);
check('响应', re.shouldRespond, true);

console.log('\n== 7. equalCount 缺省时回落到原档条数 ==');
const noCount = resolveWithChatWindow(base({ atMe: true, equalCount: 0, resolved: { tier: 1, count: 20, reason: '被艾特', shouldRespond: true } }));
check('沿用 resolved.count', noCount.count, 20);

console.log('\n== 8. windowMs 非法值容错（应视作关闭，原样返回）==');
for (const bad of [null, undefined, NaN, 'abc', -5]) {
  const r = resolveWithChatWindow(base({ windowMs: bad, atMe: true, resolved: AT_HIT }));
  check(`windowMs=${String(bad)} 不崩溃、不开窗、原判定透传`,
    { r: r.shouldRespond, o: r.opened, w: r.windowOpen, t: r.tier },
    { r: true, o: false, w: false, t: 1 });
}

console.log(`\n===== 结果：${pass} 项通过，${fail} 项失败 =====`);
process.exit(fail === 0 ? 0 : 1);
