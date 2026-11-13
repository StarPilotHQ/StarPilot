import assert from 'node:assert/strict'
import { MapOperationsClient, MapOperationsPanel, validCatalog, validOperation } from '../web/js/map-operations.js'
import { NavigationPage } from '../web/js/navigation.js'

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
const region = { token: 'us_state.IL', name: 'Illinois', bounds: [36, -92, 44, -86], groups: 12, available: true }
const unsupported = { token: 'nation.bad', name: 'Unsupported', bounds: [0, 0, 0, 0], groups: 0,
  available: false, unavailable: 'invalid_bounds' }
const catalog = { regions: [region, unsupported], maxGroups: 64, maxTransferBytes: 8589934592,
  maxNewDiskBytes: 17179869184, selectedGeneration: '' }
const status = { schemaVersion: 1, ownerSession: 's1', operationId: null, state: 'idle', regionToken: null,
  bounds: null, completedGroups: 0, totalGroups: 0, transferredBytes: 0, transferBudgetBytes: 0,
  preparedGeneration: null, selectedGeneration: '', errorCode: null, selectedForNextShadowStart: false }
assert.equal(validCatalog(catalog), true)
const largeCatalog = { ...catalog, maxGroups: 1024, regions: [
  { ...region, token: 'nation.CA', name: 'Canada', bounds: [40, -142, 74, -52], groups: 765 },
  { ...region, token: 'nation.JP', name: 'Japan', bounds: [30, 128, 46, 146], groups: 72 },
] }
assert.equal(validCatalog(largeCatalog), true)
assert.equal(validCatalog({ ...largeCatalog, regions: [{ ...region, groups: 1025 }] }), false)
assert.equal(validCatalog({ ...catalog, regions: [{ ...region, available: false, unavailable: 'too_large' }] }), true)
assert.equal(validCatalog({ ...catalog, regions: [{ ...region, token: '<script>' }] }), false)
assert.equal(validOperation(status), true)
assert.equal(validOperation({ ...status, operationId: 's1:1', state: 'transferring' }), true)
assert.equal(NavigationPage.components.MapOperationsPanel, MapOperationsPanel)
assert.match(MapOperationsPanel.template, /Start download/)
assert.match(MapOperationsPanel.template, /Add Offline Coverage/)
assert.match(MapOperationsPanel.template, /Existing regions are kept/)
assert.doesNotMatch(MapOperationsPanel.template, /Replace Selected Map|replaces the selected map/)
assert.doesNotMatch(MapOperationsPanel.template, /Retry maps/)
assert.match(MapOperationsPanel.template, /next map service start/)
assert.doesNotMatch(MapOperationsPanel.template, /v-html/)
const panel = { $data: {}, review: { region }, unauthorized: () => { panel.revoked = true } }
MapOperationsPanel.created.call(panel)
panel.client.emit({ error: 'Map manager unavailable' })
assert.equal(panel.review, null)
panel.review = { region }
panel.client.unauthorized()
assert.equal(panel.review, null)
assert.equal(panel.revoked, true)

function fixture() {
  const requests = [], publishes = [], timers = new Map()
  let sequence = 0, unauthorized = 0
  const client = new MapOperationsClient({ publish: (state) => publishes.push(state), unauthorized: () => unauthorized++,
    fetcher: (url, options) => url.endsWith('/setup') ? Promise.resolve({ ok: true, status: 200, json: async () => ({ schemaVersion: 1, packageReady: true, packageState: 'ready', snapshotReady: false, selectedGeneration: '', parked: true, freeDiskBytes: 2147483648, maxTransferBytes: 8589934592, maxNewDiskBytes: 17179869184 }) }) : new Promise((resolve) => requests.push({ url, options, resolve })),
    later: (fn, ms) => { timers.set(++sequence, { fn, ms }); return sequence }, cancelTimer: (id) => timers.delete(id) })
  async function reply(index, body, code = 200) {
    requests[index].resolve({ ok: code === 200, status: code, json: async () => body })
    await flush()
  }
  return { client, requests, publishes, timers, reply, get unauthorized() { return unauthorized } }
}
const normal = fixture()
normal.client.start()
assert.deepEqual(normal.requests.map((r) => r.url), ['./api/maps/catalog', './api/maps/operation'])
assert.deepEqual([...normal.timers.values()].map((timer) => timer.ms), [10000, 10000, 10000])
await normal.reply(0, catalog)
await normal.reply(1, status)
assert.equal(normal.client.catalog.regions.length, 2)
const starting = normal.client.startRegion(region, '', 's1')
assert.equal(normal.requests[2].url, './api/maps/start')
assert.deepEqual(JSON.parse(normal.requests[2].options.body), { regionToken: region.token,
  maxTransferBytes: catalog.maxTransferBytes, maxNewDiskBytes: catalog.maxNewDiskBytes, expectedCurrentGeneration: '' })
await normal.reply(2, { ...status, operationId: 's1:1', state: 'transferring', regionToken: region.token,
  totalGroups: 12, transferBudgetBytes: catalog.maxTransferBytes })
// Acknowledged start is not marked complete. A fresh owner read follows it.
assert.equal(normal.client.operation.state, 'transferring')
assert.equal(normal.requests[3].url, './api/maps/operation')
await normal.reply(3, normal.client.operation)
await starting
const canceling = normal.client.cancel()
assert.equal(normal.requests[4].url, './api/maps/cancel')
assert.deepEqual(JSON.parse(normal.requests[4].options.body), { operationId: 's1:1' })
await normal.reply(4, { ...normal.client.operation, state: 'canceled' })
await normal.reply(5, { ...normal.client.operation, state: 'canceled' })
await canceling
normal.client.stop()
assert.equal(normal.timers.size, 0)

const changed = fixture()
changed.client.start()
await changed.reply(0, catalog)
await changed.reply(1, { ...status, selectedGeneration: 'a'.repeat(64) })
await changed.client.startRegion(region, '', 's1')
assert.equal(changed.requests.length, 2)
assert.match(changed.publishes.at(-1).error, /changed/)

const revoked = fixture()
revoked.client.start()
await revoked.reply(0, catalog)
await revoked.reply(1, status)
const pending = revoked.client.startRegion(region, '', 's1')
await revoked.reply(2, {}, 401)
await pending
assert.equal(revoked.unauthorized, 1)
assert.equal(revoked.client.operation, null)
assert.equal(revoked.timers.size, 0)
assert.equal(revoked.client.operation, null)

const setupRequired = fixture()
setupRequired.client.start()
await setupRequired.reply(0, { code: 'setup_required' }, 503)
assert.equal(setupRequired.unauthorized, 1)
assert.equal(setupRequired.client.catalog, null)
await setupRequired.reply(1, status)
assert.equal(setupRequired.client.operation, null)

const partial = fixture()
partial.client.start()
await partial.reply(0, { code: 'owner_unavailable' }, 503)
await partial.reply(1, status)
assert.match(partial.publishes.at(-1).error, /starting up|Reconnecting/)
assert.equal(partial.client.catalog, null)
partial.client.stop()

const missingPackage = fixture()
missingPackage.client.start()
await missingPackage.reply(0, { code: 'package_unavailable' }, 503)
await missingPackage.reply(1, status)
assert.match(missingPackage.publishes.at(-1).error, /Offline map package is unavailable/)
assert.equal(missingPackage.client.catalog, null)
missingPackage.client.stop()

const restarted = fixture()
restarted.client.start()
await restarted.reply(0, catalog)
await restarted.reply(1, status)
const oldMutation = restarted.client.startRegion(region, '', 's1')
restarted.client.stop()
restarted.client.start()
await restarted.reply(3, catalog)
await restarted.reply(4, status)
await restarted.reply(2, { ...status, state: 'completed', selectedGeneration: 'b'.repeat(64),
  selectedForNextShadowStart: true })
await oldMutation
assert.equal(restarted.client.operation.state, 'idle')
assert.equal(restarted.client.busy, false)
assert.equal(restarted.requests.length, 5)

const stale = fixture()
stale.client.start()
await stale.reply(0, catalog)
stale.client.stop()
await stale.reply(1, status)
assert.equal(stale.client.operation, null)
assert.equal(stale.timers.size, 0)

for (const [code, phrase] of [
  ["busy", "Another map download"], ["selection_changed", "selected map changed"],
  ["not_parked", "fresh parked status"], ["operation_changed", "download changed"],
  ["unknown", "could not be completed"], ["toString", "could not be completed"],
]) {
  const rejected = fixture()
  rejected.client.start()
  await rejected.reply(0, catalog)
  await rejected.reply(1, status)
  const attempt = rejected.client.startRegion(region, '', 's1')
  await rejected.reply(2, { code, error: "untrusted server detail" }, 409)
  await rejected.reply(3, status)
  await attempt
  assert.ok(rejected.client.actionError.includes(phrase), code)
  assert.ok(!rejected.client.actionError.includes("untrusted server detail"))
  assert.equal(rejected.client.busy, false)
  rejected.client.stop()
}
const expiredConflict = fixture()
expiredConflict.client.start()
await expiredConflict.reply(0, catalog)
await expiredConflict.reply(1, status)
const lateStart = expiredConflict.client.startRegion(region, '', 's1')
let bodyReady
expiredConflict.requests[2].resolve({ status:409, ok:false, json:()=>new Promise(resolve=>{bodyReady=resolve}) })
await flush()
expiredConflict.client.stop()
bodyReady({code:'not_parked'})
await lateStart
assert.equal(expiredConflict.client.actionError, '')
assert.equal(expiredConflict.client.active, false)
console.log('Map operation conflicts: distinct reasons and expired response isolation passed')
