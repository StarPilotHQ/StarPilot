import assert from 'node:assert/strict'
import { PlotsFeed, PlotsPage, PlotGraph, MAX_POINTS, appendPlotSample, chartGeometry, matchQuality, seriesPoints, validatePlotPayload } from '../web/js/plots.js'

const values = () => ({ desiredLateralAccel: .2, actualLateralAccel: .18, desiredLongitudinalAccel: .3,
  actualLongitudinalAccel: .28, lateralP: .1, lateralI: .02, lateralD: null, lateralF: .08,
  longitudinalP: .1, longitudinalI: .1, longitudinalF: .1, speedMps: 12,
  controlsActive: true, lateralControlActive: true, longitudinalControlActive: true, controlsFresh: true, poseFresh: true,
  speedSource: 'deviceMotion',
  lateralSource: 'torqueState', longitudinalSource: 'aTarget', lateralTermsSource: 'torqueState',
  longitudinalTermsSource: 'controlsState' })
const payload = (index, extra = {}) => ({ schemaVersion: 1, sessionId: 'ab'.repeat(8), state: 'current', sampleIndex: index,
  sampleAgeSeconds: .1, values: values(), error: '', bootStabilizing: false, ...extra })
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fixture() {
  const requests = [], states = [], timers = new Map()
  let now = 1000, timerIndex = 0, unauthorized = 0
  const feed = new PlotsFeed({ publish: (state) => states.push(state), unauthorized: () => unauthorized++,
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, signal: options.signal, resolve })),
    now: () => now,
    later: (fn, delay) => { timers.set(++timerIndex, { fn, due: now + delay }); return timerIndex },
    cancel: (id) => timers.delete(id) })
  const reply = async (index, body, status = 200) => {
    requests[index].resolve({ ok: status === 200, status, json: async () => body })
    await flush()
  }
  const advance = (milliseconds) => {
    const end = now + milliseconds
    while (true) {
      const next = [...timers.entries()].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0]
      if (!next) break
      now = next[1].due; timers.delete(next[0]); next[1].fn()
    }
    now = end
  }
  return { feed, states, requests, reply, advance, timers, get unauthorized() { return unauthorized } }
}

assert.equal(validatePlotPayload(payload(1)).values.lateralSource, 'torqueState')
assert.throws(() => validatePlotPayload(payload(1, { values: { ...values(), actualLateralAccel: Infinity } })))
assert.throws(() => validatePlotPayload(payload(1, { values: { ...values(), poseFresh: false } })))
assert.throws(() => validatePlotPayload(payload(1, { state: 'stale' })))
let history = []
for (let i = 1; i <= 260; i++) history = appendPlotSample(history, payload(i), i * 750)
assert.equal(history.length, MAX_POINTS)
assert.equal(history[0].index, 21)

// Optional source ages must not move the shared observation clock backwards.
let variedAge = []
for (const [i, age] of [.1, 1.2, .1, .1, .1].entries()) {
  variedAge = appendPlotSample(variedAge, validatePlotPayload(payload(i + 1, { sampleAgeSeconds: age })), 1000 + i * 750)
}
assert.deepEqual(variedAge.map(sample => sample.at), [1000, 1750, 2500, 3250, 4000])
assert.deepEqual(chartGeometry(variedAge, ['desiredLateralAccel', 'actualLateralAccel']).paths.map(paths => paths.length), [1, 1])
const missingObservedInterval = variedAge.map((sample, index) => ({ ...sample, at: sample.at + (index >= 2 ? 2000 : 0) }))
assert.deepEqual(chartGeometry(missingObservedInterval, ['desiredLateralAccel', 'actualLateralAccel']).paths.map(paths => paths.length), [2, 2])
const unavailableSignal = variedAge.map((sample, index) => index === 2 ? { ...sample, actualLateralAccel: null } : sample)
assert.deepEqual(chartGeometry(unavailableSignal, ['desiredLateralAccel', 'actualLateralAccel']).paths.map(paths => paths.length), [1, 2])

assert.equal(appendPlotSample(history, payload(260), 10000).length, MAX_POINTS)
const restarted = appendPlotSample(history, payload(260, { sessionId: 'cd'.repeat(8) }), 10000)
assert.equal(restarted.length, 1)
assert.equal(restarted[0].sessionId, 'cd'.repeat(8))
assert.equal(matchQuality(history, 'desiredLateralAccel', 'actualLateralAccel', { minSpeed: .5,
  minDemand: .008, great: .15, good: .30, fair: .50 }).label, 'Great')
assert.equal(matchQuality([{ ...values(), at: 1, controlsActive: false }], 'desiredLateralAccel',
  'actualLateralAccel', { great: .15, good: .30, fair: .50 }).label, 'N/A')
const aolHistory = Array.from({ length: 8 }, (_, i) => ({ ...values(), at: i * 750, controlsActive: false,
  lateralControlActive: true, longitudinalControlActive: false }))
assert.equal(matchQuality(aolHistory, 'desiredLateralAccel', 'actualLateralAccel', { minSpeed: .5,
  minDemand: .008, great: .15, good: .30, fair: .50, activeKey: 'lateralControlActive' }).label, 'Great')
assert.equal(matchQuality(aolHistory, 'desiredLongitudinalAccel', 'actualLongitudinalAccel', { minDemand: .05,
  great: .32, good: .52, fair: .78, activeKey: 'longitudinalControlActive' }).label, 'N/A')
assert.equal(seriesPoints(history, ['desiredLateralAccel', 'actualLateralAccel']).length, 2)
assert.deepEqual(seriesPoints([{ at: 1, desiredLateralAccel: null }, { at: 2, desiredLateralAccel: .1 }],
  ['desiredLateralAccel']), [[]]) // Null is a gap, never a zero line.
assert.equal(seriesPoints([{ at: 1, desiredLateralAccel: .1 }, { at: 2, desiredLateralAccel: null },
  { at: 3, desiredLateralAccel: .2 }], ['desiredLateralAccel'])[0].length, 0) // Do not bridge missing evidence.
const shortChart = chartGeometry([{ at: 1000, desiredLateralAccel: -.25 },
  { at: 2500, desiredLateralAccel: .5 }], ['desiredLateralAccel'])
assert.equal(shortChart.top, '0.50')
assert.equal(shortChart.bottom, '-0.25')
assert.equal(shortChart.from, '−1.5 s')
assert.ok(shortChart.zeroY > 18 && shortChart.zeroY < 132)
assert.equal(chartGeometry([], ['desiredLateralAccel']).valid, false)

const live = fixture()
live.feed.start()
assert.equal(live.requests[0].url, './api/plots/live')
await live.reply(0, payload(1))
assert.equal(live.states.at(-1).status, 'current')
assert.equal(live.states.at(-1).history.length, 1)
live.advance(750)
assert.equal(live.requests.length, 2)
await live.reply(1, payload(2))
assert.equal(live.states.at(-1).history.length, 2)
live.advance(750)
await live.reply(2, payload(2)) // same producer sample cannot grow history
assert.equal(live.states.at(-1).history.length, 2)
live.feed.setPaused(true)
assert.equal(live.states.at(-1).status, 'paused')
assert.equal(live.timers.size, 0)
live.feed.setPaused(false)
assert.equal(live.requests.length, 4)
live.feed.stop()
await live.reply(3, payload(3))
assert.equal(live.states.at(-1).status, 'idle')
assert.equal(live.states.at(-1).history.length, 0)

const delayed = fixture()
delayed.feed.start()
delayed.advance(500)
await delayed.reply(0, payload(1))
delayed.advance(250)
assert.equal(delayed.requests.length, 2, 'HTTP latency must not be added to the polling interval')
delayed.advance(500)
await delayed.reply(1, payload(2))
assert.equal(delayed.states.at(-1).status, 'current')
assert.ok(!delayed.states.some(state => state.status === 'stale'), 'healthy delayed responses must not flicker stale')
delayed.feed.stop()
assert.match(PlotsPage.template, /class="gx-plots__status" role="status"/)

const stale = fixture()
stale.feed.start()
await stale.reply(0, payload(1))
stale.advance(1400)
assert.equal(stale.states.at(-1).status, 'stale')
assert.equal(stale.states.at(-1).data.values.desiredLateralAccel, .2)
assert.equal(stale.states.at(-1).history.length, 1)
await stale.reply(1, payload(1, { state: 'unavailable', sampleAgeSeconds: null, values: null, error: 'No fresh controls' }))
assert.equal(stale.states.at(-1).status, 'stale')
assert.equal(stale.states.at(-1).data.sampleIndex, 1)
stale.advance(750)
await stale.reply(2, payload(2))
assert.equal(stale.states.at(-1).status, 'current')
assert.equal(stale.states.at(-1).history.length, 2)
stale.feed.stop()
assert.equal(stale.states.at(-1).data, null)

const interrupted = fixture()
interrupted.feed.start()
await interrupted.reply(0, payload(1))
interrupted.advance(2250)
assert.equal(interrupted.states.at(-1).status, 'stale')
assert.equal(interrupted.states.at(-1).data.sampleIndex, 1)
assert.equal(interrupted.states.at(-1).history.length, 1)
interrupted.feed.stop()

const gap = chartGeometry([{ at: 0, value: 1 }, { at: 750, value: 2 },
  { at: 4000, value: 3 }, { at: 4750, value: 4 }], ['value'])
assert.equal(gap.paths[0].length, 2, 'reconnection must not draw across a missing interval')

const aged = fixture()
aged.feed.start()
aged.advance(90)
await aged.reply(0, payload(1, { sampleAgeSeconds: 1.4 }))
assert.equal(aged.states.at(-1).status, 'current')
assert.equal(aged.states.at(-1).history[0].at, 1090) // Receipt time, independent of source age.
aged.advance(10)
assert.equal(aged.states.at(-1).status, 'stale')
aged.feed.stop()
const expiredInTransit = fixture()
expiredInTransit.feed.start()
expiredInTransit.advance(200)
await expiredInTransit.reply(0, payload(1, { sampleAgeSeconds: 1.4 }))
assert.equal(expiredInTransit.states.at(-1).status, 'stale')
assert.equal(expiredInTransit.states.at(-1).history.length, 0)
expiredInTransit.feed.stop()

const denied = fixture()
denied.feed.start()
await denied.reply(0, {}, 401)
assert.equal(denied.unauthorized, 1)
assert.equal(denied.timers.size, 0)

const timeout = fixture()
timeout.feed.start()
timeout.advance(1500)
assert.equal(timeout.states.at(-1).status, 'unavailable')
await timeout.reply(0, payload(1))
assert.equal(timeout.states.at(-1).status, 'unavailable')
timeout.feed.stop()

const prior = globalThis.document, listeners = new Map()
globalThis.document = { hidden: false, addEventListener: (name, fn) => listeners.set(name, fn),
  removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name) } }
const page = { mode: 'local', paused: false, feed: { starts: 0, pauses: [], stops: 0,
  active: false, start() { this.starts++; this.active = true }, setPaused(value) { this.pauses.push(value) }, stop() { this.stops++; this.active = false } } }
PlotsPage.mounted.call(page)
assert.equal(page.feed.starts, 1)
document.hidden = true; listeners.get('visibilitychange')()
assert.deepEqual(page.feed.pauses, [true])
document.hidden = false; listeners.get('visibilitychange')()
assert.deepEqual(page.feed.pauses, [true, false])
PlotsPage.beforeUnmount.call(page)
assert.equal(page.feed.stops, 1)
assert.equal(listeners.size, 0)
const hidden = { mode: 'local', paused: false, feed: { active: false, starts: 0, pauses: [], stops: 0,
  start() { this.active = true; this.starts++ }, setPaused(value) { this.pauses.push(value) }, stop() { this.stops++ } } }
document.hidden = true
PlotsPage.mounted.call(hidden)
assert.equal(hidden.feed.starts, 0)
document.hidden = false
listeners.get('visibilitychange')()
assert.equal(hidden.feed.starts, 1)
listeners.get('visibilitychange')()
assert.equal(hidden.feed.starts, 1)
PlotsPage.beforeUnmount.call(hidden)
assert.equal(listeners.size, 0)
globalThis.document = prior
assert.match(PlotsPage.template, /Lateral acceleration/)
assert.match(PlotsPage.template, /Longitudinal acceleration/)
assert.doesNotMatch(PlotsPage.template, /Download|Apply tune|Set control/)

// Compile and render the actual Vue page template with live-looking values.
globalThis.document = { createElement: () => ({
  set innerHTML(value) {
    this.textContent = value.replaceAll('&amp;', '&')
    this.children = [{ getAttribute: () => this.textContent.match(/foo="([\s\S]*?)"/)?.[1] ?? '' }]
  }, textContent: '', children: [],
}) }
const { compile } = await import('../web/vendor/vue/vue.esm-browser.js')
const render = compile(PlotsPage.template)
const chart = chartGeometry([{ ...values(), at: 1000 }, { ...values(), desiredLateralAccel: .3, at: 2500 }],
  ['desiredLateralAccel', 'actualLateralAccel'])
const context = { embedded: false, mode: 'local', status: 'current', data: payload(1), paused: false, advanced: false, error: '',
  lateralChart: chart, longChart: chart, lateralTermsChart: chart, longTermsChart: chart,
  lateralQuality: { label: 'Good', detail: '8 samples' }, longQuality: { label: 'Fair', detail: '8 samples' },
  source: (source) => source, label: (number) => String(number), togglePause: () => {}, }
const rendered = JSON.stringify(render(context, []))
assert.match(rendered, /Lateral acceleration/)
assert.match(rendered, /Longitudinal acceleration/)
assert.match(rendered, /Good/)
assert.match(rendered, /−1.5 s/)
const graphRender = compile(PlotGraph.template)
const graph = JSON.stringify(graphRender({ chart, title: 'Lateral acceleration history', terms: false, startLabel: null, endLabel: 'Latest' }, []))
assert.match(graph, /0.30/)
assert.match(graph, /0.00/)
assert.match(graph, /−1.5 s/)
assert.match(graph, /Latest/)
assert.match(graph, /48.0,/) // Plot geometry stays inside the labeled axes.
globalThis.document = prior
console.log('Galaxy Plots browser lifecycle and rendering contracts passed')


const recordedGraph = JSON.stringify(graphRender({ chart, title: 'Recorded section', terms: false, startLabel: 'Start · 0s', endLabel: 'End · 1.5s' }, []))
assert.match(recordedGraph, /Start · 0s/)
assert.match(recordedGraph, /End · 1.5s/)
assert.doesNotMatch(recordedGraph, /Latest/)
