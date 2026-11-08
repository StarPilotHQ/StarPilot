import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { GxState } from "./state.js"
import { menuPlace, settingsSearchPath } from "./menu.js"

// Changed Settings: every saved setting that differs from its default, with resets. Shown on Troubleshoot (embedded).
export const SettingsRecoveryPage = {
  components: { GxDialog, GxNotice, GxState },
  props: { mode: { type: String, default: "local" }, unauthorized: Function, embedded: { type: Boolean, default: false } },
  emits: ["snapshot"],
  data() { return { snapshot: null, selected: [], pending: null, busy: false, error: "", result: "", timer: null } },
  mounted() { if (this.mode !== "local") return; this.refresh(); this.timer = setInterval(() => { if (!this.pending && !this.busy) this.refresh() }, 10000) },
  beforeUnmount() { clearInterval(this.timer) },
  methods: {
    // Where the setting lives in the menu, e.g. "Speed › Driving Personalities › Standard".
    where(row) { return menuPlace(settingsSearchPath({ page: row.page, label: row.label })) },
    async request(path, body) {
      const response = await fetch(path, { method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store",
        headers: body ? { "Content-Type": "application/json" } : {}, ...(body ? { body: JSON.stringify(body) } : {}) })
      const value = await response.json()
      if (response.status === 401 && this.unauthorized) this.unauthorized()
      if (!response.ok) throw new Error(value.error || "Settings are unavailable")
      return value
    },
    async refresh() {
      try {
        const selected = new Set((this.snapshot?.rows || []).filter(row => this.selected.includes(row.id)).map(row => `${row.page}:${row.label}`))
        this.snapshot = await this.request("./api/settings/non-defaults")
        this.selected = this.snapshot.rows.filter(row => row.resetAvailable && selected.has(`${row.page}:${row.label}`)).map(row => row.id)
        this.error = ""
        this.$emit?.("snapshot", this.snapshot)
      } catch (error) { this.error = error.message }
    },
    async review(ids) {
      if (!ids.length || this.busy) return
      this.busy = true
      try { this.pending = await this.request("./api/settings/recovery/preview", { view: this.snapshot.view, selected: ids }) }
      catch (error) { this.error = error.message; await this.refresh() }
      finally { this.busy = false }
    },
    async confirm() {
      this.busy = true
      try {
        const value = await this.request("./api/settings/recovery/confirm", { intent: this.pending.intent, confirmed: true })
        this.result = value.saved ? "Selected settings restored to defaults." : `${value.applied.length} settings restored. ${value.reason}`
        this.pending = null; this.selected = []; await this.refresh()
      } catch (error) { this.error = error.message; this.pending = null; await this.refresh() }
      finally { this.busy = false }
    },
  },
  template: `<section class="gx-settings" :inert="busy" :aria-busy="busy || undefined">
    <header v-if="!embedded" class="gx-settings__header gx-page-header"><div><h2>Changed Settings</h2>
      <p>Review saved settings that differ from this vehicle’s StarPilot defaults.</p></div></header>
    <GxState v-if="mode !== 'local'">Connect to local Galaxy to review saved settings.</GxState>
    <template v-else>
    <GxNotice v-if="snapshot && snapshot.resettableCount < snapshot.count" tone="warn">Some settings are unavailable for this vehicle. Their saved values remain unchanged.</GxNotice>
    <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice><GxNotice v-if="result" tone="success">{{ result }}</GxNotice>
    <GxState v-if="!snapshot && !error" loading>Loading changed settings…</GxState>
    <section class="gx-card" v-if="snapshot"><div class="gx-section__header"><i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i><span class="gx-section__title">Changed settings</span><span class="gx-chip">{{ snapshot.count }}</span></div>
      <p v-if="embedded && snapshot.rows.length" class="gx-note gx-inset">Settings that differ from this car's defaults. Reset any of them here.</p>
      <p v-if="!snapshot.rows.length" class="gx-note gx-inset">All available settings use their defaults.</p>
      <div v-for="row in snapshot.rows" :key="row.id" class="gx-row gx-row--wrap">
        <div class="gx-row__info"><label class="gx-row__label"><input type="checkbox" v-model="selected" :value="row.id" :disabled="busy || !row.resetAvailable"> {{ row.label }}</label>
          <span class="gx-row__desc"><template v-if="where(row)">{{ where(row) }} · </template>Current: <strong>{{ row.current }}</strong> · Default: {{ row.default }}</span>
          <span v-if="!row.available" class="gx-row__desc">{{ row.reason }}</span></div>
        <button class="gx-btn gx-btn--tonal" @click="review([row.id])" :disabled="busy || !row.resetAvailable">Reset to Default</button>
      </div>
      <div class="gx-panel gx-actions">
        <button class="gx-btn gx-btn--tonal" @click="review(selected)" :disabled="busy || !selected.length">Reset Selected</button>
        <button class="gx-btn" @click="review(snapshot.rows.filter(row => row.resetAvailable).map(row => row.id))" :disabled="busy || !snapshot.resettableCount">Reset All Non-Default Settings</button>
        <button class="gx-btn gx-btn--tonal" @click="refresh" :disabled="busy">Refresh</button>
      </div>
    </section></template>
    <GxDialog v-if="pending" labelledby="recovery-question" @close="pending = null">
      <h2 id="recovery-question">{{ pending.question }}</h2><ul><li v-for="label in pending.labels" :key="label">{{ label }}</li></ul>
      <div class="gx-actions"><button class="gx-btn gx-btn--tonal" @click="pending = null" :disabled="busy">Cancel</button><button class="gx-btn" @click="confirm" :disabled="busy">Reset Settings</button></div>
    </GxDialog></section>`,
}
