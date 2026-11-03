import assert from 'node:assert/strict'
import { FlmPage } from '../web/js/flm.js'
import { PlotsPage } from '../web/js/plots.js'
import { SettingsFeed } from '../web/js/settings.js'
import { SURFACE_GROUPS } from '../web/js/flm-training-results.js'

const vm = { ...FlmPage.data(), activePage: true, mode: 'local', unauthorized() {} }
for (const [name, method] of Object.entries(FlmPage.methods)) vm[name] = method.bind(vm)
for (const [name, method] of Object.entries(FlmPage.computed)) Object.defineProperty(vm, name, { get: () => method.call(vm) })
const names = [...new Set(SURFACE_GROUPS.flatMap(group => group.names))]
assert.equal(names.length, 30)
const selected = names.indexOf(vm.selectedGroup.names[0])
const locked = names.indexOf(vm.selectedGroup.names[1])
const snapshot = { page: 'tuning', view: 'original-view', subtitle: 'Parked edits apply next drive', parked: true,
  rows: names.map((coordinate, index) => ({ label: coordinate, coordinate, value: '0',
    minimum: 0, maximum: 25, step: .25, available: index !== locked, action: true })) }
const calls = [], timers = new Map()
let edited = null
vm.manualFeed = new SettingsFeed({ publish: update => Object.assign(vm.manualState, update),
  later: fn => { const key = timers.size + 1; timers.set(key, fn); return key }, cancelTimer: id => timers.delete(id),
  fetcher: async (path, options) => {
    const body = options.body ? JSON.parse(options.body) : null
    calls.push({ path, body })
    if (path.endsWith('/tuning')) return { ok: true, json: async () => structuredClone(snapshot) }
    assert(!path.endsWith('/torque'), 'Tuning does not fetch the duplicate scalar controls')
    if (path.endsWith('/preview')) { edited = body; return { ok: true, json: async () => ({ intent: 'manual-coordinate-intent' }) } }
    assert(path.endsWith('/confirm'))
    return { ok: true, json: async () => ({ saved: true, page: { ...structuredClone(snapshot), view: 'saved-view',
      rows: snapshot.rows.map((row, index) => index === edited.row ? { ...row, value: String(edited.value) } : row) } }) }
  } })
await vm.selectView('overview')
assert.equal(calls.length, 0)
await vm.selectView('tune')
assert.equal(calls[0].path, './api/settings/pages/tuning')
assert.equal(vm.manualRows.length, vm.selectedGroup.names.length, 'Only the selected group is shown')
assert.equal(await vm.saveManual(locked, 1), false, 'Unavailable controls cannot dispatch edits')
assert.equal(await vm.saveManual(selected, 1), true)
assert.deepEqual(calls[1].body, { view: 'original-view', row: selected, value: 1 })
assert.deepEqual(calls[2].body, { intent: 'manual-coordinate-intent', confirmed: true, page: 'tuning' })
assert.equal(vm.manualState.data.rows[selected].value, '1', 'Only confirmed saved readback changes the displayed setting')
assert.equal(vm.manualResetRequested, false, 'Numeric save does not open the reset dialog')
vm.manualState.error = 'Saved settings changed'
assert.equal(await vm.saveManual(selected, 2), false)
assert.equal(calls.length, 3)
vm.manualState.error = ''
const resetIndex = vm.manualState.data.rows.length
vm.manualState.data.rows.push({ label: 'Reset manual tune', value: 'Reset', choices: ['Reset'], confirm: true, available: true, action: true })
await vm.reviewManualReset(resetIndex, 1)
assert.equal(vm.manualResetRequested, true)
assert(vm.manualState.pending, 'Reset is held for explicit confirmation')
assert.deepEqual(calls.at(-1).body, { view: 'saved-view', row: resetIndex, direction: 1 })
vm.cancelManualReset()
assert.equal(vm.manualState.pending, null)
assert.equal(vm.manualResetRequested, false)
assert.equal(vm.manualState.data.rows[selected].value, '1', 'Cancel preserves the saved manual value')
await vm.selectView('plots')
assert.equal(vm.manualFeed.active, false)
assert.equal(timers.size, 0)
assert.equal(await vm.saveManual(selected, 2), false)

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
console.log('Thirty-coordinate editor, saved readback, stale-write refusal and retained plot lifecycle passed')
