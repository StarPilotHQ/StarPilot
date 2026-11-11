import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { GxState } from "./state.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"

const validApp = app => app && typeof app === 'object' && typeof app.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(app.id) &&
  typeof app.name === 'string' && app.name.length <= 80 && ['android', 'ios'].includes(app.source) &&
  typeof app.messaging === 'boolean' && typeof app.allowed === 'boolean' && typeof app.chosen === 'boolean'

export function validPhoneNotifications(value) {
  return !!value && typeof value.enabled === 'boolean' && typeof value.hideWhileMoving === 'boolean' &&
    Array.isArray(value.apps) && value.apps.length <= 64 && value.apps.every(validApp)
}

export const PhoneNotifications = {
  components: { GxNotice, GxState, GalaxySettingRow },
  props: { mode: String, unauthorized: Function },
  data: () => ({ snapshot: null, busy: false, error: '', timer: null, controller: null, generation: 0, active: false }),
  mounted() {
    this.visibility = () => { this.stop(); this.start() }
    document.addEventListener('visibilitychange', this.visibility)
    this.start()
  },
  beforeUnmount() { document.removeEventListener('visibilitychange', this.visibility); this.stop() },
  watch: { mode() { this.stop(); this.start() } },
  methods: {
    switchRow,
    start() { if (this.mode !== 'sample' && !document.hidden) { this.active = true; this.refresh() } },
    stop() { this.active = false; this.generation++; clearTimeout(this.timer); this.timer = null; this.controller?.abort(); this.busy = false },
    adopt(value) {
      if (!validPhoneNotifications(value)) throw new Error('Invalid phone notification settings')
      this.snapshot = value
    },
    async refresh() {
      if (!this.active || this.busy) return
      clearTimeout(this.timer); this.controller?.abort()
      const generation = ++this.generation
      this.controller = new AbortController()
      try {
        const value = await requestJson('./api/starport/notifications', { request: { signal: this.controller.signal } })
        if (!this.active || generation !== this.generation) return
        this.adopt(value); this.error = ''
      } catch (error) {
        if (!this.active || generation !== this.generation) return
        if (error.status === 401) { this.stop(); this.unauthorized(); return }
        this.error = error.message
      } finally {
        if (this.active && generation === this.generation) this.timer = setTimeout(() => this.refresh(), 5000)
      }
    },
    async save(patch) {
      if (!this.active || this.busy) return false
      clearTimeout(this.timer); this.controller?.abort()
      const generation = ++this.generation
      this.controller = new AbortController(); this.busy = true; this.error = ''
      try {
        const value = await requestJson('./api/starport/notifications', { request: { signal: this.controller.signal,
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) } })
        if (!this.active || generation !== this.generation) return false
        this.adopt(value)
        return true
      } catch (error) {
        if (!this.active || generation !== this.generation) return false
        if (error.status === 401) { this.stop(); this.unauthorized(); return false }
        this.error = error.message
        return false
      } finally {
        if (this.active && generation === this.generation) { this.busy = false; this.timer = setTimeout(() => this.refresh(), 5000) }
      }
    },
  },
  template: `<section class="gx-card gx-starport-step" aria-labelledby="gx-phone-notifications-title">
    <div class="gx-starport-step__head">
      <span class="gx-starport-step__num" aria-hidden="true"><i class="bi bi-chat-dots"></i></span>
      <div><h3 id="gx-phone-notifications-title">Phone Notifications</h3><p>Show messages from your phone on the car screen. Each one shows for a few seconds and is never stored.</p></div>
    </div>
    <div class="gx-starport-step__body">
      <GxState v-if="!snapshot && !error" loading>Loading phone notification settings…</GxState>
      <template v-if="snapshot">
        <GalaxySettingRow :row="switchRow('Show phone notifications on the car screen', snapshot.enabled)" :index="0" :busy="busy"
          :save-value="(index, value) => save({ enabled: value === 'On' })" />
        <GalaxySettingRow :row="switchRow('Hide while moving', snapshot.hideWhileMoving)" :index="1" :busy="busy" :disabled="!snapshot.enabled"
          :save-value="(index, value) => save({ hideWhileMoving: value === 'On' })" />
      </template>
      <ol>
        <li><strong>Android:</strong> in the StarPilot app, open Connections → Phone sharing → Share notifications.</li>
        <li><strong>iPhone:</strong> in the app, open Connection settings → Phone notifications, then allow notification sharing when iOS asks.</li>
      </ol>
      <template v-if="snapshot">
        <p v-if="!snapshot.apps.length" class="gx-note">Apps appear here after they send a notification. Messaging apps are on by default; others start off.</p>
        <template v-else>
          <p class="gx-note">Apps that have sent something. Messaging apps are on by default; others start off.</p>
          <GalaxySettingRow v-for="(app, index) in snapshot.apps" :key="app.id" :row="switchRow(app.name || app.id, app.allowed)" :index="index"
            :busy="busy" :disabled="!snapshot.enabled" :save-value="(i, value) => save({ apps: { [app.id]: value === 'On' } })" />
        </template>
      </template>
      <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
    </div>
  </section>`,
}
