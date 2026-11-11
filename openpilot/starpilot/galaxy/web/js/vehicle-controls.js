import { GxDialog } from "./dialog.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { connectionError } from "./polling.js"
import { reactive } from "../vendor/vue/vue.esm-browser.js"
import { SettingsPage } from "./settings.js"
import { GalaxySelect } from "./galaxy-select.js"
import { ToyotaToolsPage } from "./toyota-tools.js"
import { beginSave, finishSave } from "./save-feedback.js"

export function validVehiclePage(data) {
  return data?.version === 1 && typeof data.parked === "boolean" && typeof data.readable === "boolean" &&
    typeof data.valid === "boolean" && (data.selected === null || typeof data.selected === "string") &&
    typeof data.selectedLabel === "string" && data.selectedLabel.length <= 120 &&
    (data.reported === null || (typeof data.reported?.platform === "string" && typeof data.reported?.label === "string" &&
      typeof data.reported?.make === "string")) &&
    (data.view === null || typeof data.view === "string" && data.view.length <= 64) &&
    Array.isArray(data.choices) && data.choices.length <= 1000 && data.choices.every((item) =>
      typeof item?.platform === "string" && item.platform.length <= 100 && item.platform !== "MOCK" &&
      typeof item.make === "string" && item.make.length <= 100 && typeof item.label === "string" && item.label.length <= 120) &&
    (!data.readable || data.view !== null) && (!data.valid || data.selected === null ||
      data.choices.some((item) => item.platform === data.selected))
}

export class VehicleSelectionFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.request = null
    this.timer = null
    this.pollTimer = null
    this.data = null
    this.pending = null
    this.busy = false
    this.error = ""
  }

  emit(status, error = this.error) { this.error = error; this.publish({ status, data: this.data, pending: this.pending, busy: this.busy, error }) }
  stop() {
    this.active = false
    this.generation++
    this.request?.abort()
    if (this.timer !== null) this.cancelTimer(this.timer)
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.request = this.timer = this.pollTimer = null
    this.data = this.pending = null
    this.busy = false
    this.emit("idle", "")
  }
  start() { this.stop(); this.active = true; return this.refresh() }

  async run(path, body = null) {
    if (!this.active) return null
    if (this.request) {
      if (body === null || this.busy) return null
      this.request.abort()
      this.generation++
      this.cancelTimer(this.timer)
      this.request = this.timer = null
    }
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.pollTimer = null
    const generation = this.generation
    const request = new AbortController()
    this.request = request
    this.busy = body !== null
    this.emit(body === null ? (this.data ? "ready" : "loading") : "saving")
    this.timer = this.later(() => {
      if (!this.active || this.generation !== generation || this.request !== request) return
      this.request = null
      this.timer = null
      this.generation++
      this.busy = false
      this.data = this.pending = null
      request.abort()
      this.emit("unavailable", "Vehicle selection could not connect. Reconnecting automatically…")
      this.schedulePoll()
    }, 8000)
    try {
      const response = await this.fetcher(path, { credentials: "same-origin", cache: "no-store", signal: request.signal,
        ...(body === null ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) })
      if (!this.active || this.generation !== generation || this.request !== request || request.signal.aborted) return null
      const result = await response.json()
      if (!this.active || this.generation !== generation || this.request !== request || request.signal.aborted) return null
      if (response.status === 401 || response.status === 503 && ["setup_required", "access_unavailable"].includes(result?.code)) {
        this.stop(); this.unauthorized(); return null
      }
      if (response.status === 409) {
        this.data = this.pending = null
        this.emit("unavailable", result?.code === "unverified" ?
          "Save could not be confirmed. Refresh saved values before trying again." :
          "Saved vehicle changed. Refresh before choosing again.")
        return null
      }
      if (!response.ok) throw new Error("Vehicle selection is unavailable. Refresh to try again.")
      this.error = ""
      return result
    } catch (error) {
      if (this.active && this.generation === generation && this.request === request && !request.signal.aborted) {
        this.data = this.pending = null
        this.emit("unavailable", connectionError(error))
      }
      return null
    } finally {
      if (this.request === request) {
        if (this.timer !== null) this.cancelTimer(this.timer)
        this.request = this.timer = null
        this.busy = false
        this.emit(this.data ? "ready" : "unavailable")
        this.schedulePoll()
      }
    }
  }

  schedulePoll() {
    if (!this.active || this.pending || this.request || this.pollTimer !== null) return
    this.pollTimer = this.later(() => { this.pollTimer = null; this.refresh() }, 2000)
  }

  async refresh() {
    if (!this.active || this.request || this.pending) return
    this.pending = null
    const generation = this.generation
    const data = await this.run("./api/vehicle-selection")
    if (!this.active || this.generation !== generation) return
    if (data && validVehiclePage(data)) {
      this.data = data
      this.emit("ready")
    } else if (data) {
      this.data = null
      this.emit("unavailable", "Vehicle selection response was invalid.")
    }
    this.schedulePoll()
  }

  async preview(platform) {
    if (!this.active || this.busy || this.pending || !this.data?.parked || !this.data.readable ||
        (!this.data.valid && platform !== null) ||
        (platform !== null && !this.data.choices.some((item) => item.platform === platform))) return
    const result = await this.run("./api/vehicle-selection/preview", { view: this.data.view, platform })
    if (result && typeof result.intent === "string" && result.intent.length <= 64 && typeof result.question === "string") {
      this.pending = result
      this.emit("ready")
    }
  }

  cancel() { this.pending = null; if (this.active) this.emit(this.data ? "ready" : "unavailable"); this.schedulePoll() }

  async confirm() {
    if (!this.active || this.busy || !this.pending?.intent) return
    const feedback = beginSave()
    const intent = this.pending.intent
    this.pending = null
    this.data = null
    const result = await this.run("./api/vehicle-selection/confirm", { intent, confirmed: true })
    finishSave(feedback, result?.saved === true)
    if (!this.active) return
    if (result?.saved !== true) {
      if (!this.error) this.emit("unavailable", "Save could not be confirmed. Refresh saved values before trying again.")
      return
    }
    await this.refresh()
  }
}

export const VehicleControlsPage = {
  components: { ToyotaToolsPage, GxDialog, GxState, GxNotice, SettingsPage, GalaxySelect },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  setup(props) {
    const state = reactive({ status: "idle", data: null, pending: null, busy: false, error: "", query: "", make: "", manualSelectionOpen: false })
    const feed = new VehicleSelectionFeed({ publish: (update) => Object.assign(state, update), unauthorized: props.unauthorized })
    return { state, feed }
  },
  computed: {
    busy() { return this.state.busy || !!this.state.pending || !!this.$refs?.vehicleSettings?.busy },
    autoDetection() { return this.state.data?.valid && this.state.data.selected === null && !this.state.manualSelectionOpen },
    makes() { return [...new Set((this.state.data?.choices || []).map((item) => item.make))] },
    models() {
      if (!this.state.make) return []
      const needle = this.state.query.trim().toLocaleLowerCase()
      return (this.state.data?.choices || []).filter((item) => item.make === this.state.make &&
        (!needle || `${item.make} ${item.label} ${item.platform}`.toLocaleLowerCase().includes(needle)))
    },
  },
  mounted() { if (this.mode === "local") this.feed.start() },
  beforeUnmount() { this.feed.stop() },
  methods: {
    requestRouteLeave(proceed) {
      if (this.busy) return
      if (this.$refs.vehicleSettings) this.$refs.vehicleSettings.requestRouteLeave(proceed)
      else proceed()
    },
    navigateBack() {
      if (this.state.pending) { this.feed.cancel(); return true }
      if (this.state.busy) return true
      return this.$refs.vehicleSettings?.navigateBack() || false
    },
    choose(platform) { this.feed.preview(platform) },
    changeAutoDetection(event) {
      event.target.checked = this.autoDetection
      if (this.autoDetection) this.state.manualSelectionOpen = true
      else if (this.state.data.selected === null) this.state.manualSelectionOpen = false
      else { this.state.manualSelectionOpen = false; this.choose(null) }
    },
  },
  template: `
    <section class="gx-vehicle" aria-label="Vehicle Controls" :inert="state.busy" :aria-busy="state.busy || undefined">
      <header class="gx-settings__header gx-page-header"><div><h2>Vehicle Selection</h2>
        <p>Choose Auto Detection or save a vehicle for the next start.</p></div></header>
      <GxState v-if="mode !== 'local'">Local vehicle selection is unavailable in preview.</GxState>
      <template v-else>
        <GxNotice tone="danger" v-if="state.error">{{ state.error }}
          </GxNotice>
        <GxState v-if="state.status === 'loading'" loading>Loading vehicle selection…</GxState>
        <article v-if="state.data" class="gx-card"><div class="gx-section__header"><i class="bi bi-car-front" aria-hidden="true"></i><span class="gx-section__title">Selection &amp; status</span></div><div class="gx-panel gx-stack">
          <div class="gx-vehicle__summary"><div><small>Configured selection mode</small>
              <strong>{{ state.data.valid ? (state.data.selected === null ? 'Auto Detection' : 'Manually Selected') : state.data.selectedLabel }}</strong>
              <p v-if="state.data.selected !== null">{{ state.data.selectedLabel }}</p>
              <p>Saved for the next start.</p><p v-if="!state.data.valid">Saved choice needs review. Only Auto can repair it.</p></div>
            <div><small>{{ state.data.reported?.origin === 'detected' ? 'Last auto-detected vehicle' : 'Last runtime vehicle' }}</small>
              <strong>{{ state.data.reported?.label || 'Not available' }}</strong>
              <p v-if="state.data.reported?.origin === 'manual'">This runtime used a manual selection, not automatic detection.</p>
              <p>This is the last runtime report, not current physical vehicle confirmation.</p></div></div>
          <p v-if="!state.data.parked" class="gx-note">Changes require fresh parked vehicle evidence.</p>
          <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Auto Detection</span></div>
            <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Auto Detection" :checked="autoDetection"
              :disabled="!state.data.parked || !state.data.readable" @change="changeAutoDetection"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
          <template v-if="state.data.valid">
            <h3>Choose a Vehicle</h3>
            <div class="gx-vehicle__filters gx-actions"><GalaxySelect v-model="state.make" class="gx-field gx-field--full" aria-label="Vehicle make" :disabled="autoDetection || !state.data.parked || !state.data.readable">
                <option value="" disabled>Choose a make</option><option v-for="make in makes" :key="make" :value="make">{{ make }}</option></GalaxySelect>
              <input v-if="state.make" v-model="state.query" class="gx-field" type="search" aria-label="Search vehicle models" placeholder="Search models" :disabled="autoDetection || !state.data.parked || !state.data.readable"></div>
            <p v-if="state.make && !models.length" class="gx-note">No models match this search.</p>
            <div v-if="state.make && models.length" class="gx-vehicle__models"><button v-for="item in models" :key="item.platform" type="button" class="gx-btn gx-btn--tonal"
                :aria-pressed="state.data.selected === item.platform" :disabled="autoDetection || !state.data.parked || !state.data.readable" @click="choose(item.platform)">
                <strong>{{ item.label }}</strong></button></div>
          </template>
        </div></article>
        <ToyotaToolsPage :mode="mode" :unauthorized="unauthorized" />
        <SettingsPage ref="vehicleSettings" :mode="mode" :unauthorized="unauthorized" initial-page="vehicle" title="Vehicle settings" />
        <GxDialog v-if="state.pending" labelledby="gx-vehicle-controls-confirm-title" @close="feed.cancel()"><h3 id="gx-vehicle-controls-confirm-title">Confirm Vehicle Selection</h3><p>{{ state.pending.question }}</p>
            <p>The choice takes effect after the next start.</p>
            <div class="gx-settings__controls gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="feed.cancel()">Cancel</button>
              <button type="button" class="gx-btn" @click="feed.confirm()">Save</button></div></GxDialog>
      </template>
    </section>`,
}
