import { GxNotice } from "./notice.js"
import { reportSave } from "./save-feedback.js"
export const channels = ["webPush", "discord", "webhook", "ntfy"]
export const labels = { webPush: "Browser push", discord: "Discord", webhook: "General webhook", ntfy: "ntfy" }

export function validNotifications(value) {
  return value?.schemaVersion === 1 && typeof value.queueFull === "boolean" &&
    Number.isInteger(value.subscriptionCount) && value.subscriptionCount >= 0 && value.subscriptionCount <= 16 &&
    Array.isArray(value.subscriptions) && value.subscriptions.length === value.subscriptionCount &&
    value.subscriptions.every(s => typeof s?.id === "string" && /^[0-9a-f]{32}$/.test(s.id) && Object.keys(s).length === 1) &&
    typeof value.deliverySemantics === "string" && value.deliverySemantics.length < 256 &&
    (value.applicationServerKey === undefined || typeof value.applicationServerKey === "string" && /^[A-Za-z0-9_-]{87}$/.test(value.applicationServerKey)) &&
    channels.every(name => {
      const c = value.channels?.[name]
      return typeof c?.enabled === "boolean" && typeof c.configured === "boolean" && Number.isInteger(c.pending) && c.pending >= 0 && c.pending <= 512 &&
        ["idle", "queued", "sending", "sent", "failed", "cancelled"].includes(c.lastState) &&
        ["", "http", "expired", "timeout", "transport", "delivery_unknown", "stale"].includes(c.lastError)
    })
}

export class NotificationClient {
  constructor({ publish, unauthorized, fetcher = (...args) => fetch(...args) }) {
    this.publish = publish; this.unauthorized = unauthorized; this.fetcher = fetcher
    this.revision = 0; this.controller = null; this.pending = false; this.hasStatus = false; this.timer = null
  }
  stop() { this.revision++; this.controller?.abort(); this.controller = null; this.pending = false; clearTimeout(this.timer); this.timer = null }
  async run(payload = null) {
    this.stop()
    const revision = this.revision
    this.controller = new AbortController()
    this.pending = true
    const foreground = payload !== null || !this.hasStatus
    if (foreground) this.publish({ notificationBusy: true, notificationError: "", notificationTesting: payload?.action === "test" })
    this.timer = setTimeout(() => {
      if (revision !== this.revision) return
      this.stop()
      this.publish({ notificationBusy: false, notificationError: "Notification request timed out. Refresh status before repeating a test." })
    }, payload?.action === "test" ? 25000 : 4000)
    try {
      const response = await this.fetcher("./api/sentry/notifications", { credentials: "same-origin", cache: "no-store",
        signal: this.controller.signal, ...(payload === null ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }) })
      if (revision !== this.revision) return false
      if (response.status === 401) { this.unauthorized(); return false }
      const value = await response.json()
      if (revision !== this.revision) return false
      if (!response.ok) throw new Error(value?.error || "Sentry notifications are unavailable.")
      if (!validNotifications(value)) throw new Error("Notification status is unavailable.")
      this.hasStatus = true
      this.publish({ notifications: value, notificationBusy: false, notificationError: "" })
      return true
    } catch (error) {
      if (revision === this.revision && error.name !== "AbortError") this.publish({ notificationBusy: false, notificationError: error.message || "Notification request failed." })
      return false
    } finally {
      if (revision === this.revision) { clearTimeout(this.timer); this.timer = null; this.pending = false; if (foreground) this.publish({ notificationBusy: false }) }
    }
  }
}

export const SentryNotifications = {
  components: { GxNotice },
  name: "SentryNotifications",
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ nativePush: !!window.StarPilotApp?.sentryPush, pushState: {}, testPoll: null, relayUrl: '', enrollmentCode: '', relayConfirmed: false, channels, labels, notifications: null, notificationBusy: false, notificationTesting: false, notificationError: "",
    channelSetup: {},
    drafts: Object.fromEntries(channels.map(name => [name, { url: "", token: "" }])) }),
  created() { this.client = new NotificationClient({ publish: update => Object.assign(this.$data, update), unauthorized: this.unauthorized }) },
  mounted() {
    this.visibility = () => { if (document.hidden) { this.client.stop(); clearInterval(this.poll) } else this.start() }
    document.addEventListener("visibilitychange", this.visibility)
    this.start()
  },
  beforeUnmount() { clearInterval(this.testPoll); this.client.stop(); clearInterval(this.poll); document.removeEventListener("visibilitychange", this.visibility) },
  computed: { relay() { return this.notifications?.relayEnrollment?.supported ? this.notifications.relayEnrollment : null } },
  methods: {
    reportSave,
    otherPhones() { return (this.notifications?.appSubscriptions || []).filter(phone => !this.pushState.id || phone.id !== this.pushState.id) },
    refreshPush() { try { this.pushState = JSON.parse(window.StarPilotApp?.sentryPush?.() || '{}') } catch { this.pushState = {} } },
    togglePhone() { window.StarPilotApp?.setSentryPush?.(!this.pushState.enabled); this.refreshPush(); setTimeout(() => this.client.run(), 1000) },
    async testPhone(id) {
      if (!await this.client.run({ action: 'test', target: 'app', id })) return
      clearInterval(this.testPoll)
      let attempts = 0
      this.testPoll = setInterval(() => { if (++attempts >= 15) { clearInterval(this.testPoll); this.testPoll = null } if (!this.client.pending) this.client.run() }, 1000)
    },
    removePhone(id) { return this.client.run({ action: 'appUnsubscribe', id }) },
    simulate() { return this.client.run({ action: 'simulate' }) },
    canSimulate() { return !!window.StarPilotApp || location.protocol === 'http:' },
    async enableHostedAlerts() {
      if (await this.client.run({ action: 'relayEnroll', confirmed: true })) {
        window.StarPilotApp?.setSentryPush?.(true)
        this.refreshPush()
        setTimeout(() => this.client.run(), 1000)
      }
    },
    async enrollRelay() {
      if (await this.client.run({ action: 'relayEnroll', url: this.relayUrl, invitation: this.enrollmentCode.trim(), confirmed: this.relayConfirmed })) {
        this.enrollmentCode = ''; this.relayUrl = ''; this.relayConfirmed = false
      }
    },
    async relayAction(action) {
      if (await this.client.run({ action, confirmed: this.relayConfirmed })) this.relayConfirmed = false
    },
    relayChip() {
      return ({ unenrolled: 'Not set up', enrolling: 'Setting up', enrolled: 'On', rotating: 'Updating', confirming: 'Updating', revoking: 'Paused', revoked: 'Off', actionRequired: 'Needs attention' })[this.relay?.state] || 'Unknown'
    },
    relayStatus() {
      const state = this.notifications?.relayEnrollment?.state
      return ({ unenrolled: 'Enable alerts to set up this comma and phone automatically.', enrolling: 'Enrolling — waiting for the relay. Retries continue when internet returns.', enrolled: 'Relay enrolled. Enable alerts on each phone, then send a test.', rotating: 'Updating the relay credential.', confirming: 'Confirming the new relay credential.', revoking: 'Sending paused. Revocation is pending until the relay acknowledges it.', revoked: 'Alerts are disabled. Enable alerts to enroll again.', actionRequired: 'Relay authorization needs attention. Ask the operator to check or revoke the installation before enrolling again.' })[state] || 'This comma does not support relay enrollment.'
    },
    async saveRelay() {
      if (await this.client.run({ action: 'configure', channel: 'appPush', enabled: true, url: this.relayUrl, token: '' })) this.relayUrl = ''
    },
    start() {
      clearInterval(this.poll)
      this.refreshPush()
      if (this.mode !== "local" || document.hidden) return
      this.client.run()
      this.poll = setInterval(() => { this.refreshPush(); if (!this.notificationBusy && !this.client.pending) this.client.run() }, 5000)
    },
    channelExpanded(name) { return this.notifications.channels[name].enabled || !!this.channelSetup[name] },
    toggleChannel(name) {
      if (!this.notifications.channels[name].configured) {
        this.channelSetup[name] = !this.channelSetup[name]
        return
      }
      return this.reportSave(() => this.save(name, !this.notifications.channels[name].enabled))
    },
    async save(name, enabled) {
      const draft = this.drafts[name]
      const saved = await this.client.run({ action: "configure", channel: name, enabled, url: draft.url, token: draft.token })
      if (saved) {
        draft.url = ""; draft.token = ""
        this.channelSetup[name] = false
      }
      return saved
    },
    test(name) { return this.client.run({ action: "test", channel: name }) },
    async forget(name) { if (await this.client.run({ action: "forget", channel: name })) { this.drafts[name].url = ""; this.drafts[name].token = ""; this.channelSetup[name] = false } },
    remove(id) { return this.client.run({ action: "unsubscribe", id }) },
    async subscribe() {
      if (!isSecureContext || !("serviceWorker" in navigator) || !("PushManager" in window)) {
        this.notificationError = "Browser push needs HTTPS and a browser with Push API support."
        return
      }
      try {
        const permission = await Notification.requestPermission()
        if (permission !== "granted") throw new Error("Allow browser notifications to subscribe.")
        if (!await this.client.run({ action: "pushKey" })) return
        const key = this.notifications.applicationServerKey
        const bytes = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/") + "="), c => c.charCodeAt(0))
        const registration = await navigator.serviceWorker.register("./sentry-push-worker.js")
        await navigator.serviceWorker.ready
        const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes })
        const value = subscription.toJSON()
        if (!await this.client.run({ action: "subscribe", subscription: { endpoint: value.endpoint, keys: value.keys } })) return
        await this.save("webPush", true)
      } catch (error) { this.notificationError = error.message || "Browser push subscription failed." }
    },
  },
  template: `
    <section class="gx-card" aria-label="Sentry notifications" :inert="notificationBusy" :aria-busy="notificationBusy || undefined">
      <div class="gx-section__header"><i class="bi bi-bell" aria-hidden="true"></i><span class="gx-section__title">Notifications</span></div><div class="gx-panel gx-stack">
      <p class="gx-note">Receive new locally recorded motion events. Notification channels work independently.</p>
      <p v-if="mode !== 'local'">Notification configuration is available on your connected device.</p>
      <template v-else>
        <GxNotice tone="danger" v-if="notificationError">{{ notificationError }}</GxNotice>
        <p class="gx-note gx-sentry-busy" :class="{'gx-note--warning': notificationTesting}" role="status">{{ notificationBusy ? (notificationTesting ? 'Test notifications may take up to 20 seconds.' : 'Updating notifications…') : '' }}</p>
        <GxNotice tone="danger" v-if="notifications?.queueFull">The notification queue is full. Some events could not be queued.</GxNotice>
        <template v-if="notifications">
          <p class="gx-note">{{ notifications.deliverySemantics }}</p>
          <section class="gx-card" aria-label="Galaxy App Alerts">
            <div class="gx-section__header"><i class="bi bi-phone" aria-hidden="true"></i><span class="gx-section__title">Galaxy App Alerts</span><span v-if="relay" class="gx-chip">{{ relayChip() }}</span></div>
            <div class="gx-panel gx-stack">
              <p v-if="!nativePush" class="gx-note">App alerts can be set up and managed in the Galaxy app.</p>
              <template v-else>
              <p class="gx-note" role="status">{{ relay ? relayStatus() : notifications.appPushAvailable ? 'Push relay configured.' : 'Configure a push relay to deliver phone alerts.' }}</p>
              <div v-if="relay && ['unenrolled', 'revoked', 'actionRequired'].includes(relay.state)" class="gx-actions">
                <button type="button" class="gx-btn" :disabled="!relay.automaticConfigured" @click="enableHostedAlerts">Enable alerts</button>
                <span class="gx-note">{{ relay.automaticConfigured ? 'Sends phone destinations and alert details to the hosted relay.' : 'Hosted alerts are not configured in this release yet.' }}</span>
              </div>
              <p v-if="notifications.appPushAvailable && (nativePush || notifications.appSubscriptions?.length)" class="gx-note">Test alerts may take up to 20 seconds to arrive.</p>
              <div v-if="nativePush" class="gx-row gx-row--borderless gx-row--wrap">
                <div class="gx-row__info gx-row__info--wide"><span class="gx-row__label">This phone</span><span class="gx-row__desc">{{ pushState.status || (pushState.enabled ? 'Alerts on' : 'Alerts off') }}</span></div>
                <div class="gx-actions">
                  <button v-if="notifications.appPushAvailable && pushState.registered && pushState.id" type="button" class="gx-btn gx-btn--tonal" @click="testPhone(pushState.id)">Send test</button>
                  <button type="button" class="gx-btn gx-btn--tonal" :disabled="!notifications.appPushAvailable" @click="togglePhone">{{ pushState.enabled ? 'Turn off' : 'Turn on' }}</button>
                </div>
              </div>
              <div v-for="phone in otherPhones()" :key="phone.id" class="gx-row gx-row--wrap">
                <div class="gx-row__info gx-row__info--wide"><span class="gx-row__label">{{ phone.label || 'Phone' }}</span>
                  <span class="gx-row__desc">{{ phone.lastResult || 'Not tested yet' }}<template v-if="phone.lastAt">, {{ new Date(phone.lastAt * 1000).toLocaleString() }}</template></span></div>
                <div class="gx-actions">
                  <button type="button" class="gx-btn gx-btn--tonal" :disabled="!notifications.appPushAvailable" @click="testPhone(phone.id)">Test</button>
                  <button type="button" class="gx-btn gx-btn--tonal" @click="removePhone(phone.id)">Remove</button>
                </div>
              </div>
              <p v-if="otherPhones().length" class="gx-note">Remove only clears a phone from this comma. To stop it re-adding itself, also turn Sentry alerts off in the phone's app.</p>
              <p v-for="phone in (notifications.removedAppSubscriptions || [])" :key="phone.id" class="gx-note">{{ phone.lastResult }}</p>
              <details>
                <summary class="gx-link">Relay settings</summary>
                <div class="gx-stack gx-sentry-relay">
                  <template v-if="relay">
                    <dl v-if="relay.installationId" class="gx-details"><dt>Installation ID</dt><dd>{{ relay.installationId }}</dd></dl>
                    <template v-if="['unenrolled', 'revoked', 'actionRequired'].includes(relay.state)">
                      <div class="gx-sentry-channel__fields">
                        <label>Custom relay address <input v-model="relayUrl" class="gx-field" type="url" autocomplete="off" placeholder="https://your-relay.workers.dev"></label>
                        <label>One-time enrollment code <input v-model="enrollmentCode" class="gx-field" type="password" autocomplete="off"></label>
                      </div>
                    </template>
                    <label><input type="checkbox" v-model="relayConfirmed"> Tick to enable the buttons below. Enrolling shares your phone destinations and alert details with the relay operator. Forget locally does not revoke a copied credential.</label>
                    <div class="gx-actions">
                      <button v-if="['unenrolled', 'revoked', 'actionRequired'].includes(relay.state)" type="button" class="gx-btn gx-btn--tonal" :disabled="!relayConfirmed || !relayUrl || !enrollmentCode" @click="enrollRelay">Enroll with custom relay</button>
                      <button v-if="relay.state === 'enrolled'" type="button" class="gx-btn gx-btn--tonal" :disabled="!relayConfirmed" @click="relayAction('relayRotate')">Rotate credential</button>
                      <button v-if="!['unenrolled', 'revoked'].includes(relay.state)" type="button" class="gx-btn gx-btn--tonal" :disabled="!relayConfirmed" @click="relayAction('relayRevoke')">Revoke relay access</button>
                      <button v-if="relay.state !== 'unenrolled'" type="button" class="gx-btn gx-btn--tonal" :disabled="!relayConfirmed" @click="relayAction('relayForget')">Forget locally</button>
                    </div>
                  </template>
                  <div v-if="!relay || relay.state === 'unenrolled'" class="gx-sentry-channel__fields">
                    <label>{{ relay ? 'Legacy private relay URL' : 'HTTPS push relay URL' }} <input v-model="relayUrl" class="gx-field" type="password" autocomplete="off" placeholder="Leave blank to keep saved URL"></label>
                  </div>
                  <div class="gx-actions">
                    <button v-if="!relay || relay.state === 'unenrolled'" type="button" class="gx-btn gx-btn--tonal" @click="saveRelay">Save push relay</button>
                    <button v-if="canSimulate()" type="button" class="gx-btn gx-btn--tonal" @click="simulate">Simulate event</button>
                  </div>
                </div>
              </details>
              </template>
            </div>
          </section>
          <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="client.run({action: 'test', target: 'all'})">Test all channels</button></div>
          <section v-for="name in channels" :key="name" class="gx-card gx-sentry-channel">
            <div class="gx-section__header"><i class="bi" :class="{webPush: 'bi-bell', discord: 'bi-discord', webhook: 'bi-link-45deg', ntfy: 'bi-broadcast'}[name]" aria-hidden="true"></i><span class="gx-section__title">{{ labels[name] }}</span>
              <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="labels[name] + ' notifications'" :checked="channelExpanded(name)"
                :disabled="notificationBusy"
                @change="$event.target.checked=channelExpanded(name); toggleChannel(name)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
            <div v-if="channelExpanded(name)" class="gx-panel gx-stack">
            <p class="gx-note" role="status">{{ notifications.channels[name].configured ? 'Configured' : 'Not configured' }}<template v-if="notifications.channels[name].configured && notifications.channels[name].lastState !== 'idle'">, last delivery {{ notifications.channels[name].lastState }}<template v-if="notifications.channels[name].pending">, {{ notifications.channels[name].pending }} pending</template></template></p>
            <GxNotice tone="danger" v-if="notifications.channels[name].lastError">Delivery status: {{ notifications.channels[name].lastError.replaceAll('_', ' ') }}</GxNotice>
            <div v-if="name !== 'webPush'" class="gx-sentry-channel__fields">
              <label>HTTPS URL <input v-model="drafts[name].url" class="gx-field" type="password" autocomplete="off" placeholder="Leave blank to keep saved URL"></label>
              <label>Bearer token (optional) <input v-model="drafts[name].token" class="gx-field" type="password" autocomplete="off" placeholder="Leave blank to keep saved token"></label>
            </div>
            <div class="gx-sentry-channel__actions gx-actions">
            <button v-if="name !== 'webPush'" class="gx-btn gx-btn--tonal" type="button" @click="save(name, true)">Save and enable</button>
            <button v-else class="gx-btn gx-btn--tonal" type="button" @click="subscribe">Subscribe this browser</button>
            <button class="gx-btn gx-btn--tonal" type="button" :disabled="!notifications.channels[name].enabled || !notifications.channels[name].configured" @click="test(name)">Send test</button>
            <button class="gx-btn gx-btn--tonal" type="button" @click="forget(name)">Forget configuration</button>
            </div></div>
          </section>
          <div v-if="notifications.subscriptionCount" class="gx-actions"><span class="gx-note">Subscribed browsers: {{ notifications.subscriptionCount }}</span>
            <button v-for="(subscription, index) in notifications.subscriptions" :key="subscription.id" class="gx-btn gx-btn--tonal" type="button" @click="remove(subscription.id)">Remove browser {{ index + 1 }}</button></div>
        </template>
      </template></div>
    </section>`,
}
