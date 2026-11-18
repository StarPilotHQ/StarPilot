import assert from "node:assert/strict"
import { compile } from "../web/vendor/vue/vue.esm-browser.js"
import { LocalAccess, LocalAccessFeed, validLocalAccess } from "../web/js/local-access.js"
import { Home } from "../web/js/home.js"
import { GalaxyPage } from "../web/js/galaxy.js"
const snapshot = { available: true, addresses: [{ interface: 'wlan0', label: 'Wi-Fi', url: 'http://192.168.20.5:8082/' }], reason: '' }
assert.equal(validLocalAccess(snapshot), true)
assert.equal(validLocalAccess({ available: false, addresses: [], reason: 'No active addresses' }), true)
assert.equal(validLocalAccess({ ...snapshot, addresses: [{ ...snapshot.addresses[0], url: 'javascript:alert(1)' }] }), false)
assert.equal(validLocalAccess({ ...snapshot, addresses: [{ ...snapshot.addresses[0], url: 'http://password@example.com' }] }), false)
assert.equal(validLocalAccess({ ...snapshot, available: false }), false)
const updates = [], requests = [], timers = []
let auth = 0
const feed = new LocalAccessFeed({ publish: value => updates.push(value), onUnauthorized: () => auth++,
  later: (fn, ms) => { timers.push({ fn, ms }); return timers.length }, cancel() {},
  fetcher: async (url, options) => { requests.push({ url, options }); return { ok: true, status: 200, json: async () => snapshot } } })
await feed.start('sample')
await feed.refresh()
assert.equal(requests.length, 0)
assert.match(updates.at(-1).error, /preview/)
await feed.start('local')
assert.equal(requests.length, 1)
assert.equal(requests[0].url, './api/local-access')
assert.equal(requests[0].options.credentials, 'same-origin')
assert.equal(timers.at(-1).ms, 4000)
assert.deepEqual(updates.at(-2).data, snapshot)
assert.equal(timers.length, 2) // Background synchronization plus a bounded request.
await feed.refresh()
assert.equal(requests.length, 2)
feed.fetcher = async () => ({ status: 401 })
await feed.refresh()
assert.equal(auth, 1)
assert.equal(feed.active, false)
let resolve
const pending = new LocalAccessFeed({ publish: value => updates.push(value), later: () => 1, cancel() {},
  fetcher: () => new Promise(done => { resolve = done }) })
const reading = pending.start('local')
const count = updates.length
pending.stop()
resolve({ ok: true, status: 200, json: async () => snapshot })
await reading
assert.equal(updates.length, count) // Unmounted response cannot publish.
let deadline
const timeoutUpdates = []
const bounded = new LocalAccessFeed({ publish: value => timeoutUpdates.push(value), later: fn => { deadline = fn; return 1 }, cancel() {},
  fetcher: (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error(), { name: 'AbortError' })))) })
const boundedRead = bounded.start('local')
assert.equal(await bounded.refresh(), undefined) // Duplicate refresh is suppressed.
deadline()
await boundedRead
assert.match(timeoutUpdates.at(-2).error, /Reconnecting/)
assert.equal(timeoutUpdates.at(-1).loading, false)
bounded.stop()
compile(LocalAccess.template, { decodeEntities: value => value })
const setup = LocalAccess.setup({ mode: 'sample', onUnauthorized() {} })
await setup.feed.start('sample')
assert.match(setup.state.error, /preview/)
setup.feed.stop()
assert.equal(Home.components.LocalAccess, LocalAccess)
assert.ok(Home.template.includes(':on-unauthorized="unauthorized"'))
assert.ok(LocalAccess.template.includes('Connect your phone to the Galaxy hotspot'))
assert.ok(LocalAccess.template.includes('full http:// address'))
assert.ok(LocalAccess.template.includes('Your Firestar Link uses Internet instead.'))
console.log('Local access: actual Vue component/setup, bounded automatically synchronized read, sample exclusion, authentication, safe links and unmount race passed')

assert.equal(GalaxyPage.components.LocalAccess, undefined)
assert.ok(!GalaxyPage.template.includes('<LocalAccess'))
compile(GalaxyPage.template, { decodeEntities: value => value.replaceAll('&amp;', '&') })
console.log('Galaxy local access remains independent of app installation')
