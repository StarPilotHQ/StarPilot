import { GxState } from "./state.js"
import { SettingsPage } from "./settings.js"
import { GxNotice } from "./notice.js"
import { BluetoothNotice, bluetoothConnection } from "./bluetooth-notice.js"
import { SentryNotifications } from "./sentry-notifications.js"
import { SnapshotFeed } from "./snapshot-feed.js"
import { BalancedImage } from "./white-balance.js"
import { deleteRecords, storedSize } from "./record-deletion.js"

// Historical motion records and independently configured notifications.
const ID = /^[0-9a-f]{32}$/

export function validSentryEvents(value) {
  if (value?.schemaVersion !== 1 || value.source !== "local" || typeof value.scanIncomplete !== "boolean" ||
      value.capacity !== 512 || !Array.isArray(value.events) || value.events.length > 512) return false
  if (value.storageBytes !== undefined && (!Number.isSafeInteger(value.storageBytes) || value.storageBytes < 0)) return false
  if (value.storageIncomplete !== undefined && typeof value.storageIncomplete !== "boolean") return false
  const seen = new Set()
  return value.events.every((event) => {
    if (event === null || typeof event !== "object" || Object.keys(event).some((key) => !["eventId", "kind", "systemTimeMs", "images", "simulated", "voltage", "cutoff"].includes(key)) ||
        (event.kind === "battery" ? ![event.voltage, event.cutoff].every((tenths) => Number.isSafeInteger(tenths) && tenths > 0 && tenths < 1000) :
          event.voltage !== undefined || event.cutoff !== undefined) ||
        (event.simulated !== undefined && event.simulated !== true) ||
        (event.images !== undefined && (!Array.isArray(event.images) || event.images.length > 2 ||
          new Set(event.images).size !== event.images.length || event.images.some((camera) => !["wide", "cabin"].includes(camera)))) ||
        typeof event.eventId !== "string" || !ID.test(event.eventId) || seen.has(event.eventId) ||
        !["warning", "alarm", "selfie", "battery"].includes(event.kind) || !Number.isSafeInteger(event.systemTimeMs) ||
        event.systemTimeMs <= 0 || event.systemTimeMs > 8_640_000_000_000_000) return false
    seen.add(event.eventId)
    return true
  })
}

export const systemTime = (milliseconds) => Number.isSafeInteger(milliseconds) && milliseconds > 0 && milliseconds <= 8_640_000_000_000_000
  ? new Date(milliseconds).toLocaleString() : "Unavailable"

export class SentryEventsFeed extends SnapshotFeed {
  static endpoint = "./api/sentry/events"
  static valid = validSentryEvents
  static subject = "local motion events"
  static unavailable = "Local motion events are unavailable."
  static invalid = "Local motion records are unavailable."
  stop() { this.deleting = false; super.stop() }
  emit() { this.publish({ status: this.status, data: this.data, error: this.error, deleting: this.deleting === true }) }
  remove(eventId, confirm) {
    if (eventId != null && !ID.test(eventId)) return Promise.resolve(false)
    return deleteRecords(this, "./api/sentry/events/delete", eventId == null ? { all: true, confirmed: true } : { eventId }, {
      valid: validSentryEvents, confirm, subject: "Sentry events",
      accept: data => { this.data = data; this.status = "ready"; this.error = ""; this.emit() },
    })
  }
}

export const SentryEventsPage = {
  name: "SentryEventsPage",
  components: { GxState, GxNotice, BluetoothNotice, SentryNotifications, SettingsPage, BalancedImage },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    go: { type: Function, required: true } },
  data: () => ({ bluetooth: bluetoothConnection(), status: "idle", data: null, error: "", deleting: false }),
  created() { this.feed = new SentryEventsFeed({ publish: (update) => Object.assign(this.$data, update), unauthorized: this.unauthorized }) },
  mounted() {
    this.visibility = () => { if (document.hidden) this.feed.stop(); else if (this.mode === "local") this.feed.start() }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local" && !document.hidden) this.feed.start()
  },
  beforeUnmount() { document.removeEventListener("visibilitychange", this.visibility); this.feed.stop() },
  methods: { systemTime, storedSize },
  computed: { kinds: () => ({ alarm: { label: 'Alarm', icon: 'bi-bell' }, warning: { label: 'Motion', icon: 'bi-exclamation-triangle' }, selfie: { label: 'Selfie', icon: 'bi-camera' }, battery: { label: 'Low battery', icon: 'bi-battery-half' } }) },
  template: `
    <div class="gx-view gx-home" aria-label="Sentry motion events">
      <header class="gx-settings__header gx-page-header"><div><h2>Sentry</h2>
        <p>Review motion events, configure motion monitoring, and manage notifications. Timestamps use the device clock and may be inaccurate.</p></div></header>

      <SettingsPage :mode="mode" :unauthorized="unauthorized" initial-page="sentry" title="" />
      <SentryNotifications :mode="mode" :unauthorized="unauthorized" />
      <GxState v-if="mode !== 'local'">Local motion records are unavailable in preview.</GxState>
      <template v-else>

        <GxState v-if="status === 'loading'" loading>Reading local motion events…</GxState>
        <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
        <template v-if="status === 'ready' && data">
          <div class="gx-actions gx-sentry-events__bar"><p class="gx-note">{{ data.events.length }} {{ data.events.length === 1 ? 'event' : 'events' }}, {{ data.storageIncomplete ? 'at least ' : '' }}{{ storedSize(data.storageBytes) }} stored</p>
            <button v-if="data.events.length" type="button" class="gx-btn gx-btn--tonal" :disabled="deleting" @click="feed.remove(null)">Delete All</button></div>
          <GxNotice v-if="data.scanIncomplete" tone="warn">This scan was incomplete. More local motion events may exist.</GxNotice>
          <GxState v-if="!data.events.length">No motion events yet. Events appear here when Sentry detects motion while parked.</GxState>
          <BluetoothNotice v-if="bluetooth && data.events.some(event => event.images?.length)" feature="Motion event images" />
          <div class="gx-sentry-events">
            <article v-for="event in data.events" :key="event.eventId" class="gx-card gx-sentry-event">
              <div class="gx-section__header"><i class="bi" :class="kinds[event.kind].icon" aria-hidden="true"></i><span class="gx-section__title">{{ kinds[event.kind].label }}</span></div>
              <div v-if="!bluetooth && event.images?.length" class="gx-sentry-event__media"><BalancedImage v-for="camera in event.images" :key="camera" :balance="camera === 'cabin'" :src="'./api/sentry/image/' + event.eventId + '/' + camera" :alt="(camera === 'cabin' ? 'Cabin' : 'Road') + ' camera at motion event'" class="gx-media" /></div>
              <div class="gx-panel gx-row gx-row--borderless"><div class="gx-row__info"><span class="gx-row__label">{{ systemTime(event.systemTimeMs) }}</span><small v-if="event.kind === 'battery'" class="gx-note">Battery {{ (event.voltage / 10).toFixed(1) }} V, turns off at {{ (event.cutoff / 10).toFixed(1) }} V</small><small class="gx-note">Event {{ event.eventId }}</small></div>
                <button type="button" class="gx-btn gx-btn--tonal" :aria-label="'Delete ' + kinds[event.kind].label.toLowerCase() + ' event'" :disabled="deleting" @click="feed.remove(event.eventId)">Delete</button></div>
            </article>
          </div>
        </template>
      </template>
    </div>`,
}
