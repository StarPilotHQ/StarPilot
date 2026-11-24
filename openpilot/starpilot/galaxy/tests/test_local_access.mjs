import assert from "node:assert/strict"
import { compile } from "../web/vendor/vue/vue.esm-browser.js"
import { ConnectionLogs, LocalAccess, LocalAccessFeed, validLocalAccess } from "../web/js/local-access.js"
import { Home } from "../web/js/home.js"
import { RemoteQR } from "../web/js/remote-access.js"
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

const capture = { onUnauthorized() { assert.fail('unexpected sign-in request') },
  ...ConnectionLogs.data(), ...ConnectionLogs.methods }
const originalFetch = globalThis.fetch
try {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, './api/local-access/capture')
    assert.equal(options.credentials, 'same-origin')
    return { ok: true, json: async () => ({ schemaVersion: 1, zipBase64: 'UEsDBAAAAAAAAAAAAAAAAAAAAAAAAAAA' }) }
  }
  await capture.captureConnectionLogs()
  assert.equal(capture.capture.type, 'application/zip')
  assert.equal(capture.captureRequest, null)
  capture.resetCapture()
  assert.equal(capture.capture, null)
} finally { globalThis.fetch = originalFetch }
const thread = { ...ConnectionLogs.data(), ...ConnectionLogs.methods, supportCode: 'AB12 CD34-EF56' }
try {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, './api/companion/support-ticket')
    const { action, code } = JSON.parse(options.body)
    assert.equal(code, 'ab12cd34ef56')
    const body = action === 'check' ? { title: 'Lost network', kind: 'connection' } : { sent: true }
    return { ok: true, json: async () => body }
  }
  await thread.threadAction('check')
  assert.equal(thread.supportTicket.title, 'Lost network')
  assert.equal(thread.supportSent, false)
  await thread.threadAction('send')
  assert.equal(thread.supportSent, true)
} finally { globalThis.fetch = originalFetch }
assert.equal(LocalAccess.components.ConnectionLogs, ConnectionLogs)
assert.ok(LocalAccess.template.includes("mode === 'local'"))
compile(ConnectionLogs.template, { decodeEntities: value => value })
assert.ok(ConnectionLogs.template.includes('Send to existing Discord thread'))
assert.ok(ConnectionLogs.components.ReportingGuide.template.includes('#submit-logs-through-discord'))
assert.ok(ConnectionLogs.components.ReportingGuide.template.includes('#report-a-problem-through-discord'))
assert.match(ConnectionLogs.template, /<details[^>]*><summary><small>Manual ZIP download<\/small><\/summary>[\s\S]*Download ZIP<\/button>\s*<\/details>/)
for (const endpoint of ['./api/galaxy/qr.svg', './api/galaxy/session/qr.svg']) {
  const qr = {endpoint, ...RemoteQR.data(), ...RemoteQR.methods}
  try {
    globalThis.fetch = async (path, options) => {
      assert.equal(path, endpoint)
      assert.equal(options.credentials, 'same-origin')
      return new Response('<svg xmlns="http://www.w3.org/2000/svg"/>',{headers:{'Content-Type':'image/svg+xml'}})
    }
    await qr.load()
    assert.match(qr.image, /^blob:/)
    assert.equal(qr.failed, false)
    assert.equal((await originalFetch(qr.image)).headers.get('content-type'), 'image/svg+xml')
    RemoteQR.beforeUnmount.call(qr)
    await assert.rejects(originalFetch(qr.image))
  } finally { globalThis.fetch = originalFetch }
}
