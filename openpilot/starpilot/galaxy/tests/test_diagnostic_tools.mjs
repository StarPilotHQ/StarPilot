import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { DiagnosticFeed, TroubleshootPage, TmuxPage, validTroubleshoot, validTmux, troubleshootReport } from '../web/js/diagnostic-tools.js'
const report = { schemaVersion: 1, device: { state: 'parked' }, vehicle: { available: true, fingerprint: 'vehicle', brand: 'brand', longitudinal: true, steering: 'torque' }, snapshot: [{ label: 'Build', value: 'candidate' }], sections: [{ title: 'Settings', rows: [{ label: 'Planner', value: 'StarPilot' }] }], note: 'Read only' }
const tail = { available: true, text: 'launcher started\n', reason: '', pane: 'comma:0.0', truncated: false }
assert.equal(validTroubleshoot(report), true)
assert.equal(validTroubleshoot({ ...report, snapshot: [{ label: 'Raw', value: {} }] }), false)
assert.equal(validTmux(tail), true)
assert.equal(validTmux({ ...tail, text: 'a'.repeat(65537) }), false)
assert.equal(validTmux({ ...tail, text: 'é'.repeat(40000) }), false)
assert.equal(validTmux({ ...tail, text: '\n'.repeat(301) }), false)
// The report: car and device, then the settings changed from their defaults with where they live (not every value).
assert.match(troubleshootReport(report), /Build: candidate\n\nChanged settings: unavailable$/)
assert.doesNotMatch(troubleshootReport(report), /Planner: StarPilot/)
assert.match(troubleshootReport(report, []), /Changed settings \(0\)\nNone: every setting uses its default\.$/)
assert.match(troubleshootReport(report, [{ page: 'standard', label: 'Following', current: 'Custom', default: 'StarPilot Default' }]),
  /Changed settings \(1\)\nSpeed › Driving Personalities › Standard › Following: Custom \(default StarPilot Default\)$/)
const updates = [], timers = []
let auth = 0, requests = 0
const feed = new DiagnosticFeed({ endpoint: './api/tmux/live', validate: validTmux, interval: 2000,
  publish: update => updates.push(update), unauthorized: () => auth++,
  later: (fn, ms) => { timers.push({ fn, ms }); return timers.length }, cancel() {},
  fetcher: async () => { requests++; return { ok: true, status: 200, json: async () => tail } } })
await feed.start('sample')
assert.equal(requests, 0)
await feed.start('local')
assert.deepEqual(feed.data, tail)
assert.equal(timers.at(-1).ms, 2000)
await feed.setLive(false)
const count = requests
assert.equal(requests, count)
await feed.refresh()
assert.equal(requests, count + 1) // Manual refresh works while paused.
assert.equal(timers.at(-1).ms, 4000) // Only a request deadline, no new polling timer.
await feed.setLive(true)
assert.equal(timers.at(-1).ms, 2000)
feed.setVisible(false)
await feed.refresh()
const hiddenCount = requests
assert.deepEqual(feed.data, tail)
await feed.setVisible(true)
assert.equal(requests, hiddenCount + 1)
feed.fetcher = async () => { throw new Error('offline') }
await feed.refresh()
assert.deepEqual(feed.data, tail) // A failed poll retains the console.
assert.ok(updates.some(update => update.loading === false))
feed.fetcher = async () => ({ status: 401 })
await feed.refresh()
assert.equal(auth, 1)
assert.equal(feed.active, false)
let resolve
const raceUpdates = []
const race = new DiagnosticFeed({ endpoint: './api/troubleshoot', validate: validTroubleshoot, publish: value => raceUpdates.push(value), later: () => 1, cancel() {}, fetcher: () => new Promise(done => { resolve = done }) })
const reading = race.start('local')
const before = raceUpdates.length
race.setVisible(false)
resolve({ ok: true, status: 200, json: async () => report })
await reading
assert.equal(raceUpdates.length, before)
race.stop()
for (const component of [TroubleshootPage, TmuxPage]) {
  compile(component.template, { decodeEntities: value => value.replaceAll('&amp;', '&') })
  const setup = component.setup({ mode: 'sample', unauthorized() {} })
  await setup.feed.start('sample')
  assert.match(setup.state.error, /sample mode/)
  setup.feed.stop()
}
assert.ok(TroubleshootPage.template.includes("'/logs/monitor'"))
assert.ok(TroubleshootPage.template.includes("'/logs/crashes'"))
assert.ok(TmuxPage.template.includes('launcher console'))
assert.ok(TmuxPage.template.includes('state.data.text'))
console.log('Diagnostic tools: actual Vue templates/setup; read-only report, bounded console, pause/resume/visibility/unmount/auth and stable error content passed')

globalThis.location = { hash: '#/logs', pathname: '/', search: '' }
globalThis.window = { scrollTo() {}, addEventListener() {} }
globalThis.history = {
  state: null,
  replaceState(state, _title, url) { this.state = state; location.hash = url.slice(url.indexOf('#')) },
  pushState(state, _title, url) { this.state = state; location.hash = url.slice(url.indexOf('#')) },
  go() { throw new Error('Unexpected history traversal') },
}
const { Logs } = await import('../web/js/logs.js')
const { route, startRouter } = await import('../web/js/router.js')
startRouter()
Logs.methods.go('/logs/troubleshoot')
assert.equal(route.path, '/logs/troubleshoot')
Logs.methods.go('/logs/tmux')
assert.equal(route.path, '/logs/tmux')
assert.ok(Logs.template.includes('<TroubleshootPage v-else-if='))
assert.ok(Logs.template.includes('<TmuxPage v-else-if='))
compile(Logs.template, { decodeEntities: value => value.replaceAll('&amp;', '&') })
console.log('Logs tiles: actual clickable diagnostic routes and Vue component wiring passed')

let copied = null
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async text => { copied = text } } } })
const copyVm = { state: { notice: '' } }
await TroubleshootPage.methods.copyText.call(copyVm, troubleshootReport(report))
assert.match(copied, /Build: candidate/)
assert.equal(copyVm.state.notice, 'Visible text copied.')
let savedBlob, clicked = false, download
const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL
URL.createObjectURL = blob => { savedBlob = blob; return 'blob:local-fixture' }
URL.revokeObjectURL = () => {}
globalThis.document = { createElement: () => ({ set download(value) { download = value }, click() { clicked = true } }) }
TmuxPage.methods.saveText.call(copyVm, tail.text)
assert.equal(await savedBlob.text(), tail.text)
assert.equal(download, 'starpilot-console.txt')
assert.equal(clicked, true)
URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke
console.log('Diagnostic copy/save: only the visible report or console text exported')
