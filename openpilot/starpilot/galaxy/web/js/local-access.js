import { PollTimer, connectionError } from "./polling.js"
import { requestJson } from "./startup.js"
import { reactive } from "../vendor/vue/vue.esm-browser.js"
import { GxNotice } from "./notice.js"

export function validLocalAccess(value) {
  if (!value || typeof value.available !== "boolean" || typeof value.reason !== "string" || value.reason.length > 1024 ||
      !Array.isArray(value.addresses) || value.addresses.length > 32 || value.available !== (value.addresses.length > 0)) return false
  return value.addresses.every(address => {
    if (!address || !["interface", "label", "url"].every(key => typeof address[key] === "string" && address[key].length > 0 && address[key].length <= 512)) return false
    try {
      const url = new URL(address.url)
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password && !!url.hostname
    } catch { return false }
  })
}

export class LocalAccessFeed {
  constructor({ publish, onUnauthorized = () => {}, fetcher = (...args) => fetch(...args),
    later = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id) }) {
    Object.assign(this, { publish, onUnauthorized, fetcher, later, cancel })
    this.active = false; this.request = null; this.generation = 0; this.data = null
    this.poller = new PollTimer({ read: () => this.refresh(), later, cancel })
  }
  start(mode) {
    this.stop(); this.active = true; this.mode = mode
    if (mode !== "local") { this.publish({ loading: false, data: null, error: "Local comma addresses are unavailable in preview." }); return }
    this.poller.start()
    return this.refresh()
  }
  stop() { this.active = false; this.generation++; this.poller.stop(); this.request?.abort(); this.request = null }
  async refresh() {
    if (!this.active || this.mode !== "local" || this.request) return
    const request = new AbortController(), generation = ++this.generation
    this.request = request
    if (!this.data) this.publish({ loading: true })
    try {
      const data = await requestJson("./api/local-access", { fetcher: this.fetcher, timeout: 4000, later: this.later, cancel: this.cancel, request: { signal: request.signal } })
      if (!this.active || generation !== this.generation) return
      if (!validLocalAccess(data)) throw new Error("Local comma addresses are unavailable.")
      this.data = data
      this.publish({ data, error: "" })
    } catch (error) {
      if (this.active && generation === this.generation) {
        if (error.status === 401 || ["setup_required", "access_unavailable"].includes(error.code)) { this.stop(); this.onUnauthorized(); return }
        this.publish({ error: connectionError(error) })
      }
    } finally { if (this.active && generation === this.generation) { this.request = null; this.publish({ loading: false }) } }
  }
}

export const ReportingGuide = {
  template: `<p class="gx-note">Reporting a new companion app issue? Read the instructions for
    <a href="https://wiki.firestar.link/usage/companion-apps/ios/#submit-logs-through-discord" target="_blank" rel="noopener noreferrer">iPhone</a> or
    <a href="https://wiki.firestar.link/usage/companion-apps/android/#report-a-problem-through-discord" target="_blank" rel="noopener noreferrer">Android</a>.</p>`,
}

export const ConnectionLogs = {
  components: { GxNotice, ReportingGuide },
  props: { ticket: String, panel: Boolean, controller: Boolean, showController: Boolean, onUnauthorized: { type: Function, default: () => {} } },
  emits: ["help"],
  data: () => ({ supportCode: "", supportTicket: null, supportBusy: false, supportSent: false, capture: null, captureError: "", captureRequest: null }),
  beforeUnmount() { this.resetCapture() },
  methods: {
    resetCapture() {
      this.captureRequest?.abort()
      this.captureRequest = null
      this.capture = null
      this.captureError = ""
    },
    async threadAction(action) {
      if (this.supportBusy) return
      this.supportBusy = true
      this.captureError = ""
      try {
        const result = await requestJson("./api/companion/support-ticket", { timeout: 90000, request: {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({action, code: this.supportCode.replace(/[\s-]/g, "").toLowerCase()})
        }})
        if (action === "check") this.supportTicket = result
        else this.supportSent = result.sent === true
      } catch (error) {
        if (error.status === 401) this.onUnauthorized()
        else this.captureError = error.message
      } finally { this.supportBusy = false }
    },
    async captureConnectionLogs() {
      if (this.captureRequest) return
      const request = new AbortController()
      this.captureRequest = request
      this.capture = null
      this.captureError = ""
      try {
        const endpoint = this.controller ? "./api/controllers/log/capture" : "./api/local-access/capture"
        const data = await requestJson(endpoint, { request: { signal: request.signal } })
        if (this.captureRequest !== request) return
        if (data?.schemaVersion !== 1 || typeof data.zipBase64 !== "string" || data.zipBase64.length > 131072)
          throw new Error("Invalid diagnostic capture")
        const bytes = Uint8Array.from(atob(data.zipBase64), character => character.charCodeAt(0))
        if (bytes.length < 22 || bytes[0] !== 80 || bytes[1] !== 75 || bytes[2] !== 3 || bytes[3] !== 4)
          throw new Error("Invalid diagnostic capture")
        this.capture = new Blob([bytes], { type: "application/zip" })
        if (this.ticket && /^[a-f0-9]{64}$/.test(this.ticket)) {
          const form = document.createElement("form")
          form.method = "post"
          form.action = "https://starpilot-log-relay.nathan-g-hubbard.workers.dev/report/galaxy/fresh/" + this.ticket
          const input = document.createElement("input")
          input.type = "hidden"
          input.name = "archive"
          input.value = data.zipBase64
          form.appendChild(input)
          document.body.appendChild(form)
          form.submit()
          form.remove()
        }
      } catch (error) {
        if (this.captureRequest !== request) return
        if (error.status === 401 || ["setup_required", "access_unavailable"].includes(error.code)) this.onUnauthorized()
        else this.captureError = connectionError(error)
      } finally {
        if (this.captureRequest === request) this.captureRequest = null
      }
    },
    downloadConnectionLogs() {
      if (!this.capture) return
      const url = URL.createObjectURL(this.capture)
      const link = document.createElement("a")
      link.href = url
      link.download = this.controller ? "starpilot-controller-logs.zip" : "starpilot-connection-logs.zip"
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  },
  template: `<div class="gx-stack gx-connection-logs" :class="{ 'gx-card gx-panel': panel }">
    <div v-if="panel" class="gx-section__header"><i class="bi bi-file-earmark-text" aria-hidden="true"></i><span class="gx-section__title">Connection logs</span>
      <button type="button" class="gx-btn gx-btn--tonal" @click="$emit('help')">Get help</button>
    </div>
    <div v-if="panel" class="gx-actions"><a class="gx-btn gx-btn--tonal" href="https://wiki.firestar.link/usage/companion-apps/#pair-your-phone" target="_blank" rel="noopener noreferrer">App connection help</a></div>
    <details v-if="!ticket"><summary class="gx-btn">Send to existing Discord thread</summary>
      <p v-if="supportSent" role="status">Support received the logs. Your Discord thread has the receipt.</p>
      <form v-else class="gx-log-form" @submit.prevent="threadAction(supportTicket ? 'send' : 'check')">
        <label class="gx-field-group">Discord request code<input class="gx-field" v-model="supportCode" :disabled="supportBusy" @input="supportTicket = null; captureError = ''" maxlength="24" autocomplete="off" spellcheck="false"></label>
        <button v-if="!supportTicket" class="gx-btn" type="submit" :disabled="supportBusy || !supportCode.trim()">{{ supportBusy ? 'Finding report…' : 'Find report' }}</button>
        <template v-else><p>{{ supportTicket.title }}</p><p>Only support staff receive the {{ supportTicket.kind }} logs.</p>
          <button class="gx-btn" type="submit" :disabled="supportBusy">{{ supportBusy ? 'Sending…' : 'Capture and send' }}</button>
        </template>
      </form>
    </details>
    <details v-if="!ticket"><summary><small>Manual ZIP download</small></summary>
      <p v-if="showController" class="gx-note">For controller ZIPs, open Get help and choose Bluetooth Controller Support.</p>
      <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!captureRequest" @click="captureConnectionLogs">{{ captureRequest ? 'Capturing…' : 'Capture logs' }}</button>
      <button type="button" class="gx-btn gx-btn--tonal" :disabled="!capture" @click="downloadConnectionLogs">Download ZIP</button>
    </details>
    <div v-else class="gx-actions">
      <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!captureRequest" @click="captureConnectionLogs">{{ captureRequest ? 'Capturing…' : 'Capture and send' }}</button>

    </div>
    <GxNotice v-if="captureError" tone="danger">{{ captureError }}</GxNotice>
    <p v-if="capture && !ticket" class="gx-note" role="status">Capture ready. Download it to share with support.</p>
    <ReportingGuide v-if="!panel && !ticket" />
  </div>`,
}

export const LocalAccess = {
  components: { GxNotice, ConnectionLogs },
  props: { mode: { type: String, required: true }, onUnauthorized: { type: Function, default: () => {} } },
  setup(props) {
    const state = reactive({ loading: false, data: null, error: "" })
    const feed = new LocalAccessFeed({ publish: update => Object.assign(state, update), onUnauthorized: props.onUnauthorized })
    return { state, feed }
  },
  mounted() { this.feed.start(this.mode) },
  watch: { mode(value) { this.feed.start(value) } },
  beforeUnmount() { this.feed.stop() },
  template: `<section class="gx-card gx-local-access gx-panel" aria-label="Local comma access">
    <div class="gx-section__header"><i class="bi bi-router" aria-hidden="true"></i><span class="gx-section__title">Local access</span></div>
    <GxNotice v-if="state.error" tone="danger">{{ state.error }}</GxNotice>
    <template v-else-if="state.data?.available">
      <p>Connect your phone to the Galaxy hotspot or the same network as your comma, then open an address below. No Internet or Galaxy password needed.</p>
      <ul class="gx-stack"><li v-for="address in state.data.addresses" :key="address.interface + address.url"><a :href="address.url" target="_blank" rel="noopener" class="gx-btn gx-btn--tonal gx-wrap">{{ address.label }} · {{ address.url }}</a></li></ul>
      <p class="gx-note">Use the full http:// address and bookmark it. Allow local-network access if prompted. Your Firestar Link uses Internet instead.</p>
    </template>
    <p v-else-if="state.data" class="gx-note" role="status">{{ state.data.reason || 'The comma has no local network address available.' }}</p>
    <ConnectionLogs v-if="mode === 'local'" :on-unauthorized="onUnauthorized" />
  </section>`,
}
