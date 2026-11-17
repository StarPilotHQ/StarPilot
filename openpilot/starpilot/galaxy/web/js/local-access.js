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

export const LocalAccess = {
  components: { GxNotice },
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
  </section>`,
}
