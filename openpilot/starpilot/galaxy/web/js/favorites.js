import { GxState } from "./state.js"
import { PollTimer, connectionError } from "./polling.js"
import { GxNotice } from "./notice.js"
import { reactive } from "../vendor/vue/vue.esm-browser.js"
import { GalaxySelect } from "./galaxy-select.js"
import { reportSave } from "./save-feedback.js"

function validSpeedValue(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 5 && value <= 145
}

export function validFavorites(data) {
  return !!data && typeof data.revision === "string" && !!data.revision && typeof data.editable === "boolean" &&
    typeof data.valid === "boolean" && Array.isArray(data.slots) && data.slots.length === 3 &&
    data.slots.every((slot) => slot && typeof slot.enabled === "boolean" && typeof slot.show_onroad === "boolean" &&
      (slot.key === null || typeof slot.key === "string") && typeof slot.label === "string" && slot.label.length <= 32) &&
    Array.isArray(data.options) && data.options.length <= 128 && data.options.every((option) => option &&
      typeof option.key === "string" && typeof option.label === "string" && ["toggle", "enum", "action"].includes(option.kind)) &&
    new Set(data.options.map((option) => option.key)).size === data.options.length &&
    Array.isArray(data.states) && data.states.length === 3 && data.states.every((state, index) => state &&
      state.index === index && typeof state.available === "boolean" && typeof state.stateLabel === "string" && typeof state.reason === "string")
}

export class FavoritesFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer })
    this.poller = new PollTimer({ read: () => this.load(), later, cancel: cancelTimer })
    this.error = ""
    this.saving = false
    this.active = false; this.generation = 0; this.data = this.request = this.timer = null; this.needsReload = false
  }
  stop() {
    this.poller.stop()
    this.active = false; this.generation++; this.request?.abort()
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.request = this.timer = null
    this.saving = false
  }
  start() { this.stop(); this.active = true; this.poller.start(); return this.load() }
  load() { return this.run() }
  update(index, patch) {
    if (!this.data?.editable || this.saving || this.needsReload || !Number.isInteger(index) || index < 0 || index > 2 ||
        !patch || Object.keys(patch).some((key) => !["key", "label", "enabled", "show_onroad", "value"].includes(key))) return
    const slots = this.data.slots.map((slot) => ({ ...slot }))
    if (Object.hasOwn(patch, "key")) {
      const option = this.data.options.find((entry) => entry.key === patch.key)
      if (patch.key !== null && !option) return
      delete slots[index].value
      slots[index].label = option?.label.slice(0, 32) || ""
      if (!option) Object.assign(slots[index], { enabled: false, show_onroad: false })
    }
    Object.assign(slots[index], patch)
    if (Object.hasOwn(patch, "value") && (slots[index].key !== "__starpilot_controller_action__:set_speed" || !validSpeedValue(patch.value))) return
    return this.run({ revision: this.data.revision, slots })
  }
  async run(body = null) {
    if (!this.active) return
    if (this.request) {
      if (body === null || this.saving) return
      this.request.abort()
      this.cancelTimer(this.timer)
    }
    const request = new AbortController(), generation = ++this.generation, saving = body !== null
    this.request = request
    this.saving = saving
    if (saving || !this.data && !this.error) this.publish({ status: saving ? "saving" : "loading", notice: "",
      ...(saving ? { data: { ...this.data, slots: body.slots } } : {}) })
    const fail = (error) => {
      this.error = error
      this.needsReload ||= saving
      this.publish({ status: this.data ? "ready" : "unavailable", data: this.data, needsReload: this.needsReload, error })
    }
    this.timer = this.later(() => {
      if (!this.active || generation !== this.generation) return
      request.abort(); this.generation++; this.request = this.timer = null
      this.saving = false
      fail(saving ? "Saving timed out. Reload to check which Quick Select were saved." : "Reading Quick Select timed out. Reload to try again.")
    }, 5000)
    try {
      const response = await this.fetcher("./api/favorites/slots", { credentials: "same-origin", cache: "no-store", signal: request.signal,
        ...(saving ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) })
      if (!this.active || generation !== this.generation) return
      const data = await response.json().catch(() => null)
      if (!this.active || generation !== this.generation) return
      if (response.status === 401 || ["access_unavailable", "setup_required"].includes(data?.code)) { this.stop(); this.unauthorized(); return }
      if (response.status === 409) throw new Error("Quick Select changed on another screen. Reload before editing again.")
      if (!response.ok || !validFavorites(data)) throw new Error(saving ? "Quick Select could not be confirmed. Reload before editing again." : "Saved Quick Select are unavailable.")
      this.data = data; this.needsReload = false; this.error = ""
      this.publish({ status: "ready", data, needsReload: false, error: "", notice: saving ? "Quick Select saved." : "" })
      return data
    } catch (error) {
      if (this.active && generation === this.generation) fail(connectionError(error))
    } finally {
      if (this.request === request) {
        if (this.timer !== null) this.cancelTimer(this.timer)
        this.request = this.timer = null
        this.saving = false
      }
    }
  }
}

export const FavoritesPage = {
  components: { GxState, GxNotice, GalaxySelect },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  emits: ["close"],
  setup(props) {
    const state = reactive({ status: "idle", data: null, error: "", notice: "", needsReload: false })
    const feed = new FavoritesFeed({ publish: (update) => Object.assign(state, update), unauthorized: props.unauthorized })
    return { state, feed }
  },
  computed: {
    busy() { return ["loading", "saving"].includes(this.state.status) },
    disabled() { return !this.state.data?.editable || this.state.needsReload },
    byKey() { return new Map((this.state.data?.options || []).map((option) => [option.key, option])) },
    groups() { return [...new Set((this.state.data?.options || []).map((option) => option.section || "Controls"))] },
  },
  mounted() { if (this.mode === "local") this.feed.start() },
  beforeUnmount() { this.feed.stop() },
  methods: {
    reportSave,
    select(index, event) { return this.feed.update(index, { key: event.target.value || null }) },
    toggle(index, field, event) {
      const checked = event.target.checked
      return reportSave(() => this.feed.update(index, { [field]: checked }))
    },
    speed(index, event) {
      const value = Number(event.target.value)
      if (!validSpeedValue(value)) return false
      return this.feed.update(index, { value })
    },
    label(index, event) {
      const value = event.target.value.trim().slice(0, 32)
      if (value !== this.state.data.slots[index].label) return this.feed.update(index, { label: value })
    },
  },
  template: `
    <section class="gx-settings gx-favorites" aria-label="Quick Select" :inert="busy" :aria-busy="busy || undefined">
      <div class="gx-settings__header gx-page-header"><div><h2>Quick Select</h2><p>Choose your three driving-screen shortcuts.</p></div>
        </div>
      <div class="gx-favorites__intro">
        <p class="gx-note">Small UI: tap the invisible left, middle or right third. Big UI: tap or swipe the lower-left corner to open Quick Select.</p>
        <p class="gx-note">Assigning a shortcut does not activate it. Each control keeps its usual availability; some settings can only change while parked.</p>
      </div>
      <GxState v-if="mode !== 'local'">Connect to local Galaxy to configure Quick Select.</GxState>
      <template v-else>
        <div class="gx-favorites__status"><span role="status">{{ state.status === 'saving' ? 'Saving Quick Select…' : state.status === 'loading' ? 'Loading Quick Select…' : state.notice }}</span>
          </div>
        <GxNotice tone="danger" v-if="state.error">{{ state.error }}</GxNotice>
        <GxNotice v-if="state.data && !state.data.valid" tone="warn">Saved Quick Select could not be read. Empty slots are shown. Choosing a control will replace the invalid saved configuration.</GxNotice>
        <div v-if="state.data" class="gx-favorites__slots">
          <section v-for="(slot, index) in state.data.slots" :key="index" class="gx-card gx-favorites__slot">
            <div class="gx-section__header"><i class="bi bi-star" aria-hidden="true"></i><span class="gx-section__title">Favorite {{ index + 1 }} <small>{{ ['Left', 'Middle', 'Right'][index] }}</small></span></div>
            <div class="gx-panel gx-stack">
            <div class="gx-row gx-row--borderless"><div class="gx-row__info"><span class="gx-row__label">Enabled</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Enable favorite ' + (index + 1)" :checked="slot.enabled" :disabled="disabled || !slot.key" @change="toggle(index, 'enabled', $event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <label>Control<GalaxySelect class="gx-field gx-field--full" :aria-label="'Quick Select ' + (index + 1) + ' control'" :value="slot.key || ''" :disabled="disabled" @change="select(index, $event)">
              <option value="">Not assigned</option>
              <option v-if="slot.key && !byKey.has(slot.key)" :value="slot.key">{{ slot.label || slot.key }} · Unavailable in this build</option>
              <optgroup v-for="group in groups" :key="group" :label="group"><option v-for="option in state.data.options.filter(item => (item.section || 'Controls') === group)" :key="option.key" :value="option.key">{{ option.label }}</option></optgroup>
            </GalaxySelect></label>
            <label v-if="slot.key === '__starpilot_controller_action__:set_speed'">Set speed (device units)<input class="gx-field" type="number" min="5" max="145" step="1" :value="slot.value ?? 30" :disabled="disabled" @change="speed(index, $event)"></label>
            <label>Label<input class="gx-field" type="text" maxlength="32" :aria-label="'Quick Select ' + (index + 1) + ' label'" :value="slot.label" :disabled="disabled || !slot.key" @change="label(index, $event)"></label>
            <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Show onroad · Big & Small UI</span></div>
              <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Show favorite ' + (index + 1) + ' onroad'" :checked="slot.show_onroad" :disabled="disabled || !slot.enabled || !slot.key" @change="toggle(index, 'show_onroad', $event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <p v-if="slot.key" class="gx-note">{{ state.data.states[index].stateLabel }}<template v-if="state.data.states[index].reason"> · {{ state.data.states[index].reason }}</template></p>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="disabled || !slot.key" @click="feed.update(index, {key:null})">Clear slot</button>
            </div>
          </section>
        </div>
      </template>
    </section>`,
}
