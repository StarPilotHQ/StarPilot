import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { NotificationClient, SentryNotifications, validNotifications, channels, alertTypes } from '../web/js/sentry-notifications.js'
const fixture = () => ({ schemaVersion: 1, queueFull: false, subscriptionCount: 0, subscriptions: [],
  alerts: { motion: true, battery: true },
  deliverySemantics: 'Retries may repeat delivery after an interrupted remote response.',
  channels: Object.fromEntries(channels.map(name => [name, { enabled: false, configured: false, pending: 0, lastState: 'idle', lastError: '' }])) })
const status = fixture()
assert.equal(validNotifications(status), true)
assert.equal(validNotifications({ ...status, subscriptionCount: 17 }), false)
assert.equal(validNotifications({ ...status, alerts: { motion: 'yes', battery: true } }), false)
assert.equal(validNotifications({ ...status, alerts: { motion: true } }), false)
assert.equal(validNotifications({ ...status, channels: { ...status.channels, ntfy: { ...status.channels.ntfy, lastError: 'secret-url' } } }), false)
assert.deepEqual(alertTypes.map(item => item.key), ['motion', 'battery'])
assert.match(SentryNotifications.template, /Notifications only/)
assert.match(SentryNotifications.template, /toggleAlert\(alert\.key\)/)
assert.match(SentryNotifications.template, /Send test/)
assert.match(SentryNotifications.template, /Forget configuration/)
assert.match(SentryNotifications.template, /type="password"/)
assert.doesNotMatch(SentryNotifications.template, /v-html/)
const requests = [], states = []; let revoked = 0
const client = new NotificationClient({ publish: value => states.push(value), unauthorized: () => revoked++,
  fetcher: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })) })
const first = client.run({ action: 'test', channel: 'ntfy' })
assert.equal(requests[0].options.credentials, 'same-origin')
assert.equal(JSON.parse(requests[0].options.body).channel, 'ntfy')
requests[0].resolve({ status: 200, ok: true, json: async () => status })
assert.equal(await first, true)
const pollStart = states.length
const stale = client.run()
assert.equal(states.slice(pollStart).some(state => state.notificationBusy === true), false, "passive refresh keeps buttons stable")
client.stop()
requests[1].resolve({ status: 200, ok: true, json: async () => status })
assert.equal(await stale, false)
const unauthorized = client.run()
requests[2].resolve({ status: 401, ok: false, json: async () => ({}) })
assert.equal(await unauthorized, false)
assert.equal(revoked, 1)
const invalid = client.run()
requests[3].resolve({ status: 200, ok: true, json: async () => ({ privateToken: 'bad' }) })
assert.equal(await invalid, false)
assert.match(states.findLast(state => state.notificationError)?.notificationError, /unavailable/)
const worker = readFileSync(new URL('../web/sentry-push-worker.js', import.meta.url), 'utf8')
assert.match(worker, /\.\/\#\/cameras\/events/)
assert.match(worker, /tag: id/)
// Push stays registered, but stale offline app code must no longer intercept navigation.
assert.doesNotMatch(worker, /addEventListener\("fetch"/)
assert.match(worker, /galaxy-offline-v1/)
const handlers = new Map(), removed = []
let claims = 0, activation
runInNewContext(worker, {
  self: { addEventListener: (name, fn) => handlers.set(name, fn),
    clients: { claim: async () => { claims++ } }, skipWaiting() {} },
  caches: { delete: async name => { removed.push(name); return true } },
})
handlers.get('activate')({ waitUntil: promise => { activation = promise } })
await activation
assert.deepEqual(removed, ['galaxy-offline-v1'])
assert.equal(claims, 1)
assert.equal(handlers.has('fetch'), false)
assert.equal(handlers.has('push'), true)
assert.equal(handlers.has('notificationclick'), true)
console.log('Sentry notification browser client and worker checks passed')
