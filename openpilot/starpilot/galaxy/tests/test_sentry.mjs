import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { SentryEventsFeed, SentryEventsPage, validSentryEvents } from '../web/js/sentry.js'
import { CamerasPage } from '../web/js/cameras.js'
const cameraTools = JSON.parse(readFileSync(new URL('../web/data/catalog.json', import.meta.url), 'utf8'))
  .tools.find(tool => tool.path === '/cameras').children

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
const eventId = 'a'.repeat(32)
const inventory = { schemaVersion: 1, source: 'local', scanIncomplete: false, capacity: 512,
  events: [{ eventId, kind: 'warning', systemTimeMs: 1_700_000_000_000 }] }
assert.equal(validSentryEvents(inventory), true)
assert.equal(validSentryEvents({ ...inventory, events: [{ ...inventory.events[0], images: ['wide', 'cabin'] }] }), true)
assert.equal(validSentryEvents({ ...inventory, events: [{ ...inventory.events[0], images: ['../../secret'] }] }), false)
assert.equal(validSentryEvents({ ...inventory, events: [{ ...inventory.events[0], kind: 'armed' }] }), false)
assert.equal(validSentryEvents({ ...inventory, events: [{ ...inventory.events[0], kind: 'battery', voltage: 120, cutoff: 118 }] }), true)
assert.equal(validSentryEvents({ ...inventory, events: [{ ...inventory.events[0], kind: 'battery' }] }), false)
assert.equal(validSentryEvents({ ...inventory, events: [{ ...inventory.events[0], voltage: 120, cutoff: 118 }] }), false)
assert.equal(validSentryEvents({ ...inventory, events: [{ ...inventory.events[0], path: '/secret' }] }), false)
assert.equal(validSentryEvents({ ...inventory, events: [inventory.events[0], inventory.events[0]] }), false)
assert.equal(validSentryEvents({ ...inventory, source: 'remote' }), false)
assert.match(SentryEventsPage.template, /device clock and may be inaccurate/)
assert.match(SentryEventsPage.template, /<(?:img|BalancedImage)[^>]*event.images[^>]*api\/sentry\/image\//)
assert.doesNotMatch(SentryEventsPage.template, /v-html|deleteEvent|Download/)
assert.match(CamerasPage.template, /MenuTile v-for="tool in tools"/)
assert.match(cameraTools.find(tool => tool.path === "/cameras/events").description, /View motion events/)
const app = readFileSync(new URL('../web/js/app.js', import.meta.url), 'utf8')
assert.match(app, /SentryEventsPage[^>]*\/cameras\/events[^>]*\/cameras\/sentry-settings/)

function fixture() {
  const requests = [], states = [], timers = new Map()
  let next = 0, revoked = 0
  const feed = new SentryEventsFeed({ publish: (state) => states.push(state), unauthorized: () => revoked++,
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })),
    later: (fn, ms) => { timers.set(++next, { fn, ms }); return next }, cancelTimer: (id) => timers.delete(id) })
  async function reply(index, body, code = 200) {
    requests[index].resolve({ status: code, ok: code === 200, json: async () => body })
    await flush()
  }
  return { feed, requests, states, timers, reply, get revoked() { return revoked } }
}
const normal = fixture()
normal.feed.start()
assert.equal(normal.requests[0].url, './api/sentry/events')
assert.equal(normal.requests[0].options.credentials, 'same-origin')
await normal.reply(0, inventory)
assert.equal(normal.feed.status, 'ready')
await flush()
assert.equal(normal.requests.length, 1, 'no repeated storage scan without manual refresh')
normal.feed.load()
await normal.reply(1, { ...inventory, events: [] })
assert.equal(normal.feed.data.events.length, 0)
normal.feed.stop()
assert.equal(normal.feed.data, null)

const auth = fixture()
auth.feed.start()
await auth.reply(0, { code: 'setup_required' }, 503)
assert.equal(auth.revoked, 1)
assert.equal(auth.feed.data, null)
const loggedOut = fixture()
loggedOut.feed.start()
await loggedOut.reply(0, {}, 401)
assert.equal(loggedOut.revoked, 1)
const unavailable = fixture()
unavailable.feed.start()
await unavailable.reply(0, { code: 'backend_unavailable' }, 503)
assert.equal(unavailable.revoked, 0)
assert.equal(unavailable.feed.status, 'unavailable')

const timedOut = fixture()
timedOut.feed.start()
assert.deepEqual([...timedOut.timers.values()].map((timer) => timer.ms), [10000, 4000])
const [timeoutId, timeout] = [...timedOut.timers.entries()].find(([, timer]) => timer.ms === 4000)
timedOut.timers.delete(timeoutId)
timeout.fn()
assert.equal(timedOut.feed.status, 'unavailable')
timedOut.feed.load()
assert.equal(timedOut.requests.length, 2)
await timedOut.reply(1, inventory)
await timedOut.reply(0, { ...inventory, events: [] })
assert.equal(timedOut.feed.data.events.length, 1)

const lateBody = fixture()
lateBody.feed.start()
let resolveBody
lateBody.requests[0].resolve({ status: 200, ok: true, json: () => new Promise((resolve) => { resolveBody = resolve }) })
await flush()
lateBody.feed.stop()
lateBody.feed.start()
await lateBody.reply(1, inventory)
resolveBody({ ...inventory, events: [] })
await flush()
assert.equal(lateBody.feed.data.events.length, 1)

const priorDocument = globalThis.document
const listeners = new Map()
globalThis.document = { hidden: true, addEventListener: (name, cb) => listeners.set(name, cb),
  removeEventListener: (name, cb) => { if (listeners.get(name) === cb) listeners.delete(name) } }
const page = { mode: 'local', $data: { status: 'idle', data: null, error: '' }, unauthorized() { throw new Error('revoked') } }
SentryEventsPage.created.call(page)
const pending = []
page.feed.fetcher = (url, options) => new Promise((resolve) => pending.push({ url, options, resolve }))
SentryEventsPage.mounted.call(page)
assert.equal(pending.length, 0)
document.hidden = false
listeners.get('visibilitychange')()
assert.equal(pending.length, 1)
document.hidden = true
listeners.get('visibilitychange')()
assert.equal(page.$data.data, null)
SentryEventsPage.beforeUnmount.call(page)
assert.equal(listeners.size, 0)
globalThis.document = priorDocument
