import assert from 'node:assert/strict'
import { FlmPage } from '../web/js/flm.js'
import { PlotsPage } from '../web/js/plots.js'
import { SettingsFeed } from '../web/js/settings.js'

const vm = { ...FlmPage.data(), activePage: true, mode: 'local', unauthorized() {} }
for (const [name, method] of Object.entries(FlmPage.methods)) vm[name] = method.bind(vm)
for (const [name, method] of Object.entries(FlmPage.computed)) Object.defineProperty(vm, name, { get: () => method.call(vm) })
const calls = []
const snapshot = { page: 'torque', view: 'original-view', subtitle: 'Saved steering tune', rows: [
  { label: 'Use StarPilot Auto Learn', value: 'On', choices: ['Off', 'On'], available: true, action: true },
  { label: 'Lat Accel', value: '2', minimum: 1, maximum: 3, step: .05, available: true, action: true },
  { label: 'Friction', value: '.1', minimum: 0, maximum: .2, step: .01, available: false, action: true },
] }
const timers = new Map()
vm.manualFeed = new SettingsFeed({ publish: update => Object.assign(vm.manualState, update),
  later: fn => { const key = timers.size + 1; timers.set(key, fn); return key }, cancelTimer: id => timers.delete(id),
  fetcher: async (path, options) => {
    calls.push({ path, body: options.body ? JSON.parse(options.body) : null })
    if (path.endsWith('/torque')) return { ok: true, json: async () => structuredClone(snapshot) }
    if (path.endsWith('/preview')) return { ok: true, json: async () => ({ intent: 'actual-settings-intent' }) }
    assert(path.endsWith('/confirm'))
    return { ok: true, json: async () => ({ saved: true, page: { ...structuredClone(snapshot), view: 'saved-view',
      rows: snapshot.rows.map((row, index) => index === 1 ? { ...row, value: '2.1' } : row) } }) }
  } })

await vm.selectView('overview')
assert.equal(calls.length, 0, 'Numeric settings do not poll outside Your tune')
await vm.selectView('tune')
assert.deepEqual(vm.manualRows.map(item => [item.row.label, item.index]), [['Lat Accel', 1], ['Friction', 2]])
assert.equal(vm.manualRows[1].row.available, false, 'Backend unavailable control stays unavailable')
assert.equal(await vm.saveManual(2, .12), false, 'Unavailable controls cannot dispatch edits')
assert.equal(await vm.saveManual(0, 'Off'), false, 'Only numeric rows are editable here')
assert(await vm.saveManual(1, 2.1))
assert.deepEqual(calls[1].body, { view: 'original-view', row: 1, value: 2.1 })
assert.deepEqual(calls[2].body, { intent: 'actual-settings-intent', confirmed: true, page: 'torque' })
assert.equal(vm.manualRows[0].row.value, '2.1', 'Display changes only after saved readback')
assert.equal(calls.length, 3, 'One edit uses existing preview then confirm')
vm.manualState.error = 'Saved settings changed'
assert.equal(await vm.saveManual(1, 2.2), false)
assert.equal(calls.length, 3, 'No writes with stale or failed settings')
await vm.selectView('plots')
assert.equal(vm.manualFeed.active, false)
assert.equal(timers.size, 0, 'Leaving Your tune stops settings timers')
assert.equal(await vm.saveManual(1, 2.3), false)

const oldDocument = globalThis.document
let started = 0, stopped = 0
const paused = []
globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} }
try {
  const page = { active: false, mode: 'local', paused: false, feed: {
    active: false, start() { this.active = true; started++ }, stop() { stopped++ },
    setPaused(value) { paused.push(value) },
  } }
  PlotsPage.mounted.call(page)
  assert.equal(started, 0, 'Hidden plot tab does not fetch')
  page.active = true; PlotsPage.watch.active.call(page)
  assert.equal(started, 1)
  assert.equal(paused.at(-1), false)
  page.active = false; PlotsPage.watch.active.call(page)
  assert.equal(paused.at(-1), true, 'Unselected plot tab pauses polling')
  page.active = true; PlotsPage.watch.active.call(page)
  assert.equal(started, 1, 'Reopening plot tab resumes the existing bounded history')
  assert.equal(paused.at(-1), false)
  PlotsPage.beforeUnmount.call(page)
  assert.equal(stopped, 1)
} finally { globalThis.document = oldDocument }
console.log('Tuning numeric save/readback, stale write refusal, tab polling and retained plot lifecycle passed')
