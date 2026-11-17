import { GxNotice } from "./notice.js"
import { beginSave, finishSave } from "./save-feedback.js"
export const validDriveState = (value) => !!value && ["auto", "offroad", "onroad"].includes(value.mode) &&
  typeof value.available === "boolean" && typeof value.overrideAllowed === "boolean" &&
  (value.revision === null || typeof value.revision === "string" && /^[0-9a-f]{32}$/.test(value.revision)) &&
  [null, "offroad", "onroad"].includes(value.effective)

export const DriveStatePanel = {
  components: { GxNotice },
  props: { offroadOnly: { type: Boolean, default: false }, lockout: { type: Boolean, default: true } },
  data: () => ({ state: null, error: "", statusError: "", busy: false }),
  computed: {
    visible() { return !this.offroadOnly || this.lockout || this.state?.mode === 'offroad' || !!this.error },
    pending() { return this.state?.mode !== "auto" && this.state?.effective !== this.state?.mode },
  },
  mounted() {
    this.active = true
    this.generation = 0
    this.load()
    this.timer = setInterval(() => { if (!document.hidden) this.load() }, 1000)
  },
  beforeUnmount() { this.active = false; this.generation++; clearInterval(this.timer); this.controller?.abort() },
  methods: {
    async load() {
      if (this.busy || this.loading || !this.active) return
      this.loading = true
      const generation = ++this.generation
      this.controller = new AbortController()
      const controller = this.controller
      const deadline = setTimeout(() => controller.abort(), 3000)
      try {
        const response = await fetch("./api/drive-state/status", { credentials: "same-origin", cache: "no-store", signal: this.controller.signal })
        const value = await response.json()
        if (!response.ok || !validDriveState(value)) throw new Error(value.error || "Drive state unavailable")
        if (this.active && generation === this.generation) { this.state = value; this.statusError = "" }
      } catch (error) { if (this.active && generation === this.generation) { this.state = null; this.statusError = "Drive state unavailable" } }
      finally { clearTimeout(deadline); if (generation === this.generation) this.loading = false }
    },
    async toggleOffroad(event) {
      const input = event.target
      const mode = input.checked ? 'offroad' : 'auto'
      input.checked = this.state?.mode === 'offroad'
      await this.change(mode)
      input.checked = this.state?.mode === 'offroad'
    },
    async change(mode) {
      if (this.busy || !this.state?.available) return
      if (!["onroad", "offroad", "auto"].includes(mode)) return
      this.error = ""
      const feedback = beginSave()
      const revision = this.state.revision
      this.busy = true
      this.loading = false
      const generation = ++this.generation
      this.controller?.abort()
      this.controller = new AbortController()
      const controller = this.controller
      const deadline = setTimeout(() => controller.abort(), 3000)
      try {
        const response = await fetch("./api/drive-state/action", { method: "POST", credentials: "same-origin", cache: "no-store",
          signal: this.controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode, revision }) })
        const value = await response.json()
        if (!response.ok || !validDriveState(value)) throw new Error(value.error || "Drive state unavailable")
        if (this.active && generation === this.generation) { this.state = value; this.error = ""; finishSave(feedback, true) }
      } catch (error) { if (this.active && generation === this.generation) this.error = error.message; finishSave(feedback, false) }
      finally { clearTimeout(deadline); this.busy = false; if (this.active && generation === this.generation) this.load() }
    },
  },
  template: `<section v-if="visible" class="gx-card gx-force-drive" aria-label="Force Drive State" :inert="busy" :aria-busy="busy || undefined"><div v-if="!offroadOnly" class="gx-section__header"><i class="bi bi-car-front" aria-hidden="true"></i><span class="gx-section__title">Force Drive State</span></div><div class="gx-panel">
    <GxNotice tone="danger" v-if="error || statusError">{{ error || statusError }}</GxNotice>
    <GxNotice tone="danger" v-if="state && !state.available">The drive-state manager is unavailable. Reconnect to check again.</GxNotice>
    <GxNotice v-if="pending" tone="info">Waiting for the device to switch {{ state.mode }}…</GxNotice>
    <p v-if="!offroadOnly">Device: {{ state?.effective === "onroad" ? "Onroad" : state?.effective === "offroad" ? "Offroad" : "Checking…" }}</p>
    <p v-if="!offroadOnly">Auto follows the car. Force Onroad starts driving services at any time. Return to Auto restores normal operation.</p>
    <label v-if="offroadOnly" class="gx-row gx-row--borderless"><span class="gx-row__info"><span class="gx-row__label">Force device offroad</span><small class="gx-row__desc">Park the vehicle and disengage controls to enable. Turn off to restore automatic drive state.</small></span><span class="gx-switch"><input type="checkbox" role="switch"
      :checked="state?.mode === 'offroad'" :disabled="!state?.available"
      @change="toggleOffroad" /><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
    <div v-if="!offroadOnly" class="gx-force-drive__actions gx-actions"><button v-for="mode in ['onroad', 'auto']" :key="mode" type="button"
      class="gx-btn" :class="state?.mode === mode ? '' : 'gx-btn--tonal'"
      :disabled="!state?.available"
      :aria-pressed="state?.mode === mode" @click="change(mode)">{{ mode === 'auto' ? 'Return to Auto' : 'Onroad' }}</button></div>
    </div></section>`,
}
