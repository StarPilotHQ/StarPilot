import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { GxState } from "./state.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"

const validApp = app => app && typeof app === 'object' && typeof app.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(app.id) &&
  typeof app.name === 'string' && app.name.length <= 80 && ['android', 'ios'].includes(app.source) &&
  typeof app.messaging === 'boolean' && typeof app.allowed === 'boolean' && typeof app.chosen === 'boolean'

const OUTCOMES = ['shown', 'appOff', 'notMessage', 'tooOld', 'disabled']
const validRecent = row => row && typeof row === 'object' && typeof row.app === 'string' && typeof row.appName === 'string' &&
  OUTCOMES.includes(row.outcome) && Number.isFinite(row.ageSeconds)
const validShowing = value => value === null || value === undefined || (typeof value === 'object' && typeof value.appName === 'string' &&
  Number.isFinite(value.remainingSeconds) && ['sender', 'text'].every(key => value[key] === undefined || typeof value[key] === 'string'))

export function validPhoneNotifications(value) {
  return !!value && typeof value.enabled === 'boolean' && typeof value.hideWhileMoving === 'boolean' &&
    Array.isArray(value.apps) && value.apps.length <= 64 && value.apps.every(validApp) &&
    (value.recent === undefined || (Array.isArray(value.recent) && value.recent.length <= 8 && value.recent.every(validRecent))) &&
    validShowing(value.showing)
}

export const OUTCOME_TEXT = Object.freeze({
  shown: 'Shown on screen', appOff: 'Not shown: this app is off', notMessage: 'Not shown: not a messaging app; turn it on under Apps',
  tooOld: 'Not shown: arrived too late', disabled: 'Not shown: phone notifications are off',
})

export const ago = seconds => seconds < 60 ? `${seconds} s ago` : seconds < 3600 ? `${Math.floor(seconds / 60)} min ago` : `${Math.floor(seconds / 3600)} h ago`

export const PhoneNotifications = {
  components: { GxNotice, GxState, GalaxySettingRow },
  props: { mode: String, unauthorized: Function },
  computed: { outcomeText: () => OUTCOME_TEXT },
  data: () => ({ snapshot: null, busy: false, error: '', testResult: '', setupOpen: false, timer: null, controller: null, generation: 0, active: false }),
  mounted() {
    this.visibility = () => { this.stop(); this.start() }
    document.addEventListener('visibilitychange', this.visibility)
    this.start()
  },
  beforeUnmount() { document.removeEventListener('visibilitychange', this.visibility); this.stop() },
  watch: { mode() { this.stop(); this.start() } },
  methods: {
    switchRow, ago,
    start() { if (this.mode !== 'sample' && !document.hidden) { this.active = true; this.refresh() } },
    stop() { this.active = false; this.generation++; clearTimeout(this.timer); this.timer = null; this.controller?.abort(); this.busy = false },
    adopt(value) {
      if (!validPhoneNotifications(value)) throw new Error('Invalid phone notification settings')
      // Saves return settings only; keep the live part until the next poll.
      const live = this.snapshot || { recent: [], showing: null, contentVisible: false }
      this.snapshot = { recent: live.recent, showing: live.showing, contentVisible: live.contentVisible, ...value }
    },
    pollMs() { return this.snapshot?.contentVisible ? 1000 : 5000 },
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
        if (this.active && generation === this.generation) this.timer = setTimeout(() => this.refresh(), this.pollMs())
      }
    },
    async sendTest() {
      if (!this.active || this.busy) return false
      this.busy = true; this.error = ''; this.testResult = ''
      try {
        const value = await requestJson('./api/starport/notifications/test', { request: {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' } })
        if (!this.active) return false
        this.testResult = value?.accepted ? '' : value?.enabled === false ? 'Turn on Show phone notifications first.' : 'The test was not shown. Try again.'
        this.busy = false
        this.refresh()
        return !!value?.accepted
      } catch (error) {
        if (!this.active) return false
        if (error.status === 401) { this.stop(); this.unauthorized(); return false }
        this.error = error.message
        return false
      } finally {
        if (this.active) this.busy = false
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
        if (this.active && generation === this.generation) { this.busy = false; this.timer = setTimeout(() => this.refresh(), this.pollMs()) }
      }
    },
  },
  template: `<div class="gx-settings__body">
    <section v-if="snapshot" class="gx-card gx-settings__section" aria-labelledby="gx-phone-incoming-title">
      <div class="gx-section__header"><i class="bi bi-broadcast" aria-hidden="true"></i>
        <span id="gx-phone-incoming-title" class="gx-section__title">Incoming</span>
        <button type="button" class="gx-icon-btn" title="Send test notification" aria-label="Send test notification"
          :disabled="busy || !snapshot.enabled" @click="sendTest"><i class="bi bi-send" aria-hidden="true"></i></button>
      </div>
      <div v-if="snapshot.showing" class="gx-row" role="status">
        <div class="gx-row__info">
          <span class="gx-row__label">{{ snapshot.showing.sender || snapshot.showing.appName }}</span>
          <span class="gx-row__desc">{{ snapshot.contentVisible ? (snapshot.showing.text || snapshot.showing.appName) : 'Message text shows only when Galaxy is opened locally' }}</span>
        </div>
        <span class="gx-row__value">On screen · {{ snapshot.showing.remainingSeconds }} s</span>
      </div>
      <p v-if="testResult" class="gx-empty" role="status">{{ testResult }}</p>
      <p v-if="!snapshot.showing && !snapshot.recent.length" class="gx-empty">Nothing yet. Send a test or text yourself; it shows here as it arrives.</p>
      <div v-for="(row, index) in snapshot.recent" :key="index + row.app + row.ageSeconds" class="gx-row">
        <div class="gx-row__info">
          <span class="gx-row__label">{{ row.appName }}</span>
          <span class="gx-row__desc">{{ outcomeText[row.outcome] }}</span>
        </div>
        <span class="gx-row__value">{{ ago(row.ageSeconds) }}</span>
      </div>
    </section>
    <section class="gx-card gx-settings__section" aria-labelledby="gx-phone-notifications-title">
      <div class="gx-section__header"><i class="bi bi-phone" aria-hidden="true"></i>
        <span id="gx-phone-notifications-title" class="gx-section__title">Phone notifications</span>
      </div>
      <GxState v-if="!snapshot && !error" loading>Loading phone notification settings…</GxState>
      <template v-if="snapshot">
        <GalaxySettingRow :row="switchRow('Show phone notifications', snapshot.enabled, 'On the comma screen and Android Auto for 8 seconds; never stored')" :index="0" :busy="busy"
          :save-value="(index, value) => save({ enabled: value === 'On' })" />
        <GalaxySettingRow :row="switchRow('Hide while moving', snapshot.hideWhileMoving, 'Only show them while stopped')" :index="1" :busy="busy" :disabled="!snapshot.enabled"
          :save-value="(index, value) => save({ hideWhileMoving: value === 'On' })" />
      </template>
    </section>
    <section v-if="snapshot" class="gx-card gx-settings__section" aria-labelledby="gx-phone-apps-title">
      <div class="gx-section__header"><i class="bi bi-grid" aria-hidden="true"></i>
        <span id="gx-phone-apps-title" class="gx-section__title">Apps</span>
        <span v-if="snapshot.apps.length" class="gx-section__count">{{ snapshot.apps.length }}</span>
      </div>
      <p v-if="!snapshot.apps.length" class="gx-empty">Apps appear here after they send a notification. Messaging apps start on; others start off.</p>
      <GalaxySettingRow v-for="(app, index) in snapshot.apps" :key="app.id" :row="switchRow(app.name || app.id, app.allowed, app.messaging ? 'Messaging' : '')" :index="index"
        :busy="busy" :disabled="!snapshot.enabled" :save-value="(i, value) => save({ apps: { [app.id]: value === 'On' } })" />
    </section>
    <section class="gx-card gx-settings__section">
      <button type="button" class="gx-section__header gx-section__header--toggle" :aria-expanded="setupOpen" @click="setupOpen = !setupOpen">
        <i class="bi bi-question-circle" aria-hidden="true"></i><span class="gx-section__title">Set up a phone</span>
        <i class="bi" :class="setupOpen ? 'bi-chevron-up' : 'bi-chevron-down'" aria-hidden="true"></i></button>
      <template v-if="setupOpen">
        <p class="gx-empty"><strong>Android:</strong> StarPilot app → Connections → Phone sharing → Share notifications.</p>
        <p class="gx-empty"><strong>iPhone:</strong> app → Connection settings → Phone notifications, then allow sharing when iOS asks.</p>
        <p class="gx-empty">Tap Send test notification in the phone app to check the whole path from your phone.</p>
      </template>
    </section>
    <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
  </div>`,
}
