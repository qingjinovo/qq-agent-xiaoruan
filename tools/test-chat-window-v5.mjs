// ============================================================================
//  resolveWithChatWindow 单测（适配"概率作用于窗口内"的新语义）
//
//  运行：node tools/test-chat-window-v5.mjs
//  纯函数测试：不碰网络、不碰真实 QQ、时间与骰子值都由调用方传入。
//
//  新语义（2026-10-14 修订）：
//    · 窗口外 + 没召唤 → 一定不响应（机器人平常不主动插话）
//    · 窗口内         → 按 windowProb 决定接不接这句话
//    · 召唤（@/关键词）→ 必响应，并开启/续期窗口
//
//  路径自适应：仓库结构（app/src/）与本机开发结构（resources/app/src/）都要能跑。
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
const { resolveWithChatWindow } = await import(rel);

let pass = 0, fail = 0;
function check(name, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { pass++; console.log(`  [OK]   ${name}`); }
  else {
    fail++;
    console.log(`  [FAIL] ${name}\n         期望: ${e}\n         实际: ${a}`);
  }
}

const NO_TRIGGER = { tier: 0, count: 0, reason: '未触发', shouldRespond: false };
const KEYWORD_HIT = { tier: 2, count: 15, reason: '关键词命中', shouldRespond: true };
const AT_HIT = { tier: 1, count: 12, reason: '被艾特', shouldRespond: true };

const base = (over = {}) => ({
  atMe: false, windowOpen: false, resolved: NO_TRIGGER,
  windowMs: 120000, windowProb: 100, roll: null,
  equalCount: 12, windowCount: 8,
  ...over,
});

console.log('== 1. 功能关闭（windowMs <= 0）：原判定透传，不碰窗口 ==');
{
  const off = resolveWithChatWindow(base({ windowMs: 0, atMe: true, resolved: AT_HIT }));
  check('原样返回（命中就是命中）', { r: off.shouldRespond, t: off.tier }, { r: true, t: 1 });
  check('不开启窗口', { o: off.opened, w: off.windowOpen }, { o: false, w: false });
  const miss = resolveWithChatWindow(base({ windowMs: 0, atMe: true, resolved: NO_TRIGGER }));
  check('未命中时即使 atMe 也不响应', miss.shouldRespond, false);
}

console.log('\n== 2. 窗口外 + 没召唤 → 一定不响应（核心：平常不主动插话）==');
for (const prob of [100, 50, 1]) {
  // 即使概率给到 100%，窗口外也不该响应 —— 概率只作用于窗口内
  const r = resolveWithChatWindow(base({ windowProb: prob, roll: 0, resolved: NO_TRIGGER }));
  check(`windowProb=${prob}% 窗口外仍不响应`, { r: r.shouldRespond, o: r.opened }, { r: false, o: false });
}

console.log('\n== 3. 召唤（atMe）→ 必响应 + 开窗（与概率无关）==');
{
  const s = resolveWithChatWindow(base({ atMe: true, resolved: AT_HIT, windowProb: 1, roll: 99 }));
  check('响应（概率再低也响应）', s.shouldRespond, true);
  check('标记为新开窗', { o: s.opened, w: s.windowOpen }, { o: true, w: true });
  check('条数沿用召唤档', s.count, 12);
}

console.log('\n== 4. 窗口内按概率决定接不接 ==');
{
  const hit = resolveWithChatWindow(base({ windowOpen: true, resolved: NO_TRIGGER, windowProb: 50, roll: 30 }));
  check('roll=30 < 50% → 响应', hit.shouldRespond, true);
  check('tier=9', hit.tier, 9);
  check('reason 含概率明细', hit.reason.includes('30%<50%'), true);
  check('条数用 windowCount', hit.count, 8);
  check('不算新开窗', { o: hit.opened, w: hit.windowOpen }, { o: false, w: true });

  const miss = resolveWithChatWindow(base({ windowOpen: true, resolved: NO_TRIGGER, windowProb: 50, roll: 80 }));
  check('roll=80 ≥ 50% → 不响应', miss.shouldRespond, false);
  check('reason 标注窗口内未命中', miss.reason.includes('窗口内未命中'), true);
  check('窗口仍然开着（不因未命中而关闭）', miss.windowOpen, true);
  check('也不算新开窗', miss.opened, false);
}

console.log('\n== 5. 概率边界 ==');
{
  const p0 = resolveWithChatWindow(base({ windowOpen: true, windowProb: 0, roll: 0 }));
  check('0% → 永不接（roll=0 也不接）', p0.shouldRespond, false);

  const p100 = resolveWithChatWindow(base({ windowOpen: true, windowProb: 100, roll: 99.99 }));
  check('100% → 必接', p100.shouldRespond, true);

  const bad = resolveWithChatWindow(base({ windowOpen: true, windowProb: null, roll: 99 }));
  check('概率非法 → 当作 100%', bad.shouldRespond, true);
}

console.log('\n== 6. roll 缺省时内部现掷（不崩溃，结果落在闭区间内）==');
{
  let ok = true;
  for (let i = 0; i < 200; i++) {
    const r = resolveWithChatWindow(base({ windowOpen: true, windowProb: 50, roll: null }));
    if (typeof r.shouldRespond !== 'boolean') { ok = false; break; }
  }
  check('200 次随机调用均返回布尔', ok, true);

  // 统计意义：50% 概率下命中率应接近一半（放宽到 30%~70% 避免偶发失败）
  let hits = 0;
  for (let i = 0; i < 2000; i++) {
    if (resolveWithChatWindow(base({ windowOpen: true, windowProb: 50, roll: null })).shouldRespond) hits++;
  }
  const rate = hits / 2000;
  check(`2000 次命中率 ${(rate * 100).toFixed(1)}% 落在 30%~70%`, rate > 0.3 && rate < 0.7, true);
}

console.log('\n== 7. 原档位已命中（如 3 档随机命中）→ 按原档走，不碰窗口 ==');
{
  const kw = resolveWithChatWindow(base({ windowOpen: false, resolved: KEYWORD_HIT }));
  check('响应', kw.shouldRespond, true);
  check('保留原档位与条数', { t: kw.tier, r: kw.reason, c: kw.count }, { t: 2, r: '关键词命中', c: 15 });
  check('没被误开窗', { o: kw.opened, w: kw.windowOpen }, { o: false, w: false });
}

console.log('\n== 8. 窗口内再次召唤 → 续期（不是新开窗）==');
{
  const re = resolveWithChatWindow(base({ atMe: true, windowOpen: true, resolved: AT_HIT, windowProb: 1, roll: 99 }));
  check('不是新开窗（调用方据此续期）', re.opened, false);
  check('仍在窗口', re.windowOpen, true);
  check('响应（召唤优先于概率）', re.shouldRespond, true);
}

console.log('\n== 9. equalCount 缺省时回落到原档条数 ==');
{
  const r = resolveWithChatWindow(base({
    atMe: true, equalCount: 0,
    resolved: { tier: 1, count: 20, reason: '被艾特', shouldRespond: true },
  }));
  check('沿用 resolved.count', r.count, 20);
}

console.log('\n== 10. windowMs 非法值容错 ==');
for (const badMs of [null, undefined, NaN, 'abc', -5]) {
  const r = resolveWithChatWindow(base({ windowMs: badMs, atMe: true, resolved: AT_HIT }));
  check(`windowMs=${String(badMs)} 视作关闭、原判定透传`,
    { r: r.shouldRespond, o: r.opened, t: r.tier }, { r: true, o: false, t: 1 });
}

console.log(`\n===== 结果：${pass} 项通过，${fail} 项失败 =====`);
process.exit(fail === 0 ? 0 : 1);
