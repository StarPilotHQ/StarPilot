import { GxState } from "./state.js"
import { GxIconButton } from "./icon-button.js"
// Bounded, read-only local control history. No layout or telemetry is persisted.
export const MAX_POINTS = 240
export const POLL_MS = 750
const SIGNALS = ['desiredLateralAccel', 'actualLateralAccel', 'desiredLongitudinalAccel', 'actualLongitudinalAccel',
  'lateralP', 'lateralI', 'lateralD', 'lateralF', 'longitudinalP', 'longitudinalI', 'longitudinalF', 'speedMps']
const STATES = ['current', 'stale', 'unavailable']
const SOURCES = {
  lateralSource: ['unavailable', 'torqueState', 'curvature'],
  longitudinalSource: ['unavailable', 'aTarget', 'pidSum'],
  lateralTermsSource: ['unavailable', 'torqueState', 'pidState'],
  longitudinalTermsSource: ['unavailable', 'controlsState'],
  speedSource: ['unavailable', 'deviceMotion', 'carState'],
}

export function validatePlotPayload(data) {
  if (data?.schemaVersion !== 1 || !/^[0-9a-f]{16}$/.test(data.sessionId) || !STATES.includes(data.state) ||
      !Number.isSafeInteger(data.sampleIndex) || data.sampleIndex < 0 ||
      !(data.sampleAgeSeconds === null || Number.isFinite(data.sampleAgeSeconds) && data.sampleAgeSeconds >= 0) ||
      typeof data.error !== 'string' || data.error.length > 80 || typeof data.bootStabilizing !== 'boolean' ||
      !(data.values === null || typeof data.values === 'object')) throw new Error('Invalid plot sample')
  if (data.state !== 'current') {
    if (data.values !== null) throw new Error('Stale plot values')
    return data
  }
  if (!data.values || data.sampleAgeSeconds === null || data.sampleAgeSeconds > 1.5 ||
      data.values.controlsFresh !== true || typeof data.values.poseFresh !== 'boolean' ||
      typeof data.values.controlsActive !== 'boolean' || typeof data.values.lateralControlActive !== 'boolean' ||
      typeof data.values.longitudinalControlActive !== 'boolean') throw new Error('Invalid plot source')
  for (const key of SIGNALS) if (!(data.values[key] === null || typeof data.values[key] === 'number' && Number.isFinite(data.values[key]))) throw new Error('Invalid plot value')
  for (const [key, allowed] of Object.entries(SOURCES)) if (!allowed.includes(data.values[key])) throw new Error('Invalid plot source')
  if (data.values.lateralSource === 'unavailable' && (data.values.desiredLateralAccel !== null || data.values.actualLateralAccel !== null)) throw new Error('Invalid lateral source')
  if (data.values.longitudinalSource === 'unavailable' && data.values.desiredLongitudinalAccel !== null) throw new Error('Invalid longitudinal source')
  if (!data.values.poseFresh && data.values.actualLongitudinalAccel !== null) throw new Error('Invalid pose source')
  if (data.values.speedSource === 'unavailable' && data.values.speedMps !== null) throw new Error('Invalid speed source')
  return data
}

export function appendPlotSample(history, payload, observedAt) {
  if (payload.state !== 'current' || !payload.values) return history
  if (history.at(-1)?.sessionId !== undefined && history.at(-1).sessionId !== payload.sessionId) history = []
  if (payload.sampleIndex === (history.at(-1)?.index ?? 0)) return history
  if (payload.sampleIndex < (history.at(-1)?.index ?? 0)) history = []
  const sample = { ...payload.values, sessionId: payload.sessionId, index: payload.sampleIndex,
    // Chart client observations; source age remains a separate freshness check.
    at: observedAt }
  return [...history, sample].slice(-MAX_POINTS)
}

export function matchQuality(history, desired, actual, { minSpeed = 0, minDemand = 0, great, good, fair, activeKey = 'controlsActive',
                                      warnError = null, severeError = null }) {
  const latest = history.at(-1)?.at
  if (!Number.isFinite(latest)) return { label: 'N/A', detail: 'Waiting for data' }
  const errors = history.filter((sample) => sample.at >= latest - 30000 && sample[activeKey] &&
    Number.isFinite(sample[desired]) && Number.isFinite(sample[actual]) &&
    (sample.speedMps ?? 0) >= minSpeed && Math.max(Math.abs(sample[desired]), Math.abs(sample[actual])) >= minDemand)
    .map((sample) => Math.abs(sample[desired] - sample[actual])).sort((a, b) => a - b)
  if (errors.length < 8) return { label: 'N/A', detail: `${errors.length} of 8 valid driving samples` }
  const percentile = (p) => { const i = (errors.length - 1) * p; const lo = Math.floor(i); return errors[lo] + (errors[Math.ceil(i)] - errors[lo]) * (i - lo) }
  const value = 0.7 * percentile(0.5) + 0.3 * percentile(0.9)
  const warnFraction = warnError === null ? 0 : errors.filter((error) => error > warnError).length / errors.length
  const severeFraction = severeError === null ? 0 : errors.filter((error) => error > severeError).length / errors.length
  const pass = (limit, maxWarn, maxSevere) => value <= limit && warnFraction <= maxWarn && severeFraction <= maxSevere
  return { label: pass(great, .18, .05) ? 'Great' : pass(good, .34, .12) ? 'Good' : pass(fair, .55, .24) ? 'Fair' : 'Poor',
    detail: `${errors.length} samples / 30 s · robust error ${value.toFixed(2)} m/s²` }
}

export function chartGeometry(history, keys) {
  const valid = history.filter((sample) => keys.some((key) => Number.isFinite(sample[key])))
  const lo = Math.min(0, ...valid.flatMap((sample) => keys.map((key) => sample[key]).filter(Number.isFinite)))
  const hi = Math.max(0, ...valid.flatMap((sample) => keys.map((key) => sample[key]).filter(Number.isFinite)))
  const span = Math.max(0.01, hi - lo)
  const first = history[0]?.at ?? 0, elapsed = Math.max(1, (history.at(-1)?.at ?? first) - first)
  const y = (value) => 132 - (value - lo) / span * 114
  const duration = valid.length > 1 ? Math.max(0, (history.at(-1).at - first) / 1000) : 0
  const paths = keys.map((key) => {
    let points = [], paths = [], previousAt = null
    for (const sample of history) {
      if (previousAt !== null && sample.at - previousAt > 1500) {
        if (points.length > 1) paths.push(points.join(' ')); points = []
      }
      previousAt = sample.at
      if (!Number.isFinite(sample[key])) { if (points.length > 1) paths.push(points.join(' ')); points = []; continue }
      const x = Math.max(48, Math.min(624, 48 + (sample.at - first) / elapsed * 576))
      points.push(`${x.toFixed(1)},${y(sample[key]).toFixed(1)}`)
    }
    if (points.length > 1) paths.push(points.join(' '))
    return paths
  })
  return { paths, valid: valid.length > 0, top: hi.toFixed(2), bottom: lo.toFixed(2), zeroY: y(0),
    from: duration > 0 ? `−${duration < 10 ? duration.toFixed(1) : Math.round(duration)} s` : '0 s' }
}

export function seriesPoints(history, keys) {
  return chartGeometry(history, keys).paths
}

export class PlotsFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                now = () => performance.now(), later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, now, later, cancel })
    this.active = false; this.paused = false; this.generation = 0; this.displayGeneration = 0
    this.timer = this.deadline = this.expiry = this.request = null
    this.history = []; this.lastData = null
  }
  clear() {
    for (const key of ['timer', 'deadline', 'expiry']) { if (this[key] !== null) this.cancel(this[key]); this[key] = null }
    this.request?.abort(); this.request = null
  }
  stop() {
    this.active = false; this.generation++; this.displayGeneration++; this.clear(); this.history = []; this.lastData = null
    this.publish({ status: 'idle', data: null, history: [], error: '' })
  }
  start() { this.stop(); this.active = true; if (!this.paused) return this.load() }
  setPaused(paused) {
    this.paused = paused
    if (paused) { this.generation++; this.displayGeneration++; this.clear(); this.publish({ status: 'paused', data: this.lastData, history: this.history, error: '' }) }
    else if (this.active) this.load()
  }
  async load() {
    if (!this.active || this.paused || this.request) return
    const generation = ++this.generation, start = this.now(), request = new AbortController()
    this.request = request
    this.deadline = this.later(() => {
      if (!this.active || generation !== this.generation) return
      this.generation++; this.displayGeneration++; request.abort(); this.request = null; this.clear()
      this.publish({ status: this.lastData ? 'stale' : 'unavailable', data: this.lastData, history: this.history, error: 'Plot refresh timed out' })
      this.timer = this.later(() => { this.timer = null; this.load() }, POLL_MS)
    }, 1500)
    try {
      const response = await this.fetcher('./api/plots/live', { signal: request.signal, cache: 'no-store' })
      if (!this.active || generation !== this.generation || request.signal.aborted) return
      if (response.status === 401) { this.stop(); this.unauthorized(); return }
      if (response.status === 503) {
        const body = await response.json().catch(() => null)
        if (!this.active || generation !== this.generation || request.signal.aborted) return
        if (['access_unavailable', 'setup_required'].includes(body?.code)) { this.stop(); this.unauthorized(); return }
      }
      if (!response.ok) throw new Error('Live plots unavailable')
      const data = validatePlotPayload(await response.json())
      if (!this.active || generation !== this.generation || request.signal.aborted) return
      const elapsed = this.now() - start
      if (!Number.isFinite(elapsed) || elapsed < 0 || elapsed >= 1500) throw new Error('Plot sample delayed')
      if (this.expiry !== null) this.cancel(this.expiry)
      this.expiry = null
      const displayGeneration = ++this.displayGeneration
      const remaining = data.sampleAgeSeconds === null ? 0 : 1500 - data.sampleAgeSeconds * 1000 - elapsed
      if (data.state === 'current' && remaining > 0) {
        this.history = appendPlotSample(this.history, data, this.now())
        this.lastData = data
        this.publish({ status: 'current', data, history: this.history, error: '' })
        this.expiry = this.later(() => {
          if (this.active && displayGeneration === this.displayGeneration) this.publish({ status: 'stale', data: this.lastData, history: this.history, error: 'Showing the last received sample'  })
        }, remaining)
      } else this.publish({ status: this.lastData ? 'stale' : data.state === 'unavailable' ? 'unavailable' : 'stale', data: this.lastData, history: this.history, error: data.error || 'Waiting for fresh control data' })
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.displayGeneration++
        if (this.expiry !== null) this.cancel(this.expiry)
        this.expiry = null
        this.publish({ status: this.lastData ? 'stale' : 'unavailable', data: this.lastData, history: this.history, error: error.message })
      }
    } finally {
      if (generation === this.generation) {
        if (this.deadline !== null) this.cancel(this.deadline); this.deadline = null
        this.request = null
        if (this.active && !this.paused) this.timer = this.later(() => { this.timer = null; this.load() }, Math.max(0, POLL_MS - (this.now() - start)))
      }
    }
  }
}

export const PlotGraph = {
  name: 'PlotGraph',
  props: { chart: { type: Object, required: true }, title: { type: String, required: true }, terms: { type: Boolean, default: false } },
  template: `<svg viewBox="0 0 640 170" role="img" :aria-label="title">
    <line class="gx-plot-axis" x1="48" y1="18" x2="48" y2="132" />
    <line class="gx-plot-axis" x1="48" y1="132" x2="624" y2="132" />
    <template v-if="chart.valid">
      <line class="gx-plot-zero" x1="48" :y1="chart.zeroY" x2="624" :y2="chart.zeroY" />
      <text class="gx-plot-label" x="42" y="21" text-anchor="end">{{ chart.top }}</text>
      <text class="gx-plot-label" x="42" y="135" text-anchor="end">{{ chart.bottom }}</text>
      <text class="gx-plot-label" x="52" y="160">{{ chart.from }}</text>
      <text class="gx-plot-label" x="624" y="160" text-anchor="end">Latest</text>
    </template>
    <g v-for="(paths, series) in chart.paths" :key="series"><polyline v-for="(points, i) in paths" :key="i" :points="points"
      :class="terms ? 'gx-plot-term-' + series : series === 0 ? 'gx-plot-desired' : 'gx-plot-actual'" /></g>
  </svg>`,
}

export const PlotsPage = {
  name: 'PlotsPage',
  components: { GxState, PlotGraph, GxIconButton },
  props: { embedded: { type: Boolean, default: false }, active: { type: Boolean, default: true }, mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ status: 'idle', data: null, history: [], error: '', paused: false, advanced: false }),
  created() { this.feed = new PlotsFeed({ publish: (update) => Object.assign(this.$data, update), unauthorized: this.unauthorized }) },
  watch: { active() { this.visibility?.() } },
  mounted() {
    this.visibility = () => {
      if (document.hidden || this.active === false) this.feed.setPaused(true)
      else if (!this.paused && this.mode === 'local') {
        if (!this.feed.active) { this.feed.setPaused(false); this.feed.start() }
        else this.feed.setPaused(false)
      }
    }
    document.addEventListener('visibilitychange', this.visibility)
    if (this.active !== false && this.mode === 'local' && !document.hidden) this.feed.start()
  },
  beforeUnmount() { document.removeEventListener('visibilitychange', this.visibility); this.feed.stop() },
  computed: {
    lateralQuality() { return matchQuality(this.history, 'desiredLateralAccel', 'actualLateralAccel', { minSpeed: 0.5, minDemand: 0.008, great: 0.15, good: 0.30, fair: 0.50, activeKey: 'lateralControlActive' }) },
    longQuality() { return matchQuality(this.history, 'desiredLongitudinalAccel', 'actualLongitudinalAccel', { minDemand: 0.05, great: 0.32, good: 0.52, fair: 0.78, activeKey: 'longitudinalControlActive', warnError: .52, severeError: .90 }) },
    lateralChart() { return chartGeometry(this.history, ['desiredLateralAccel', 'actualLateralAccel']) },
    longChart() { return chartGeometry(this.history, ['desiredLongitudinalAccel', 'actualLongitudinalAccel']) },
    lateralTermsChart() { return chartGeometry(this.history, ['lateralP', 'lateralI', 'lateralD', 'lateralF']) },
    longTermsChart() { return chartGeometry(this.history, ['longitudinalP', 'longitudinalI', 'longitudinalF']) },
  },
  methods: {
    togglePause() { this.paused = !this.paused; this.feed.setPaused(this.paused) },
    label(value, unit = 'm/s²') { return value === null || value === undefined ? 'Unavailable' : `${value.toFixed(2)} ${unit}` },
    source(kind) { return ({ torqueState: 'Steering torque controller', curvature: 'Curvature × speed² estimate', aTarget: 'Planner target acceleration',
      pidSum: 'PID term sum', pidState: 'Steering PID', controlsState: 'Longitudinal controller',
      deviceMotion: 'Device motion', carState: 'Vehicle speed', unavailable: 'Unavailable' })[kind] || 'Unavailable' },
  },
  template: `
    <div :class="['gx-plots', { 'gx-view': !embedded }]">
      <header v-if="!embedded" class="gx-settings__header gx-page-header"><div><h2>Plots &amp; Analysis</h2><p>Live local control observations. Graphs and match scores are diagnostic, not driving-control checks.</p></div></header>
      <GxState v-if="mode !== 'local'">Live plots require the authenticated local Galaxy service.</GxState>
      <template v-else>
        <div class="gx-plots__toolbar"><GxIconButton :label="paused ? 'Resume live plots' : 'Pause live plots'" :icon="paused ? 'bi-play-fill' : 'bi-pause-fill'" @click="togglePause" />
          <button type="button" class="gx-btn gx-btn--tonal" :aria-pressed="advanced" @click="advanced=!advanced">{{ advanced ? 'Hide' : 'Show' }} controller terms</button>
          <span class="gx-plots__status" role="status">{{ paused ? 'Paused' : data?.bootStabilizing ? 'Starting vehicle systems' : status === 'current' ? 'Live' : status === 'stale' ? 'Reconnecting · last received values' : status === 'unavailable' ? 'Unavailable' : 'Connecting' }}</span></div>
        <p class="gx-note gx-plots__hint" role="status">{{ !paused && error ? error : 'Missing samples leave a gap in the graph.' }}</p>
        <div class="gx-plots__grid">
          <section class="gx-card gx-plots__panel"><div class="gx-section__header"><i class="bi bi-graph-up" aria-hidden="true"></i><span class="gx-section__title">Lateral Acceleration</span></div><p class="gx-note gx-plots__source">Desired / actual · m/s² · {{ source(data?.values?.lateralSource) }}</p>
            <plot-graph :chart="lateralChart" title="Lateral acceleration history" />
            <dl class="gx-plots__readout"><div><dt>Desired</dt><dd>{{ label(data?.values?.desiredLateralAccel) }}</dd></div><div><dt>Actual</dt><dd>{{ label(data?.values?.actualLateralAccel) }}</dd></div></dl>
            <p class="gx-note gx-plots__source">Speed source: {{ source(data?.values?.speedSource) }}</p>
            <p class="gx-plots__match">30 s match: <strong>{{ !data || data?.bootStabilizing ? 'N/A' : lateralQuality.label }}</strong> · {{ data?.bootStabilizing ? 'Waiting for startup to settle' : status !== 'current' ? 'Last received window' : lateralQuality.detail }}</p></section>
          <section class="gx-card gx-plots__panel"><div class="gx-section__header"><i class="bi bi-graph-up-arrow" aria-hidden="true"></i><span class="gx-section__title">Longitudinal Acceleration</span></div><p class="gx-note gx-plots__source">Target / measured · m/s² · {{ source(data?.values?.longitudinalSource) }}; measured from {{ data?.values?.poseFresh ? 'device motion' : 'unavailable motion source' }}</p>
            <plot-graph :chart="longChart" title="Longitudinal acceleration history" />
            <dl class="gx-plots__readout"><div><dt>Target</dt><dd>{{ label(data?.values?.desiredLongitudinalAccel) }}</dd></div><div><dt>Measured</dt><dd>{{ label(data?.values?.actualLongitudinalAccel) }}</dd></div></dl>
            <p class="gx-plots__match">30 s match: <strong>{{ !data || data?.bootStabilizing ? 'N/A' : longQuality.label }}</strong> · {{ data?.bootStabilizing ? 'Waiting for startup to settle' : status !== 'current' ? 'Last received window' : longQuality.detail }}</p></section>
          <section v-if="advanced" class="gx-card gx-plots__panel"><div class="gx-section__header"><i class="bi bi-sliders" aria-hidden="true"></i><span class="gx-section__title">Lateral Controller Terms</span></div><p class="gx-note gx-plots__source">P / I / D / F · controller output units · {{ source(data?.values?.lateralTermsSource) }}</p>
            <plot-graph :chart="lateralTermsChart" title="Lateral controller terms" :terms="true" /></section>
          <section v-if="advanced" class="gx-card gx-plots__panel"><div class="gx-section__header"><i class="bi bi-sliders" aria-hidden="true"></i><span class="gx-section__title">Longitudinal Controller Terms</span></div><p class="gx-note gx-plots__source">P / I / F · m/s² · {{ source(data?.values?.longitudinalTermsSource) }}</p>
            <plot-graph :chart="longTermsChart" title="Longitudinal controller terms" :terms="true" /></section>
        </div><p class="gx-note">Blue is target, orange is observed. Missing or stale sources are never plotted as zero.</p>
      </template>
    </div>`,
}
