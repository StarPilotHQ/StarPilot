import { EdgePreview, PREVIEW_SCENARIOS, previewScenario } from './uniden-preview.js'
import { GalaxySelect } from './galaxy-select.js'
import { GxDialog } from './dialog.js'
import { guardUnload } from './unload-guard.js'

export function detectorStatus(state) {
  if (state?.connected) return { label: 'Connected', detail: 'Ready to receive detector alerts.', tone: 'connected' }
  const code = state?.connection_state
  if (code === 'radio_off') return { label: 'Bluetooth off', detail: 'Turn on Bluetooth on the Bluetooth tab.', tone: 'waiting' }
  if (code === 'detector_pairing_required') return { label: 'Check pairing', detail: 'Pair the detector in Bluetooth, then select it here.', tone: 'waiting' }
  if (code === 'session_expired') return { label: 'Sign in required', detail: 'Sign in to Galaxy again to reconnect.', tone: 'waiting' }
  if (code === 'busy') return { label: 'Waiting for Bluetooth', detail: 'Finish any device search or pairing. Connection will resume automatically.', tone: 'waiting' }
  if (code === 'unavailable') return { label: 'Connection unavailable', detail: 'Check that the detector is on and another app is not connected. Retrying automatically.', tone: 'waiting' }
  return { label: 'Reconnecting', detail: 'Keep the detector on and close other apps connected to it. Retrying automatically.', tone: 'waiting' }
}

const LEARNING_ROWS = 5
let nextRuleId = 0
// A client-only id keeps each rule row stable; normalize_rules takes exact fields, so it is stripped before saving.
const ruleIds = (rules, previous = []) => rules.map((rule, index) => ({ ...rule, uid: previous[index]?.uid ?? ++nextRuleId }))
export const savedConfig = config => ({ ...config, frequency_rules: config.frequency_rules.map(({ uid, ...rule }) => rule) })
export const detectorConfig = (value = {}, previous = []) => {
  const config = { ...defaults(), ...value }
  config.frequency_rules = ruleIds(config.frequency_rules, previous)
  config.timed_mute_settings = Object.fromEntries(['X', 'K', 'KA'].map(band => [band,
    { seconds: config.timed_mute_seconds, strength: config.timed_mute_strength, ...value.timed_mute_settings?.[band] }]))
  return config
}
const defaults = () => ({ enabled: false, address: '', model: 'R8',
  auto_mute: false, hide_filtered: false, hide_below_limit: false, hide_q_ride: false, hide_mute_memory: false, frequency_rules: [], quiet_ride: false, quiet_margin: 0, quiet_bands: ['X', 'K'],
  auto_learn: false, learn_bands: ['X', 'K'], border_bands: ['KA', 'LASER'], border_colors: { KA: 'coral', K: 'amber', LASER: 'coral' },
  border_edge: { line: true, dashes: true, glow: false, veil: false, chevrons: true }, border_muted_percent: 50,
  timed_mute: false, timed_mute_seconds: 5, timed_mute_strength: 6, timed_mute_bands: ['X', 'K', 'KA'] })
export const SWATCHES = { amber: '#FFBA4E', coral: '#FF5B65', magenta: '#FF1FCE', ice: '#9FE8FF', lime: '#A6FF00', white: '#FFFFFF', ultraviolet: '#7A1FFF' }
export const EDGE_PARTS = {
  line: ['Line along the border', 'A line just inside the openpilot border.'],
  dashes: ['Segmented line', 'Break the line into dashes so it never reads as the openpilot border.'],
  glow: ['Soft glow band', 'A glow fading in from the edge. Deeper and brighter as the signal grows.'],
  veil: ['Dark veil under the line', 'Darken the edge of the picture so the line stands out in daylight.'],
  chevrons: ['Chevrons', 'Point toward the signal: beside the speed for ahead, at the bottom for behind, on both sides for beside. One to four chevrons show strength. An R4 shows them beside the speed.'],
}
// Ka and laser are what police radar and lidar use, so learning them asks first.
export const LEARN_WARNINGS = {
  KA: { title: 'Learn Ka lockouts automatically?', body: 'Ka is the band most police radar uses. A learned Ka lockout keeps that signal quiet at that place on later drives, so a real radar set up there could go unannounced. StarPilot learns a Ka source only after five separate passes, and a signal stronger than any seen there still alerts.' },
  LASER: { title: 'Learn laser lockouts automatically?', body: 'Laser is almost always police lidar aimed at you, and by the time it alerts the reading is usually taken. A learned laser lockout keeps laser quiet at that place on later drives. StarPilot learns a laser source only after five separate passes.' },
}
const BAND_SCENARIO = { K: 'k-ahead', KA: 'ka-strong', LASER: 'laser' }
const BANDS = { X: 'X', K: 'K', KA: 'Ka', LASER: 'Laser' }
const copy = value => JSON.parse(JSON.stringify(value))
export const UnidenPanel = {
  components: { EdgePreview, GalaxySelect, GxDialog },
  props: { devices: { type: Array, default: () => [] }, parked: Boolean, unauthorized: Function, settings: Boolean },
  data: () => ({ config: detectorConfig(), saved: null, loaded: false, busy: false, error: '', state: null, editing: false,
    locks: [], candidates: 0, learning: [], metric: false, swatches: SWATCHES, edgeParts: EDGE_PARTS, bands: BANDS,
    scenarios: PREVIEW_SCENARIOS, scenario: 'drive', panel: 'alerts',
    sections: { alerts: 'Preferences', warning: 'Colors', frequencies: 'Rules', lockouts: 'Lockouts' }, replay: 0, leaveAction: null, leaveModal: false, confirmLearn: null, learnWarnings: LEARN_WARNINGS }),
  computed: {
    detectors() { return this.devices.filter(d => d.paired && /(?:^|[^a-z0-9])r[48]w?(?=$|[^a-z0-9])/i.test(d.name)) },
    selected() { return this.detectors.find(d => d.address.toUpperCase() === this.config.address.toUpperCase()) },
    dirty() { return this.saved !== null && JSON.stringify(this.config) !== JSON.stringify(this.saved) },
    ready() { return !!this.saved?.address && this.detectors.some(d => d.address.toUpperCase() === this.saved.address.toUpperCase()) },
    leaveInline() { return !!this.leaveAction && !this.leaveModal },
    learningOn() { return !!this.config.auto_learn },
    learningRows() {
      const bands = this.config.learn_bands || []
      return this.learning.slice(0, LEARNING_ROWS).map(item => ({ ...item, learnable: bands.includes(item.band),
        percent: Math.min(100, Math.round(item.count / item.need * 100)), left: Math.max(0, item.need - item.count),
        stage: item.need - item.count <= 1 ? 'Locks out next pass' : item.count * 2 < item.need ? 'Just started' : 'Learning' }))
    },
    learningTitle() {
      if (!this.learningOn) return 'Learning is off'
      const count = this.learning.length
      return count ? `Learning ${count} ${count === 1 ? 'source' : 'sources'}` : 'Learning: watching for repeat sources'
    },
    savedName() { return this.detectors.find(d => d.address.toUpperCase() === this.saved?.address)?.name || (this.saved?.address ? `Uniden ${this.saved.model}` : 'Select your detector') },
    connection() {
      if (!this.saved?.address) return { label: 'Not configured', detail: 'Choose a paired detector to get started.', tone: 'waiting' }
      if (!this.ready) return { label: 'Not paired', detail: 'Pair the selected detector again on the Bluetooth tab.', tone: 'waiting' }
      if (!this.saved.enabled) return { label: 'Integration off', detail: 'Enable the integration below to receive alerts.', tone: 'off' }
      return detectorStatus(this.state)
    },
  },
  mounted() {
    this.alive = true; this.epoch = 0
    this.visibility = () => { clearTimeout(this.timer); this.locksLoaded = false; if (!document.hidden) this.refresh() }
    document.addEventListener('visibilitychange', this.visibility)
    if (!document.hidden) this.refresh()
    this.stopUnloadGuard = guardUnload(() => this.dirty || this.busy)
  },
  updated() { this.measureEditor() },
  beforeUnmount() { this.resizer?.disconnect(); this.stopUnloadGuard?.(); this.alive = false; this.epoch++; clearTimeout(this.timer); document.removeEventListener('visibilitychange', this.visibility) },
  methods: {
    async request(payload) {
      const response = await fetch('./api/bluetooth/uniden', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: AbortSignal.timeout(15000) })
      const value = await response.json()
      if (response.status === 401) this.unauthorized?.()
      if (!response.ok) throw new Error(value.error || 'Detector unavailable')
      return value
    },
    async refresh() {
      if (!this.alive || this.busy || document.hidden) return
      const epoch = ++this.epoch
      try {
        const value = await this.request({ operation: 'status' })
        if (!this.alive || this.epoch !== epoch) return
        this.state = value
        if (!this.locksLoaded) { this.locksLoaded = true; this.loadLocks() }
        if (!this.loaded || !this.dirty && !this.editing) {
          this.config = copy(detectorConfig(value.config, this.saved?.frequency_rules)); this.saved = copy(this.config)
          if (!this.loaded) this.error = ''
          this.loaded = true
        }
      } catch {
        if (this.alive && this.epoch === epoch) {
          this.state = { connected: false, alerts: [], connection_state: 'unavailable' }
          if (!this.loaded) this.error = 'Unable to load detector settings. Retrying automatically.'
        }
      }
      finally { if (this.alive && this.epoch === epoch && !document.hidden) this.timer = setTimeout(() => this.refresh(), 2000) }
    },
    // The pinned save bar and preview set where the section tabs stick on narrow screens, as in the theme maker.
    measureEditor() {
      const editor = this.$refs.editor
      if (!editor || this.measured === editor || typeof ResizeObserver !== 'function') return
      this.resizer?.disconnect(); this.measured = editor
      this.resizer = new ResizeObserver(() => {
        const bar = this.$refs.leaveBar, pin = editor.querySelector('.gx-uniden__preview-card')
        if (bar) editor.style.setProperty('--layout-bar-h', bar.offsetHeight + 'px')
        if (pin) editor.style.setProperty('--layout-pin-h', getComputedStyle(pin).position === 'sticky' ? pin.offsetHeight + 'px' : '0px')
      })
      for (const el of [this.$refs.leaveBar, editor.querySelector('.gx-uniden__preview-card'), editor]) if (el) this.resizer.observe(el)
    },
    // Turning on Ka or laser learning waits for the dialog; turning anything off, or X and K on, is immediate.
    requestLearnBand(band) {
      if (band in LEARN_WARNINGS && !this.config.learn_bands.includes(band)) this.confirmLearn = band
      else this.toggleBand('learn_bands', band)
    },
    confirmLearnBand() {
      const band = this.confirmLearn
      this.confirmLearn = null
      if (band && !this.config.learn_bands.includes(band)) this.toggleBand('learn_bands', band)
    },
    // Show the band being colored: switch the preview to that band's scenario, or replay it if it is already showing.
    pickColor(band, name) {
      this.config.border_colors[band] = name
      const showing = previewScenario(this.scenario).script ? 'K' : previewScenario(this.scenario).alert.band
      if (showing === band) this.replay++
      else this.scenario = BAND_SCENARIO[band]
    },
    toggleBand(key, band) {
      const list = this.config[key]
      this.config[key] = list.includes(band) ? list.filter(item => item !== band) : [...list, band]
    },
    async unlock(id) { await this.loadLocks({ operation: 'unlock', id }) },
    async loadLocks(payload = { operation: 'locks' }) {
      try {
        const value = await this.request(payload)
        if (this.alive) { this.locks = value.locks; this.candidates = value.candidates; this.learning = value.learning || []; this.metric = value.metric }
      } catch (error) { if (this.alive && payload.operation === 'unlock') this.error = error.message }
    },
    lockLabel(lock) { return BANDS[lock.band] + (lock.frequency ? ` ${lock.frequency.toFixed(3)} GHz` : '') },
    lockDetail(lock) {
      return `${lock.count} ${lock.count === 1 ? 'pass' : 'passes'} · ${lock.lat.toFixed(4)}, ${lock.lon.toFixed(4)} · ${lock.radius_m} m`
    },
    selectDetector() {
      const match = this.selected?.name.match(/(?:^|[^a-z0-9])(r[48])w?(?=$|[^a-z0-9])/i)
      if (match) { this.config.model = match[1].toUpperCase(); this.config.enabled = true }
    },
    ruleRange(rule) {
      const frequency = Number(rule.frequency), tolerance = Number(rule.tolerance_mhz)
      if (rule.frequency === '' || rule.tolerance_mhz === '' || !Number.isFinite(frequency) || !Number.isFinite(tolerance) || tolerance < 0) return 'Enter a frequency and tolerance.'
      return tolerance === 0 ? `Matches ${frequency.toFixed(3)} GHz exactly` :
        `Matches ${(frequency - tolerance / 1000).toFixed(4)}–${(frequency + tolerance / 1000).toFixed(4)} GHz`
    },
    cancel() { this.config = copy(this.saved); this.editing = false; this.error = '' },
    // The same leave flow as the theme maker: an inline prompt in the save bar, or a dialog when leaving from the menu.
    requestLeave(proceed, presentation = 'inline') {
      if (this.busy) return
      if (!this.dirty) { proceed(); return }
      this.leaveAction = proceed
      this.leaveModal = presentation === 'modal'
      if (!this.leaveModal) this.$nextTick(() => {
        const bar = this.$refs.leaveBar
        bar?.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
        bar?.querySelector('button')?.focus({ preventScroll: true })
      })
    },
    cancelLeave() { if (!this.busy) { this.leaveAction = null; this.leaveModal = false } },
    discardAndLeave() {
      const proceed = this.leaveAction
      this.cancelLeave(); this.cancel(); proceed?.()
    },
    async saveAndLeave() {
      const proceed = this.leaveAction
      await this.save()
      if (!this.dirty && this.leaveAction === proceed) { this.cancelLeave(); proceed?.() }
    },
    addRule() {
      if (this.config.frequency_rules.length < 32) this.config.frequency_rules.push({ uid: ++nextRuleId, enabled: true, label: '', band: 'KA', frequency: 34.313, tolerance_mhz: 0 })
    },
    async save() {
      if (!this.parked || this.busy || !this.dirty || !this.selected) return
      this.busy = true
      this.epoch++
      clearTimeout(this.timer)
      try {
        const value = await this.request({ operation: 'configure', config: savedConfig(copy(this.config)) })
        if (this.alive) {
          this.state = this.saved?.address === value.config.address ? { ...this.state, config: value.config } : value
          const config = detectorConfig(value.config, this.config.frequency_rules)
          this.config = copy(config); this.saved = copy(config); this.error = ''; this.editing = false
        }
      } catch (error) { if (this.alive) this.error = error.message }
      finally { this.busy = false; if (this.alive) this.refresh() }
    },
  },
  template: `<div class="gx-uniden">
    <p v-if="!loaded" class="gx-note" role="status">Loading detector setup…</p>
    <p v-if="error" role="alert" class="gx-uniden__error">{{ error }}</p>
    <template v-if="loaded">
      <section class="gx-card gx-uniden__detector" :class="{ 'gx-uniden__detector--compact': ready && !editing }" aria-label="Detector connection">
        <div class="gx-panel gx-stack">
          <div v-if="ready && !editing" class="gx-uniden__summary">
            <div class="gx-uniden__summary-text">
              <div class="gx-uniden__summary-title"><h3>{{ savedName }}</h3><span class="gx-uniden__status" :class="'gx-uniden__status--' + connection.tone" role="status">{{ connection.label }}</span></div>
              <p class="gx-note">{{ connection.detail }}</p>
            </div>
            <div class="gx-uniden__summary-actions">
              <a v-if="!settings" class="gx-btn" href="#/bluetooth/uniden">Uniden settings</a>
              <button class="gx-btn gx-btn--tonal" type="button" :disabled="!parked || busy || dirty" aria-label="Change detector" @click="editing = true">Change</button>
            </div>
          </div>
          <template v-else>
            <div class="gx-uniden__heading">
              <div><span class="gx-uniden__eyebrow">DETECTOR</span><h3>{{ savedName }}</h3></div>
              <span class="gx-uniden__status" :class="'gx-uniden__status--' + connection.tone" role="status">{{ connection.label }}</span>
            </div>
            <p class="gx-note">{{ connection.detail }}</p>
          </template>
          <template v-if="!ready || editing">
            <p v-if="!detectors.length" class="gx-note">No paired detector found. <a class="gx-link" href="#/bluetooth">Pair in Bluetooth</a>, then return here.</p>
            <fieldset v-else :disabled="!parked || busy" class="gx-uniden__fieldset gx-stack">
              <label class="gx-uniden__field">Detector <select v-model="config.address" @change="selectDetector" class="gx-field" aria-label="Uniden detector"><option value="">Select a paired detector</option>
                <option v-for="d in detectors" :key="d.address" :value="d.address">{{ d.name }} · {{ d.address }}</option></select></label>
              <p class="gx-note">{{ selected ? config.model + ' detected. Saving enables the integration.' : 'Choose a paired detector to finish setup.' }}</p>
              <div class="gx-actions"><button class="gx-btn" type="button" :disabled="!dirty || !selected" @click="save">{{ busy ? 'Saving…' : saved.address ? 'Save changes' : 'Use this detector' }}</button>
                <button v-if="ready" class="gx-btn gx-btn--tonal" type="button" @click="cancel">Cancel</button></div>
            </fieldset>
          </template>
        </div>
      </section>
      <p v-if="!parked" class="gx-note">Park to change detector settings.</p>
      <div v-if="settings && ready && !editing" ref="editor" class="gx-layout gx-uniden-editor">
        <div ref="leaveBar" class="gx-layout__savebar" :class="{'gx-layout__savebar--leave': leaveInline}">
          <span role="status" :class="{'is-dirty': dirty && !leaveInline && !busy}">{{ leaveInline ? 'Leave without saving your changes?' : busy ? 'Saving…' : !parked ? 'Park to change settings' : dirty ? 'Unsaved' : 'Saved' }}</span>
          <template v-if="leaveInline">
            <button class="gx-btn gx-btn--tonal" type="button" aria-label="Keep editing" title="Keep editing" @click="cancelLeave"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12a8 8 0 1 0 3-6M4 4v6h6"/></svg><span class="gx-layout__action-label">Keep editing</span></button>
            <button class="gx-btn gx-btn--danger" type="button" aria-label="Discard and leave" title="Discard and leave" @click="discardAndLeave"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13"/></svg><span class="gx-layout__action-label">Discard and leave</span></button>
            <button class="gx-btn" type="button" :disabled="!parked || busy" aria-label="Save changes" title="Save changes" @click="saveAndLeave"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-8h10v8"/></svg><span class="gx-layout__action-label">{{ busy ? 'Saving…' : 'Save changes' }}</span></button>
          </template>
          <template v-else>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!dirty || busy" aria-label="Discard changes" title="Discard changes" @click="cancel"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13"/></svg><span class="gx-layout__action-label">Discard</span></button>
            <button class="gx-btn gx-layout__save" type="button" :disabled="!parked || busy || !dirty" aria-label="Save changes" title="Save changes" @click="save"><svg class="gx-layout__action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-8h10v8"/></svg><span class="gx-layout__action-label gx-layout__save-label">{{ busy ? 'Saving…' : 'Save' }}<span class="gx-layout__long"> changes</span></span></button>
          </template>
        </div>
        <nav class="gx-layout__segment gx-uniden__pages" aria-label="Uniden settings pages">
          <button v-for="(label, id) in sections" :key="id" class="gx-btn gx-btn--tonal" type="button" :aria-pressed="panel === id" @click="panel = id">{{ label }}</button>
        </nav>
        <div v-show="panel === 'warning'" class="gx-layout__workspace gx-uniden__workspace">
          <div class="gx-card gx-layout__inspector gx-uniden__controls">
        <section class="gx-uniden__section" aria-label="Full-screen warning">
          <div class="gx-section__header"><span class="gx-section__title">Full-screen warning</span></div>
          <fieldset :disabled="!parked || busy" class="gx-uniden__fieldset"><div class="gx-panel gx-stack">
            <p class="gx-note">Drawn around the driving screen during a live alert, above any openpilot alert. Each band uses the same warning in its own color.</p>
            <div class="gx-uniden__bands">
              <div v-for="band in ['KA', 'K', 'LASER']" :key="band" class="gx-uniden__band" :class="{ 'gx-uniden__band--off': !config.border_bands.includes(band) }">
                <div class="gx-uniden__heading">
                  <h4>{{ bands[band] }}</h4>
                  <label class="gx-uniden__enable"><span>{{ config.border_bands.includes(band) ? 'Warning on' : 'Warning off' }}</span><span class="gx-switch"><input type="checkbox" role="switch" :aria-label="bands[band] + ' full-screen warning'" :checked="config.border_bands.includes(band)" @change="toggleBand('border_bands', band)" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
                </div>
                <div class="gx-uniden__choices" role="group" :aria-label="bands[band] + ' warning color'">
                  <button v-for="(hex, name) in swatches" :key="name" type="button" class="gx-uniden__dot" :style="{ background: hex }" :aria-pressed="config.border_colors[band] === name" :aria-label="name" :title="name" @click="pickColor(band, name)"></button>
                </div>
              </div>
            </div>
            <div class="gx-uniden__edge-parts" role="group" aria-label="What the warning shows">
              <div v-for="(text, part) in edgeParts" :key="part" class="gx-row gx-uniden__preference">
                <div class="gx-row__info"><span class="gx-row__label">{{ text[0] }}</span><span class="gx-row__desc">{{ text[1] }}</span></div>
                <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="text[0]" v-model="config.border_edge[part]" :disabled="part === 'dashes' && !config.border_edge.line" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
              </div>
              <label class="gx-uniden__field">When the detector is muted, show the warning at {{ config.border_muted_percent ? config.border_muted_percent + '%' : 'off' }}
                <input type="range" min="0" max="100" step="5" v-model.number="config.border_muted_percent" aria-describedby="uniden-muted-note" /></label>
              <p id="uniden-muted-note" class="gx-note">0% hides the warning while the detector is muted.</p>
            </div>
          </div></fieldset>
        </section>
            <p class="gx-note">Widget size, position and appearance are in the theme maker.</p>
          </div>
          <section class="gx-card gx-layout__preview-card gx-uniden__preview-card" aria-label="Warning preview" style="--layout-ratio: 1.7222">
            <div class="gx-layout__subhead"><strong>Warning preview</strong><span>{{ dirty ? 'Showing unsaved changes' : 'Showing saved settings' }}</span></div>
            <div class="gx-layout__preview-bar">
              <GalaxySelect class="gx-field gx-field--full" aria-label="Preview scenario" :value="scenario" @change="scenario = $event.target.value">
                <optgroup v-for="(group, title) in scenarios" :key="title" :label="title">
                  <option v-for="(item, id) in group" :key="id" :value="id">{{ item.label }}</option>
                </optgroup>
              </GalaxySelect>
              <button class="gx-icon-btn gx-layout__bar-toggle" type="button" aria-label="Replay the scenario" title="Replay" @click="replay++"><i class="bi bi-arrow-repeat" aria-hidden="true"></i></button>
            </div>
            <edge-preview :config="config" :swatches="swatches" :scenario="scenario" :replay="replay" />
            <p class="gx-note">A sample scene. Choosing a scenario never changes your settings.</p>
          </section>
        </div>
        <section v-show="panel === 'alerts'" class="gx-card gx-uniden__page" aria-label="Alert preferences">
          <div class="gx-section__header"><span class="gx-section__title">Alert preferences</span></div>
          <fieldset :disabled="!parked || busy" class="gx-uniden__fieldset">
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Enable Uniden integration</span><span class="gx-row__desc">Receive alerts while the driving screen is active, even with the widget hidden.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Enable Uniden integration" v-model="config.enabled" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Auto-mute saved frequencies</span><span class="gx-row__desc">Mute when every active signal matches an enabled rule. A brief initial beep may occur.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Automatically mute matching frequencies" v-model="config.auto_mute" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Quiet Ride at the posted limit</span><span class="gx-row__desc">Mute the beep, keep the alert on screen, while you are at or under the posted limit plus the margin. Sudden strong signals are never quieted. Needs a known limit and a GPS fix.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Quiet Ride at the posted limit" v-model="config.quiet_ride" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div v-if="config.quiet_ride" class="gx-uniden__sub">
              <label class="gx-uniden__field gx-uniden__narrow">Margin over the limit ({{ metric ? 'km/h' : 'mph' }}) <input class="gx-field" type="number" min="0" max="15" step="1" inputmode="numeric" v-model.number="config.quiet_margin" /></label>
              <div class="gx-uniden__choices" role="group" aria-label="Bands Quiet Ride may mute"><span class="gx-note">Mute</span>
                <button v-for="band in Object.keys(bands)" :key="band" type="button" class="gx-chip gx-uniden__choice" :class="{ 'gx-chip--selected': config.quiet_bands.includes(band) }" :aria-pressed="config.quiet_bands.includes(band)" @click="toggleBand('quiet_bands', band)">{{ bands[band] }}</button></div>
              <p v-if="config.quiet_bands.includes('LASER')" class="gx-note">Laser is muted too. The detector mutes everything at once, so a new signal that arrives after a mute may stay quiet until the detector re-alerts.</p>
            </div>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Mute after a few seconds</span><span class="gx-row__desc">Let each new signal beep for a moment, then mute it. The alert stays on screen. If it reaches the chosen strength, the detector is unmuted, the card says Strong signal and the warning flashes. Choose Never to keep it quiet. Laser is never muted this way.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Mute after a few seconds" v-model="config.timed_mute" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div v-if="config.timed_mute" class="gx-uniden__sub">
              <div v-for="band in ['X', 'K', 'KA']" :key="band">
                <label class="gx-uniden__field"><span><input type="checkbox" :value="band" v-model="config.timed_mute_bands" /> {{ bands[band] }}</span></label>
                <div v-if="config.timed_mute_bands.includes(band)" class="gx-uniden__pair">
                  <label class="gx-uniden__field">{{ bands[band] }} mute after (seconds) <input class="gx-field" type="number" min="1" max="60" step="1" inputmode="numeric" v-model.number="config.timed_mute_settings[band].seconds" /></label>
                  <label class="gx-uniden__field">{{ bands[band] }} warn again at strength <select class="gx-field" v-model.number="config.timed_mute_settings[band].strength"><option :value="0">Never</option><option v-for="n in [2, 3, 4, 5, 6, 7, 8]" :key="n" :value="n">{{ n }} of 8</option></select></label>
                </div>
              </div>
            </div>
            <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Learn lockouts automatically</span><span class="gx-row__desc">After the same fixed source is passed on separate trips (three for X and K, five for Ka and Laser), lock it out at that place. A source that repeats at one spot but is missing on many drive-bys, like a patrol spot, is not learned. A signal stronger than ever seen there still alerts.</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Learn lockouts automatically" v-model="config.auto_learn" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div v-if="config.auto_learn" class="gx-uniden__sub">
              <div class="gx-uniden__choices" role="group" aria-label="Bands to learn"><span class="gx-note">Learn</span>
                <button v-for="band in Object.keys(bands)" :key="band" type="button" class="gx-chip gx-uniden__choice" :class="{ 'gx-chip--selected': config.learn_bands.includes(band) }" :aria-pressed="config.learn_bands.includes(band)" @click="requestLearnBand(band)">{{ bands[band] }}</button></div>
            </div>
            <div class="gx-uniden__group" role="group" aria-labelledby="uniden-on-screen">
              <div class="gx-uniden__group-head"><strong id="uniden-on-screen">On the screen</strong><span class="gx-note">For the widget and the full-screen warning. Each works without the other.</span></div>
              <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Hide lockouts and frequency rules</span><span class="gx-row__desc">Alerts that match a saved lockout or an enabled frequency rule.</span></div>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Hide lockouts and frequency rules" v-model="config.hide_filtered" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
              <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Hide alerts muted below the limit</span><span class="gx-row__desc">Alerts Quiet Ride is muting while you are at or under the posted limit.</span></div>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Hide alerts muted below the limit" v-model="config.hide_below_limit" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
              <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Hide Q-Ride alerts</span><span class="gx-row__desc">Alerts the detector's own Q-Ride mode is silencing.</span></div>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Hide Q-Ride alerts" v-model="config.hide_q_ride" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
              <div class="gx-row gx-uniden__preference"><div class="gx-row__info"><span class="gx-row__label">Hide mute memory alerts</span><span class="gx-row__desc">Alerts the detector silences from a saved or learned mute location.</span></div>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Hide mute memory alerts" v-model="config.hide_mute_memory" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
              <p class="gx-note">Shown alerts that are muted stay on screen: the widget names the reason, and the warning dims to the muted brightness on the Colors page.</p>
            </div>
          </fieldset>
        </section>
        <section v-show="panel === 'lockouts'" class="gx-card gx-uniden__page" aria-label="Saved lockouts">
          <div class="gx-section__header"><span class="gx-section__title">Saved lockouts</span></div>
          <div class="gx-panel gx-stack">
            <div class="gx-uniden__learning" :class="{ 'gx-uniden__learning--on': learningOn }" role="status">
              <div class="gx-uniden__learning-head"><span class="gx-uniden__learning-dot" aria-hidden="true"></span><strong>{{ learningTitle }}</strong></div>
              <p v-if="!learningOn" class="gx-note">Turn on Learn lockouts automatically in Preferences to lock out sources you pass again and again.</p>
              <p v-else-if="!learning.length" class="gx-note">A fixed source becomes a lockout after you pass it on separate trips: three for X and K, five for Ka and Laser.</p>
              <ul v-if="learningOn && learningRows.length" class="gx-uniden__learning-list">
                <li v-for="item in learningRows" :key="item.id" :class="{ 'gx-uniden__learning-item--paused': !item.learnable }">
                  <div class="gx-uniden__learning-line">
                    <span class="gx-uniden__learning-name">{{ lockLabel(item) }}</span>
                    <span class="gx-uniden__learning-count">{{ item.learnable ? item.stage : 'Band not learned' }}</span>
                  </div>
                  <div v-if="item.learnable" class="gx-uniden__meter" role="progressbar" :aria-label="lockLabel(item) + ' learning progress'"
                       aria-valuemin="0" :aria-valuemax="item.need" :aria-valuenow="item.count"><span :style="{ width: item.percent + '%' }"></span></div>
                </li>
              </ul>
              <p v-if="learningOn && learning.length > learningRows.length" class="gx-note">+{{ learning.length - learningRows.length }} more being learned</p>
            </div>
            <p class="gx-note">Hold an alert on the driving screen to lock it out where you are. Lockouts only apply near that place.</p>
            <div v-if="!locks.length" class="gx-uniden__empty"><strong>No saved lockouts</strong></div>
            <div v-for="lock in locks" :key="lock.id" class="gx-uniden__rule-footer">
              <div><h4>{{ lockLabel(lock) }} <span class="gx-uniden__lock-source" :class="{ 'gx-uniden__lock-source--auto': lock.source === 'auto' }">{{ lock.source === 'auto' ? 'Learned' : 'Held on screen' }}</span></h4><p class="gx-note">{{ lockDetail(lock) }}</p></div>
              <button class="gx-btn gx-btn--tonal" type="button" @click="unlock(lock.id)" :aria-label="'Remove lockout ' + lockLabel(lock)">Remove</button>
            </div>
          </div>
        </section>
        <section v-show="panel === 'frequencies'" class="gx-card gx-uniden__page" aria-label="Frequency rules">
          <div class="gx-section__header"><span class="gx-section__title">Frequency rules</span>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!parked || busy || config.frequency_rules.length >= 32" @click="addRule">Add frequency</button></div>
          <div class="gx-panel gx-stack">
            <p class="gx-note">Save known false alerts such as your head unit. Rules apply wherever you drive.</p>
            <div v-if="!config.frequency_rules.length" class="gx-uniden__empty"><strong>No saved frequencies</strong><p class="gx-note">Choose Add frequency, then enter the reading shown by your detector.</p></div>
            <fieldset v-for="(rule, index) in config.frequency_rules" :key="rule.uid" :disabled="!parked || busy" class="gx-uniden__fieldset gx-uniden__rule">
              <div class="gx-uniden__heading">
                <h4>{{ rule.label || 'Frequency ' + (index + 1) }}</h4>
                <label class="gx-uniden__enable"><span>{{ rule.enabled ? 'Enabled' : 'Off' }}</span><span class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Rule ' + (index + 1) + ' enabled'" v-model="rule.enabled" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
              </div>
              <div class="gx-uniden__fields">
                <label class="gx-uniden__field gx-uniden__name">Label <input class="gx-field" v-model="rule.label" maxlength="60" placeholder="e.g. Head unit" /></label>
                <label class="gx-uniden__field">Band <select class="gx-field" v-model="rule.band"><option>X</option><option>K</option><option>KA</option></select></label>
                <label class="gx-uniden__field">Frequency (GHz) <input class="gx-field" type="number" step="0.001" inputmode="decimal" v-model.number="rule.frequency" /></label>
                <label class="gx-uniden__field">Tolerance (± MHz) <input class="gx-field" type="number" min="0" max="10" step="0.1" inputmode="decimal" v-model.number="rule.tolerance_mhz" /></label>
              </div>
              <div class="gx-uniden__rule-footer"><div><p class="gx-note">{{ ruleRange(rule) }}</p><p class="gx-note">Keep tolerance at 0 unless the reading drifts.</p></div>
                <button class="gx-btn gx-btn--tonal" type="button" @click="config.frequency_rules.splice(index, 1)" :aria-label="'Remove rule ' + (index + 1)">Remove</button></div>
            </fieldset>
            <p v-if="config.frequency_rules.length" class="gx-note">Another source at the same frequency will also match. These rules are saved in StarPilot, not the detector’s lockout memory.</p>
          </div>
        </section>
        <GxDialog v-if="confirmLearn" class="gx-layout__leave-dialog" labelledby="gx-uniden-learn-title" describedby="gx-uniden-learn-body" alert @close="confirmLearn = null">
          <div class="gx-layout__leave-copy"><h3 id="gx-uniden-learn-title">{{ learnWarnings[confirmLearn].title }}</h3>
            <p id="gx-uniden-learn-body">{{ learnWarnings[confirmLearn].body }}</p></div>
          <div class="gx-layout__leave-actions">
            <button type="button" class="gx-btn gx-btn--tonal" @click="confirmLearn = null">Keep it off</button>
            <button type="button" class="gx-btn" @click="confirmLearnBand">Learn {{ bands[confirmLearn] }} lockouts</button>
          </div>
        </GxDialog>
        <GxDialog v-if="leaveModal" class="gx-layout__leave-dialog" labelledby="gx-uniden-leave-title" describedby="gx-uniden-leave-body" alert @close="cancelLeave">
          <div class="gx-layout__leave-copy"><h3 id="gx-uniden-leave-title">Leave without saving your changes?</h3>
            <p id="gx-uniden-leave-body">Save your Uniden settings before leaving, keep editing, or discard the changes.</p></div>
          <div class="gx-layout__leave-actions">
            <button type="button" class="gx-btn gx-btn--tonal" @click="cancelLeave">Keep editing</button>
            <button type="button" class="gx-btn gx-btn--danger" @click="discardAndLeave">Discard and leave</button>
            <button type="button" class="gx-btn" :disabled="!parked || busy" @click="saveAndLeave">{{ busy ? 'Saving…' : 'Save changes' }}</button>
          </div>
        </GxDialog>
      </div>
    </template>
  </div>`,
}
