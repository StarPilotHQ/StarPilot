import { BluetoothTabs } from "./bluetooth-tabs.js"
import { guardUnload } from "./unload-guard.js"
import { GxNotice } from "./notice.js"
import { reportSave } from "./save-feedback.js"
import { reactive } from "../vendor/vue/vue.esm-browser.js"

const HEX = /^[0-9a-f]{64}$/
const string = (value, limit = 128) => typeof value === "string" && value.length <= limit && !/[\x00-\x1f\x7f]/.test(value)
const key = (value) => value === null || (string(value) && value.length > 0)
const integer = (value, max) => Number.isInteger(value) && value >= 0 && value <= max
const draftFrom = (status) => ({ revision: status.revision, enabled: status.enabled,
  slots: status.slots.slice(3).map((slot) => slot.key) })

export function validControllersStatus(data) {
  if (data?.version !== 1 || typeof data.available !== "boolean" || typeof data.editable !== "boolean" ||
      typeof data.enabled !== "boolean" || typeof data.valid !== "boolean" || !HEX.test(data.revision) ||
      !Array.isArray(data.devices) || data.devices.length > 32 || !Array.isArray(data.slots) || data.slots.length !== 13 ||
      !Array.isArray(data.options) || data.options.length > 128 || !Array.isArray(data.bindings) || data.bindings.length > 128 ||
      typeof data.testing !== "boolean") return false
  if (!data.devices.every((device) => string(device?.id, 256) && device.id && string(device.name, 80) &&
      [3, 5].includes(device.bus))) return false
  if (!data.slots.every((slot, index) => slot?.index === index && string(slot.label, 80) &&
      key(slot.key) && typeof slot.available === "boolean")) return false
  if (!data.options.every((option) => key(option?.key) && option.key !== null &&
      string(option.label, 80) && string(option.section, 80))) return false
  if (new Set(data.options.map((option) => option.key)).size !== data.options.length) return false
  if (!data.bindings.every((binding) => string(binding?.deviceId, 256) && binding.deviceId &&
      string(binding.name, 80) && integer(binding.code, 65551) && integer(binding.slot, 12))) return false
  if (data.learning !== null && (!integer(data.learning?.slot, 12) ||
      !Number.isFinite(data.learning.expiresIn) || data.learning.expiresIn < 0 || data.learning.expiresIn > 20)) return false
  if (data.lastPress !== null && (!string(data.lastPress?.deviceId, 256) ||
      !integer(data.lastPress.code, 65551) || (data.lastPress.slot !== null && !integer(data.lastPress.slot, 12)) ||
      typeof data.lastPress.executed !== "boolean" || !string(data.lastPress.message, 200))) return false
  return true
}

export class ControllersFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.request = this.timeout = this.poll = null
    this.status = null
    this.busy = false
    this.error = ""
  }

  emit(replaceDraft = false) {
    this.publish({ status: this.status, busy: this.busy, error: this.error,
      ...(replaceDraft && this.status ? { draft: draftFrom(this.status) } : {}) })
  }
  stop() {
    if (this.active && this.status?.editable && (this.status.learning || this.status.testing)) {
      this.fetcher("./api/controllers/action", { method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation: "cancel" }) }).catch(() => {})
    }
    this.active = false
    this.generation++
    this.request?.abort()
    if (this.timeout !== null) this.cancelTimer(this.timeout)
    if (this.poll !== null) this.cancelTimer(this.poll)
    this.request = this.timeout = this.poll = null
    this.status = null
    this.busy = false
    this.error = ""
    this.emit()
  }
  start() { this.stop(); this.active = true; return this.refresh() }

  async run(payload = null, replaceDraft = false) {
    if (!this.active) return null
    if (this.request) {
      if (payload === null || this.busy) return null
      this.request.abort()
      this.cancelTimer(this.timeout)
      this.generation++
    }
    if (this.poll !== null) this.cancelTimer(this.poll)
    this.poll = null
    const generation = this.generation, request = new AbortController()
    this.request = request
    this.busy = payload !== null || this.status === null
    this.emit()
    this.timeout = this.later(() => request.abort(), 6000)
    let accepted = false
    let failure = "Controller Buttons are unavailable. Reconnecting automatically…"
    try {
      const response = await this.fetcher(payload === null ? "./api/controllers/status" : "./api/controllers/action", {
        credentials: "same-origin", cache: "no-store", signal: request.signal,
        ...(payload === null ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
      })
      if (!this.active || generation !== this.generation || request.signal.aborted) return null
      if (response.status === 401) { this.stop(); this.unauthorized(); return null }
      if (response.status === 403) { failure = "Turn off the vehicle to change controller buttons."; throw new Error() }
      if (response.status === 409) { failure = "Controller settings changed. Reload them before saving."; throw new Error() }
      if (response.status === 429) { failure = "Controller is busy. Try again shortly."; throw new Error() }
      if (!response.ok) throw new Error()
      const data = await response.json()
      if (!this.active || generation !== this.generation || request.signal.aborted) return null
      if (!validControllersStatus(data)) { failure = "Controller Buttons returned an unsupported status. Try again."; throw new Error() }
      this.status = data
      accepted = true
      this.error = ""
      return data
    } catch {
      if (this.active && generation === this.generation) this.error = failure
      return null
    } finally {
      if (this.request === request) {
        if (this.timeout !== null) this.cancelTimer(this.timeout)
        this.request = this.timeout = null
        this.busy = false
        this.emit(accepted && (replaceDraft || payload?.operation === "save"))
        if (this.active && this.poll === null) this.poll = this.later(() => { this.poll = null; this.refresh() }, 2000)
      }
    }
  }
  refresh(replaceDraft = false) { return this.run(null, replaceDraft) }
  action(payload) { return this.run(payload) }
}

export const ControllersPage = {
  components: { GxNotice, BluetoothTabs },
  props: { go: { type: Function, default: path => { location.hash = path } }, mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  setup(props) {
    const state = reactive({ status: null, busy: false, error: "", draft: null })
    const feed = new ControllersFeed({ unauthorized: props.unauthorized, publish(update) {
      const prior = state.status
      Object.assign(state, update)
      if (update.status === null) state.draft = null
      else if (update.status && (!state.draft || (prior && state.draft.revision === prior.revision &&
          state.draft.enabled === prior.enabled && state.draft.slots.every((value, index) => value === prior.slots[index + 3].key)))) {
        state.draft = draftFrom(update.status)
      }
    } })
    return { state, feed }
  },
  computed: {
    dirty() { return !!this.state.status && !!this.state.draft &&
      (this.state.draft.enabled !== this.state.status.enabled ||
       this.state.draft.slots.some((value, index) => value !== this.state.status.slots[index + 3].key)) },
    changed() { return !!this.state.status && !!this.state.draft && this.state.draft.revision !== this.state.status.revision },
    editable() { return !!this.state.status?.editable && !this.changed },
    canEdit() { return this.editable && !this.state.busy },
    favorites() { return this.state.status?.slots.slice(0, 3) || [] },
    actions() { return this.state.status?.slots.slice(3) || [] },
  },
  mounted() {
    this._stopUnloadGuard = guardUnload(() => this.dirty || this.state.busy)
    this.visibility = () => { if (this.mode === "local" && !document.hidden) this.feed.start(); else this.feed.stop() }
    document.addEventListener("visibilitychange", this.visibility)
    this.visibility()
  },
  beforeUnmount() { this._stopUnloadGuard(); document.removeEventListener("visibilitychange", this.visibility); this.feed.stop() },
  watch: { mode() { this.visibility() } },
  methods: {
    reload() { return this.feed.refresh(true) },
    async save() {
      if (!this.canEdit || !this.dirty) return
      const saved = await reportSave(() => this.feed.action({ operation: "save", revision: this.state.draft.revision,
        enabled: this.state.draft.enabled, slots: this.state.draft.slots.map((value) => value || null) }))
      if (!saved && this.state.status) this.state.draft = draftFrom(this.state.status)
      return saved
    },
    learn(slot) {
      if (this.canEdit && !this.dirty && integer(slot, 12)) return this.feed.action({ operation: "learn", revision: this.state.status.revision, slot })
    },
    cancel() { if (this.state.status?.learning) return this.feed.action({ operation: "cancel" }) },
    test() { if (this.canEdit) return this.feed.action({ operation: "test", enabled: !this.state.status.testing }) },
    remove(binding) {
      if (this.canEdit && !this.dirty) return this.feed.action({ operation: "remove", revision: this.state.status.revision,
        deviceId: binding.deviceId, code: binding.code })
    },
  },
  template: `
    <section class="gx-driving" aria-label="Controllers">
      <header class="gx-settings__header gx-page-header"><div><h2>Controllers</h2><p>Assign buttons and test your connected controllers.</p></div></header>
      <BluetoothTabs active="controllers" :go="go" />
    <section class="gx-card gx-controllers" aria-label="Controller Buttons" :inert="state.busy" :aria-busy="state.busy || undefined">
      <div class="gx-section__header"><i class="bi bi-controller" aria-hidden="true"></i><span class="gx-section__title">Controller Buttons</span></div>
      <div class="gx-panel gx-stack">
      <p>Assign USB or Bluetooth controller buttons to Quick Select and driving screen actions. This does not control steering, gas, or brakes. Turn off the vehicle to edit.</p>
      <p v-if="mode !== 'local'" class="gx-note">Connect to local Galaxy to manage controller buttons.</p>
      <template v-else>
        <GxNotice :tone="state.error ? 'danger' : 'info'" v-if="state.error || !state.status || !state.status.available">
          {{ state.error || (!state.status ? 'Checking attached controllers…' : 'Controller Buttons are unavailable. Reconnecting automatically…') }}
        </GxNotice>
        <template v-if="state.status?.available">
          <GxNotice v-if="!state.status.editable" tone="warn">Turn off the vehicle to change controller buttons.</GxNotice>
          <GxNotice v-if="changed" tone="warn">Controller settings changed. Reload before saving.</GxNotice>
          <GxNotice v-if="dirty" tone="warn">Save these changes before learning or removing buttons.</GxNotice>
          <div class="gx-driving__actions gx-actions"><button class="gx-btn gx-btn--tonal" type="button" @click="reload">Reload</button></div>
          <div class="gx-row gx-row--borderless"><div class="gx-row__info"><span class="gx-row__label">Enable assigned controller buttons</span></div>
            <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Enable assigned controller buttons" v-model="state.draft.enabled" :disabled="!canEdit" @change="save"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
          <h4>Attached Controllers</h4>
          <p v-if="!state.status.devices.length" class="gx-note">No controller is attached. Plug in a USB controller or <a class="gx-link" href="#/bluetooth">pair in Bluetooth</a>.</p>
          <div v-for="device in state.status.devices" :key="device.id" class="gx-row gx-controllers__row"><div class="gx-row__info"><span class="gx-row__label">{{ device.name }}</span><span class="gx-row__desc">{{ device.bus === 3 ? 'USB' : 'Bluetooth' }}</span></div></div>
          <h4>Favorite Buttons</h4>
          <div v-for="slot in favorites" :key="slot.index" class="gx-row gx-controllers__row"><div class="gx-row__info"><span class="gx-row__label">{{ slot.label }}</span>
            <span class="gx-row__desc">{{ slot.key ? 'Available when its control is ready' : 'Not assigned' }}</span></div>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!editable || dirty || !!state.status.learning" @click="learn(slot.index)">Learn Button</button></div>
          <h4>Action Buttons</h4>
          <div v-for="slot in actions" :key="slot.index" class="gx-row gx-controllers__row"><label class="gx-row__info gx-controllers__selector"><span class="gx-row__label">{{ slot.label }}</span>
              <select class="gx-field" :aria-label="slot.label + ' action'" v-model="state.draft.slots[slot.index - 3]" :disabled="!canEdit" @change="save">
                <option :value="null">No action</option><option v-for="option in state.status.options" :key="option.key" :value="option.key">{{ option.section }} · {{ option.label }}</option>
              </select></label>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!editable || dirty || !!state.status.learning" @click="learn(slot.index)">Learn Button</button></div>
          <p v-if="state.status.learning" role="status">Press a button for {{ state.status.slots[state.status.learning.slot].label }} within {{ Math.ceil(state.status.learning.expiresIn) }} seconds.
            <button class="gx-btn gx-btn--tonal" type="button" @click="cancel">Cancel Learning</button></p>
          <div class="gx-driving__actions gx-actions"><button class="gx-btn gx-btn--tonal" type="button" :disabled="!editable" @click="test">{{ state.status.testing ? 'Stop test' : 'Test buttons (20s)' }}</button></div>
          <p v-if="state.status.testing" class="gx-note">Button test shows input without running its action.</p>
          <GxNotice v-if="state.status.lastPress" tone="info">{{ state.status.lastPress.message }}</GxNotice>
          <h4>Learned Buttons</h4><p v-if="!state.status.bindings.length" class="gx-note">No buttons learned yet.</p>
          <div v-for="binding in state.status.bindings" :key="binding.deviceId + ':' + binding.code" class="gx-row gx-controllers__row">
            <div class="gx-row__info"><span class="gx-row__label">{{ binding.name }}</span><span class="gx-row__desc">Button {{ binding.code }} · {{ state.status.slots[binding.slot].label }}</span></div>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!editable || dirty" @click="remove(binding)">Remove</button></div>
        </template>
      </template></div>
    </section></section>`,
}
