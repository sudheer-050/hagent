// Run with: node tests/runtime_reload.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('hagent/static/app.js', 'utf8');
const handler = source.slice(source.indexOf('let runtimeReloadBusy'), source.indexOf('// Required connection fields'));
const summary = {};
const reload = {setAttribute() {}, removeAttribute() {}};
const panels = ['one', 'two', 'three'].map(id => {
  const status = {};
  const refreshed = {dataset:{runtimeRefreshed:id}};
  const pill = {classList: {toggle() {}, add() {}}};
  const panel = {
    dataset: {runtimeId: id},
    querySelector: selector => selector === '[data-runtime-test]' ? button : selector === '[data-runtime-refreshed]' ? refreshed : status,
    querySelectorAll: () => [button],
    closest: () => ({querySelector: () => pill})
  };
  const button = {closest: () => panel, hasAttribute: () => false};
  return panel;
});
let click;
let backendReady = true;
const calls = [];
const pendingResponses = new Map();
let clock = Date.parse("2026-11-01T04:00:00Z");
class ClockDate extends Date { constructor(value) { super(arguments.length ? value : clock); } }
vm.runInNewContext(handler, {
  window: {},
  AbortSignal,
  Map,
  Date: ClockDate,
  DOMParser: class { parseFromString() { return {querySelectorAll: () => panels.map((panel,i) => ({dataset:{runtimeId:panel.dataset.runtimeId,healthStatus:i===0?"healthy":"error",checkedAt:"2026-10-31T12:00:00Z"}}))}; } },
  document: {
    addEventListener: (_, callback) => click = callback,
    querySelectorAll: selector => selector === 'time[data-runtime-time]' ? [] : selector === '[data-runtime-refreshed]' ? panels.map(p=>p.querySelector('[data-runtime-refreshed]')) : panels,
    querySelector: () => summary
  },
  fetch: url => {
    if (url === '/api/runtime-status') return Promise.resolve({ok:backendReady,status:backendReady?200:404});
    calls.push(url);
    assert.ok(url.endsWith('/test'), 'Reload must make a fresh live request');
    return new Promise(resolve => pendingResponses.set(url, resolve));
  }

});
(async () => {
  const event = {target: {closest: selector => selector === '[data-runtime-reload]' ? reload : null}};
  const pending = click(event);
  assert.equal(reload.disabled, true);
  assert.ok(panels.every(panel => panel.dataset.refreshing === 'true'));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, ['/runtimes/one/test', '/runtimes/two/test', '/runtimes/three/test']);
  await click(event);
  assert.equal(calls.length, 3, 'Repeated clicks must not duplicate checks');
  const finish = async (id, ok) => {
    pendingResponses.get('/runtimes/'+id+'/test')({ok:true,json:async () => ({ok,message:ok?'OK':'Unavailable',elapsed_seconds:1,checked_at:new ClockDate().toISOString()})});
    await new Promise(resolve => setImmediate(resolve));
  };
  await finish('two', true);
  assert.equal(panels[1].dataset.refreshing, undefined);
  assert.equal(panels[0].dataset.refreshing, 'true');
  assert.ok(panels[1].querySelector('[data-runtime-refreshed]').textContent.startsWith('Status refreshed:'));
  await finish('one', false);
  await finish('three', true);
  await pending;
  assert.equal(summary.textContent, 'Fresh checks complete: 2 connected, 1 failed.');
  assert.equal(reload.disabled, false);
  assert.ok(panels.every(panel => panel.dataset.refreshing === undefined));
  assert.ok(panels[0].querySelector('[data-runtime-result]').textContent.includes('Unavailable'));
  const firstTime = panels[1].querySelector('[data-runtime-refreshed]').textContent;
  clock += 1000;
  const again = click(event);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 6, 'Every reload must send new requests');
  await finish('one', true); await finish('two', true); await finish('three', true);
  await again;
  assert.notEqual(panels[1].querySelector('[data-runtime-refreshed]').textContent, firstTime);
  backendReady = false;
  await click(event);
  assert.equal(calls.length, 6);
  assert.ok(summary.textContent.includes('older server'));
  assert.equal(reload.disabled, false);
  console.log('Runtime reload: fresh parallel requests, individual completion, failure, and repeat checks passed.');
})();
