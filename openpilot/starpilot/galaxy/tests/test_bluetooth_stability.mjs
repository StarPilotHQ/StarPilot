import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BluetoothFeed, BluetoothPage } from '../web/js/bluetooth.js';
import { compile } from '../web/vendor/vue/vue.esm-browser.js';
const car = {address: 'AA:BB:CC:DD:EE:FF', name: 'Car', paired: true, connected: false, trusted: true};
const adapter = {...car, address: '11:22:33:44:55:66', name: 'Adapter', paired: false, trusted: false};
const status = (devices = [car], extra = {}) => ({version: 1, available: true, parked: true,
  powered: true, discovering: true, errorCode: null, pairing: null, devices, ...extra});
const response = (body, code = 200) => ({status: code, ok: code === 200, json: async () => body});
const deferred = () => {let resolve; const promise = new Promise((done) => {resolve = done}); return {promise, resolve};};
function harness(fetcher, unauthorized = () => {}) {
  const states = [], timers = new Map(); let next = 0;
  const feed = new BluetoothFeed({fetcher, unauthorized, publish: (value) => states.push(value),
    later: (fn, ms) => {timers.set(++next, {fn, ms}); return next}, cancelTimer: (id) => timers.delete(id)});
  return {feed, states, timers};
}

test('first-load connection errors render without accessing missing adapter state', () => {
  const render = compile(BluetoothPage.template, {decodeEntities: value => value});
  const vm = {...BluetoothPage.data(), mode:'local', unidenSettings: false, go() {}, error:'Connection interrupted', unauthorized() {}};
  for (const [key, getter] of Object.entries(BluetoothPage.computed)) {
    Object.defineProperty(vm, key, {get: () => getter.call(vm)});
  }
  for (const [key, method] of Object.entries(BluetoothPage.methods)) vm[key] = method.bind(vm);
  assert.doesNotThrow(() => render(vm, []));
});

test('background reads retain existing state and never mark buttons busy', async () => {
  let devices = [car, adapter, {...car, address: '22:22:22:22:22:22', name: ' '},
    {...car, address: '33:33:33:33:33:33', name: '33:33:33:33:33:33'}];
  const delayed = deferred(); let wait = false;
  const {feed, states} = harness(async () => wait ? delayed.promise : response(status(devices)));
  await feed.start(); const before = feed.status; wait = true;
  const poll = feed.refresh();
  assert.equal(feed.status, before); assert.equal(feed.busy, false);
  devices = [{...adapter, name: ' Adapter renamed '}, {...car, connected: true}];
  delayed.resolve(response(status(devices))); await poll;
  assert.deepEqual(feed.status.devices.map((d) => d.name), ['Car', 'Adapter renamed']);
  assert.equal(feed.status.devices[0].connected, true);
  assert(states.every((s) => s.busy === false)); feed.stop();
});

test('action supersedes delayed poll JSON, stays busy and blocks duplicates', async () => {
  const delayed = deferred(), post = deferred(); let wait = false, signal; let posts = 0;
  const {feed, timers} = harness(async (url, options) => {
    if (options.method === 'POST') {posts++; return post.promise;}
    if (wait) {wait = false; signal = options.signal; return {...response(null), json: () => delayed.promise};}
    return response(status([car], {discovering: false}));
  });
  await feed.start(); wait = true; const poll = feed.refresh();
  while (!signal) await Promise.resolve();
  const action = feed.action('scan');
  assert.equal(feed.busy, true); assert.equal(signal.aborted, true);
  await feed.action('scan'); assert.equal(posts, 1);
  delayed.resolve(status([adapter], {parked: false})); await poll;
  assert.equal(feed.busy, true); assert.equal(feed.status.parked, true);
  assert.equal(feed.status.devices[0].address, car.address);
  post.resolve(response(status([car], {discovering: true}))); await action;
  assert.equal(feed.busy, false);
  assert.equal([...timers.values()].filter((t) => t.ms === 2000).length, 1); feed.stop();
});

test('failed actions and hard timeouts re-arm polling without accepting stale responses', async () => {
  const delayed = deferred(); let mode = 'ready'; let posts = 0;
  const {feed, timers} = harness(async (url, options) => {
    if (options.method === 'POST') {
      posts++;
      if (mode === 'fail') throw new Error('Connection interrupted');
      return delayed.promise;
    }
    return response(status());
  });
  await feed.start(); mode = 'fail'; await feed.action('scan');
  assert.equal(feed.busy, false); assert.match(feed.error, /interrupted/);
  assert.equal([...timers.values()].filter((t) => t.ms === 2000).length, 1);
  mode = 'timeout'; const action = feed.action('connect', {address: car.address});
  assert.equal(feed.busy, true);
  [...timers.values()].find((t) => t.ms === 30000).fn();
  assert.equal(feed.busy, false); assert.match(feed.error, /too long/);
  const poll = [...timers.values()].find((t) => t.ms === 2000); assert(poll);
  await poll.fn(); assert.equal(feed.error, '');
  delayed.resolve(response(status([adapter], {powered: false}))); await action;
  assert.equal(feed.status.powered, true); assert.equal(feed.status.devices[0].address, car.address);
  assert.equal(posts, 2); feed.stop();
});

test('authorization failures and parked gates prevent backend actions', async () => {
  let code = 200, revoked = 0; const calls = [];
  const {feed, timers} = harness(async (url) => {calls.push(url); return response(status([], {parked: false}), code)}, () => revoked++);
  await feed.start(); await feed.action('power', {enabled: false});
  assert.equal(calls.length, 1);
  code = 401; await feed.refresh();
  assert.equal(revoked, 1); assert.equal(feed.active, false); assert.equal(timers.size, 0);
});

test('stopping cancels pairing and resets device ordering for the next session', async () => {
  const prompt = {id: 'a'.repeat(32), kind: 'confirmation', value: '123456', displayOnly: false};
  let devices = [car, adapter]; const calls = [];
  const {feed} = harness(async (url, options) => {calls.push(options); return response(status(devices,
    {pairing: {address: car.address, state: 'pairing', prompt}}))});
  await feed.start(); feed.stop(true);
  assert(calls.some((options) => options.keepalive && JSON.parse(options.body).operation === 'cancel_pair'));
  devices = [adapter, car]; await feed.start();
  assert.deepEqual(feed.status.devices.map((d) => d.address), [adapter.address, car.address]); feed.stop();
});

test('timed-out background polling preserves status and resumes even when fetch ignores abort', async () => {
  const delayed = deferred(); let wait = false;
  const {feed, timers, states} = harness(async () => wait ? delayed.promise : response(status()));
  await feed.start(); const before = feed.status; wait = true; const pending = feed.refresh();
  [...timers.values()].find((t) => t.ms === 12000).fn();
  assert.equal(feed.status, before); assert.equal(feed.busy, false);
  wait = false; await [...timers.values()].find((t) => t.ms === 2000).fn();
  delayed.resolve(response(status([adapter]))); await pending;
  assert.equal(feed.status.devices[0].address, car.address);
  assert(states.every((s) => s.busy === false)); feed.stop();
});

test('hiding the page keeps a phone pairing window open so the phone can find the comma', async () => {
  const calls = [];
  const {feed} = harness(async (url, options) => {calls.push(options); return response(status([car],
    {discovering: false, pairing: {address: '', state: 'pairing', prompt: null, incoming: true, name: ''}}))});
  await feed.start(); feed.stop(true);
  assert(!calls.some((options) => options?.body && JSON.parse(options.body).operation === 'cancel_pair'));
});
