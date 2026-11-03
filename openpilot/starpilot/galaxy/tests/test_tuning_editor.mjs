import assert from 'node:assert/strict'
import { FlmPage } from '../web/js/flm.js'
import { PlotsPage } from '../web/js/plots.js'

const vm = { ...FlmPage.data() }
for (const [name, method] of Object.entries(FlmPage.methods)) vm[name] = method.bind(vm)
vm.selectView('tune')
assert.equal(vm.view, 'tune')
assert.equal(vm.manualFeed, undefined, 'Tuning does not start the duplicate torque-settings feed')
assert(!FlmPage.template.includes('GalaxySettingRow'), 'Lat Accel and Friction stay in Toggles')
vm.selectView('plots')
let focused = -1
vm.tabKey({ key: 'Home', preventDefault() {}, currentTarget: { parentElement: {
  querySelectorAll: () => ['overview', 'tune', 'activity', 'plots'].map((_, index) => ({ focus() { focused = index } })),
} } })
assert.equal(vm.view, 'overview')
assert.equal(focused, 0)
vm.selectView('unsupported')
assert.equal(vm.view, 'overview')

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
console.log('Tuning navigation, duplicate control removal and retained plot lifecycle passed')
