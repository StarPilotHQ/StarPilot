import assert from 'node:assert/strict'
import { settingLevel, settingVisible, saveSettingsMode } from '../web/js/settings-levels.js'
import { SettingsPage, SETTINGS_SECTIONS } from '../web/js/settings.js'

const originalFetch = globalThis.fetch
const requests = []
globalThis.fetch = async (url, request) => {
  requests.push({ url, request })
  return { ok: true, json: async () => ({ mode: JSON.parse(request.body).mode }) }
}
try {
  assert.equal(await saveSettingsMode('advanced'), 'advanced')
  assert.equal(requests[0].url, './api/settings/mode')
  assert.deepEqual(JSON.parse(requests[0].request.body), { mode: 'advanced' })
  assert.equal(requests[0].request.credentials, 'same-origin')
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ error: 'Save failed' }) })
  await assert.rejects(saveSettingsMode('simple'), /Save failed/)
} finally { globalThis.fetch = originalFetch }

const section = SETTINGS_SECTIONS.find(section => section.id === 'lateral')
const rows = [
  { id: 'lane_change:enabled', label: 'Enable', value: 'On' },
  { id: 'lane_change:speed', label: 'Speed', value: '42', level: 'advanced' },
  { id: 'lane_change:automatic_turn_initiation', label: 'Experiment', value: 'Off', level: 'advanced' },
]
const state = { query: '', data: { page: 'lane_change', rows }, settingsPreference: { mode: 'simple' } }
const view = { state, initialPage: 'hub', activeSection: section }
const entries = () => SettingsPage.computed.visibleRows.call(view)
assert.deepEqual(entries().map(entry => [entry.index, entry.visible]), [[0, true], [1, false], [2, false]])
state.settingsPreference.mode = 'advanced'
assert.deepEqual(entries().map(entry => [entry.index, entry.visible]), [[0, true], [1, true], [2, true]])
assert.equal(state.data.rows, rows)
assert.equal(rows[1].value, '42')
// Owners alone classify rows. Names, route prefixes and IDs never imply a level or create links.
for (const id of ['lane_change:speed', 'AlphaLongitudinalEnabled', 'ExperimentalSpeedNudge']) assert.equal(settingLevel({ id }), 'simple')
assert.equal(settingLevel({ level: 'unknown' }), 'simple')
state.data = { page: 'hub', rows: [{ page: 'lane_change', label: 'Lane Changes' }] }
assert.deepEqual(entries().map(entry => entry.row.page), ['lane_change'])
const device = SETTINGS_SECTIONS.find(section => section.id === 'device')
state.data = { page: 'hub', rows: [{ page: 'power', level: 'advanced' }] }
view.activeSection = device
state.settingsPreference.mode = 'simple'
assert.equal(entries().find(entry => entry.row.page === 'power').visible, false, 'static navigation preserves the owner-declared level')

assert.equal(settingVisible({ levels: [] }, true), false)
assert.equal(settingVisible({ levels: ['advanced'] }, false), false)
assert.equal(settingVisible({ levels: ['advanced', 'simple'] }, false), true)
const tabView = { sections: [section], state: { pageLevels: Object.fromEntries(section.pages.map(page => [page, ['advanced']])) }, advancedMode: false }
assert.deepEqual(SettingsPage.computed.visibleSections.call(tabView), [])
tabView.advancedMode = true
assert.deepEqual(SettingsPage.computed.visibleSections.call(tabView), [section])
tabView.advancedMode = false
tabView.state.pageLevels.aol = ['simple', 'advanced']
assert.deepEqual(SettingsPage.computed.visibleSections.call(tabView), [section])
let selected
const selection = { state: { data: { page: 'hub' } }, busy: false, activeSection: section,
  visibleSections: [{ id: 'wheel' }], selectSection: section => { selected = section.id } }
SettingsPage.methods.ensureVisibleSelection.call(selection)
assert.equal(selected, 'wheel')

// A page read dispatched before a mode save must not restore the stale device preference.
const { SettingsFeed } = await import('../web/js/settings.js')
const { state: modeState } = SettingsPage.setup({ initialPage: 'hub', initialSection: null, unauthorized() {} })
let replyPage
const feed = new SettingsFeed({ publish() {}, fetcher: () => new Promise(resolve => { replyPage = resolve }) })
feed.active = true
const stalePage = feed.load('lane')
const restoreFetch = globalThis.fetch
globalThis.fetch = async () => ({ ok: true, json: async () => ({ mode: 'advanced' }) })
try {
  await SettingsPage.methods.setSettingsMode.call({ state: modeState, busy: false, unauthorized() {} }, 'advanced')
  replyPage({ ok: true, status: 200, json: async () => ({ page: 'lane', settingsMode: 'simple', rows: [] }) })
  await stalePage
  assert.equal(modeState.settingsPreference.mode, 'advanced')
} finally { feed.stop(); globalThis.fetch = restoreFetch }
