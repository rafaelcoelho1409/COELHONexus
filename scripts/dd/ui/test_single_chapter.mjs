// Offline check of the per-chapter ▶ button on the Synth chapter strip (jsdom + the real synth modules).
//   cd scripts/dd/ui && npm i && npm test
import { JSDOM } from 'jsdom';
import assert from 'node:assert/strict';

const dom = new JSDOM(`<!doctype html><body>
  <button id="fw-synth-start"></button><button id="fw-synth-wipe"></button>
  <div id="fw-chstrip"><span id="fw-chstrip-counter"></span><div id="fw-chstrip-cells"></div></div>
  <div id="fw-modal"><div id="fw-modal-title"></div><div id="fw-modal-message"></div><button id="fw-modal-confirm"></button><button id="fw-modal-cancel"></button></div>
  <div id="fw-denied-toast"><span id="fw-denied-toast-text"></span><button id="fw-denied-toast-close"></button></div>
</body>`, { url: 'http://localhost/docs-distiller/synth?slug=asyncio', pretendToBeVisual: true });
const w = dom.window;
for (const k of ['window', 'document', 'localStorage', 'CustomEvent', 'Event', 'HTMLElement', 'MouseEvent', 'requestAnimationFrame', 'navigator']) {
  try { Object.defineProperty(globalThis, k, { value: w[k] ?? globalThis[k], configurable: true, writable: true }); } catch (_) {}
}
globalThis.window = w;

// ---- fetch / EventSource mocks
const calls = [];
let routes = {};
globalThis.fetch = async (url, opts = {}) => {
  const method = (opts.method || 'GET').toUpperCase();
  calls.push(method + ' ' + url);
  for (const [k, v] of Object.entries(routes)) {
    if ((method + ' ' + url).includes(k)) {
      const body = typeof v === 'function' ? v(url, opts) : v;
      return { ok: body.__status ? body.__status < 400 : true, status: body.__status || 200, json: async () => body, text: async () => JSON.stringify(body) };
    }
  }
  return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
};
const sources = [];
globalThis.EventSource = class { constructor(u) { this.url = u; this.closed = false; sources.push(this); } close() { this.closed = true; } };
const tick = (ms = 30) => new Promise(r => setTimeout(r, ms));

const Sy = await import('@dd/shared/state/synth.js');
const Si = await import('@dd/shared/state/ingestion.js');
const S = await import('@dd/synth/synth.js');
const L = await import('@dd/synth/lifecycle.js');
const { $activePipeline } = await import('@nx/stores/pipeline.js');

Si.setActiveSlug('asyncio');
Sy.setSynthImplemented(new Set(['outline_sdp']));
Sy.setSynthHasPlan(true);
routes['GET /api/v1/docs-distiller/pipeline/active'] = { planner: null, synth: null };
routes['GET /api/v1/docs-distiller/synth/asyncio/active'] = { active: false };

const chapters = [{ id: 'ch-01-a', title: 'Alpha' }, { id: 'ch-02-b', title: 'Beta' }, { id: 'ch-03-c', title: 'Gamma' }];
S._renderChStrip(chapters);
S.refreshSynthStartState();
const cells = () => [...document.querySelectorAll('.fw-chstrip-cell')];
const runBtn = (cid) => document.querySelector(`.fw-chstrip-run[data-chapter-id="${cid}"]`);
const status = (cid) => document.querySelector(`.fw-chstrip-cell[data-chapter-id="${cid}"]`).dataset.status;

// 1) every row has a ▶ button and, with a plan and nothing running, all are enabled
assert.equal(document.querySelectorAll('.fw-chstrip-run').length, 3);
assert.ok([...document.querySelectorAll('.fw-chstrip-run')].every(b => !b.disabled), 'enabled when idle');
console.log('1 ▶ on every row, enabled when idle ok');

// 2) click ▶ on chapter 2: POST with chapter_id, row running, graph attached to the chapter thread, Start -> Stop, ▶ disabled
const T1 = 'docs-distiller/synth/asyncio/T1';
routes['POST /api/v1/docs-distiller/synth/asyncio?chapter_id=ch-02-b&mode=quality'] = { thread_id: T1, slug: 'asyncio', chapter_id: 'ch-02-b', status: 'queued' };
routes['GET /api/v1/docs-distiller/synth/debug/graph/' + T1 + '/state'] = { values: { chapter_id: 'ch-02-b' }, next: ['outline_sdp'] };
runBtn('ch-02-b').click();
await tick(80);
assert.ok(calls.includes('POST /api/v1/docs-distiller/synth/asyncio?chapter_id=ch-02-b&mode=quality'), calls.join('\n'));
assert.equal(Sy.synthThreadId, T1);
assert.equal(status('ch-02-b'), 'running');
assert.equal(status('ch-01-a'), 'pending');
assert.equal(document.querySelector('#fw-synth-start').textContent, 'Stop');
assert.ok([...document.querySelectorAll('.fw-chstrip-run')].every(b => b.disabled), '▶ disabled while a run is live');
assert.equal(sources.length, 1);
assert.ok(sources[0].url.endsWith('/synth/' + T1 + '/events'), sources[0].url);
assert.ok(document.querySelector('.fw-chstrip-cell.selected')?.dataset.chapterId === 'ch-02-b');
assert.deepEqual($activePipeline.get(), { stage: 'synth', slug: 'asyncio', run_id: null });
assert.equal(localStorage.getItem('dd:synth:active:asyncio'), T1);
console.log('2 ▶ starts only that chapter and attaches the live graph ok');

// 3) a second ▶ press while running does nothing (button disabled, guard in startSingleChapter)
const nPost = calls.filter(c => c.startsWith('POST')).length;
await L.startSingleChapter('ch-03-c');
assert.equal(calls.filter(c => c.startsWith('POST')).length, nPost);
console.log('3 no second run while one is live ok');

// 4) live node events flow through the same SSE consumer, then the terminal event settles the row
const now = Date.now() / 1000;
sources[0].onmessage({ data: JSON.stringify({ step: 'outline_sdp', kind: 'start', ts: now }) });
routes['GET /api/v1/docs-distiller/synth/asyncio/study/chapters'] = {
  study_total_wall_ms: 5000,
  chapters: chapters.map((c, i) => ({ id: c.id, title: c.title, order: i, rendered: c.id === 'ch-02-b', wall_ms: c.id === 'ch-02-b' ? 91000 : 0, thread_id: c.id === 'ch-02-b' ? T1 : null })),
};
await sources[0].onmessage({ data: JSON.stringify({ step: 'synth', kind: 'terminal', status: 'done', ts: Date.now() / 1000 }) });
await tick(120);
assert.equal(Sy.synthThreadId, null);
assert.equal(status('ch-02-b'), 'done');
assert.equal(status('ch-01-a'), 'pending');
assert.ok(document.querySelector('.fw-chstrip-cell[data-chapter-id="ch-02-b"] .time').textContent.length > 0, 'wall time shown from the durable status');
assert.equal(document.querySelector('.fw-chstrip-cell[data-chapter-id="ch-02-b"]').dataset.chapterThreadId, T1);
assert.ok(document.querySelector('.fw-chstrip-open[data-chapter-id="ch-02-b"]').disabled === false, '"Open in Study" enabled');
assert.ok(![...document.querySelectorAll('.fw-chstrip-run')].some(b => b.disabled), '▶ enabled again');
assert.equal($activePipeline.get(), null);
assert.equal(localStorage.getItem('dd:synth:active:asyncio'), null);
assert.match(document.querySelector('#fw-synth-start').textContent, /Resume|Start/);
console.log('4 terminal event settles the row, timers, buttons ok');

// 5) a failed / cancelled chapter keeps its outcome on the row after the strip re-syncs from MinIO
routes['POST /api/v1/docs-distiller/synth/asyncio?chapter_id=ch-03-c&mode=quality'] = { thread_id: 'docs-distiller/synth/asyncio/T2', status: 'queued' };
routes['GET /api/v1/docs-distiller/synth/debug/graph/docs-distiller/synth/asyncio/T2/state'] = { values: {}, next: [] };
runBtn('ch-03-c').click();
await tick(80);
assert.equal(status('ch-03-c'), 'running');
await sources.at(-1).onmessage({ data: JSON.stringify({ step: 'synth', kind: 'terminal', status: 'failed', ts: Date.now() / 1000, error: 'boom' }) });
await tick(120);
assert.equal(status('ch-03-c'), 'failed');
assert.equal(status('ch-02-b'), 'done');
console.log('5 failed chapter keeps its outcome ok');

// 6) re-running a DONE chapter asks first, wipes only that chapter, then runs it
const before = calls.length;
runBtn('ch-02-b').click();
await tick(60);
assert.ok(document.querySelector('#fw-modal').classList.contains('visible'), 'confirmation shown');
assert.equal(calls.slice(before).filter(c => c.startsWith('DELETE')).length, 0, 'nothing wiped before confirming');
document.querySelector('#fw-modal-cancel').click();
await tick(40);
assert.equal(calls.slice(before).filter(c => c.startsWith('POST') || c.startsWith('DELETE')).length, 0, 'cancel = nothing happens');
routes['DELETE /api/v1/docs-distiller/synth/asyncio/wipe?chapter_id=ch-02-b'] = { minio_deleted: 14 };
routes['POST /api/v1/docs-distiller/synth/asyncio?chapter_id=ch-02-b&mode=quality'] = { thread_id: 'docs-distiller/synth/asyncio/T3', status: 'queued' };
routes['GET /api/v1/docs-distiller/synth/debug/graph/docs-distiller/synth/asyncio/T3/state'] = { values: {}, next: [] };
runBtn('ch-02-b').click();
await tick(60);
document.querySelector('#fw-modal-confirm').click();
await tick(120);
const after = calls.slice(before);
const iDel = after.findIndex(c => c.startsWith('DELETE')); const iPost = after.findIndex(c => c.startsWith('POST'));
assert.ok(iDel >= 0 && iPost > iDel, 'wipe happens before the run: ' + after.join(' | '));
assert.equal(Sy.synthThreadId, 'docs-distiller/synth/asyncio/T3');
assert.equal(status('ch-02-b'), 'running');
console.log('6 re-run of a done chapter confirms, wipes that chapter, runs it ok');

// 7) Stop while a chapter runs cancels that chapter's thread
routes['POST /api/v1/docs-distiller/synth/docs-distiller/synth/asyncio/T3/cancel'] = { status: 'cancel_requested', propagated_to: [] };
document.querySelector('#fw-synth-start').click();
await tick(60);
assert.ok(calls.includes('POST /api/v1/docs-distiller/synth/docs-distiller/synth/asyncio/T3/cancel'), calls.slice(-5).join(' | '));
await sources.at(-1).onmessage({ data: JSON.stringify({ step: 'synth', kind: 'terminal', status: 'cancelled', ts: Date.now() / 1000 }) });
await tick(120);
assert.equal(status('ch-02-b'), 'cancelled');
assert.equal(Sy.synthThreadId, null);
console.log('7 Stop cancels the chapter run ok');

// 8) a locked response (planner / another synth) starts nothing and leaves the buttons usable
routes['POST /api/v1/docs-distiller/synth/asyncio?chapter_id=ch-01-a&mode=quality'] = { status: 'locked', stage: 'planner', slug: 'x', message: 'A planner is running' };
runBtn('ch-01-a').click();
await tick(80);
assert.equal(Sy.synthThreadId, null);
assert.equal(status('ch-01-a'), 'pending');
assert.equal($activePipeline.get(), null);
console.log('8 locked response starts nothing ok');

// 9) page refresh mid-run: /active reports the chapter thread -> the row and graph re-attach
routes['GET /api/v1/docs-distiller/synth/asyncio/active'] = { active: true, chapter_thread_id: 'docs-distiller/synth/asyncio/T9', study_thread_id: null };
routes['GET /api/v1/docs-distiller/synth/debug/graph/docs-distiller/synth/asyncio/T9/state'] = { values: { chapter_id: 'ch-03-c' }, next: ['sawc_write'] };
localStorage.clear();
const resumed = await L._tryResumeActiveSynth('asyncio');
await tick(80);
assert.equal(resumed, true);
assert.equal(Sy.synthThreadId, 'docs-distiller/synth/asyncio/T9');
assert.equal(status('ch-03-c'), 'running');
assert.equal(document.querySelector('#fw-synth-start').textContent, 'Stop');
console.log('9 page refresh re-attaches to the running chapter ok');
console.log('ALL OK');
process.exit(0);
