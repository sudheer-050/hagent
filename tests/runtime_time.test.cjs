// Run with: node tests/runtime_time.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
process.env.TZ = 'America/New_York';
const source = fs.readFileSync('hagent/static/app.js', 'utf8');
const helpers = source.slice(source.indexOf('function runtimeCheckTime'), source.indexOf('async function runRuntimeAction'));
const time = {getAttribute: () => '2026-11-01T03:47:13'};
const window = {};
const format = vm.runInNewContext(helpers + '; runtimeCheckTime', {
  runtimeRefreshedAt: new Map(), window, document: {querySelectorAll: selector => selector === "time[data-runtime-time]" ? [time] : []}, Date, Number
});
assert.equal(format('2026-11-01T03:47:13'), format('2026-11-01T03:47:13Z'));
assert.equal(format('2026-10-31T23:47:13-04:00'), format('2026-11-01T03:47:13Z'));
assert.ok(format('2026-11-01T03:47:13').includes('10/31/2026'));
assert.ok(format('2026-11-01T05:30:00').includes('EDT'));
assert.ok(format('2026-11-01T06:30:00').includes('EST'));
assert.equal(time.textContent, format(time.getAttribute()));
time.textContent = 'UTC';
window.hagentPageInits[0]();
assert.equal(time.textContent, format(time.getAttribute()));
console.log('Runtime times: UTC parsing, local display, daylight saving, and page refresh passed.');
