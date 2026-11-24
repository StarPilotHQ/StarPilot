// Read-only StarPort session logs. State is cleared on navigation and sign-out.
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"

export const LOGS_URL = "./api/starport/logs"

// compat_report's outcome is a short phrase: "projected", "failed: <error>", "stopped at rfcomm", "no session".
export const outcomeLabel = (outcome) => (outcome ? outcome[0].toUpperCase() + outcome.slice(1) : "Unknown")

export class StarPortLogsFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args) }) {
    Object.assign(this, { publish, unauthorized, fetcher })
    this.active = false
    this.generation = 0
    this.request = null
  }

  clear() {
    this.publish({ sessions: [], others: [], status: "idle", error: "" })
  }

  stop() {
    this.active = false
    this.generation++
    this.request?.abort()
    this.request = null
    this.clear()
  }

  start() {
    this.stop()
    this.active = true
    return this.load()
  }

  async load() {
    if (!this.active) return
    const generation = ++this.generation
    this.request?.abort()
    const request = new AbortController()
    this.request = request
    const current = () => this.active && generation === this.generation && !request.signal.aborted
    this.publish({ status: "loading", error: "" })
    try {
      const response = await this.fetcher(LOGS_URL, { signal: request.signal, cache: "no-store" })
      if (!current()) return
      if (response.status === 401) {
        this.stop()
        this.unauthorized()
        return
      }
      if (response.status === 503) {
        const body = await response.json().catch(() => null)
        if (!current()) return
        if (["access_unavailable", "setup_required"].includes(body?.code)) {
          this.stop()
          this.unauthorized()
          return
        }
        throw new Error("StarPort logs are unavailable")
      }
      if (!response.ok) throw new Error("StarPort logs are unavailable")
      const body = await response.json()
      if (!current()) return
      this.publish({ sessions: body.sessions ?? [], others: body.others ?? [], status: "ready", error: "" })
    } catch (error) {
      if (!current() || error?.name === "AbortError") return
      this.publish({ sessions: [], others: [], status: "unavailable", error: error?.message || "StarPort logs are unavailable" })
    }
  }
}

export const SEND_URL = `${LOGS_URL}/send`
export const BLUETOOTH_SEND_URL = "./api/controllers/log/send"
export const MESSAGE_LIMIT = 60
const POLL_MS = 1500

// The device builds the log and posts it to the developer's Discord.
// Only small JSON crosses the connection, so this also works over Bluetooth.
export class StarPortLogSend {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args), schedule = (fn, ms) => setTimeout(fn, ms),
    cancel = (id) => clearTimeout(id), url = SEND_URL, unavailable = "Sending StarPort logs is unavailable" }) {
    Object.assign(this, { publish, unauthorized, fetcher, schedule, cancel, url, unavailable })
    this.active = false
    this.timer = null
    this.generation = 0
  }

  stop() {
    this.generation++
    this.active = false
    if (this.timer !== null) this.cancel(this.timer)
    this.timer = null
  }

  start() {
    this.stop()
    this.active = true
    return this.poll()
  }

  async call(options, generation) {
    const response = await this.fetcher(this.url, { cache: "no-store", credentials: "same-origin", ...options })
    if (!this.active || generation !== this.generation) return null
    if (response.status === 401) {
      this.stop()
      this.unauthorized()
      return null
    }
    const body = await response.json().catch(() => null)
    if (!this.active || generation !== this.generation) return null
    if (!response.ok) throw new Error(body?.error || this.unavailable)
    return body
  }

  apply(body) {
    if (!this.active || !body) return
    this.publish({ state: body.state, message: body.message || "", available: body.available !== false, fork: body.fork === true, error: "" })
    this.queuePoll(body.state === "sending")
  }

  queuePoll(pending = true) {
    if (this.timer !== null) this.cancel(this.timer)
    this.timer = this.active && pending ? this.schedule(() => { this.timer = null; this.poll() }, POLL_MS) : null
  }

  async poll() {
    if (!this.active) return
    const generation = this.generation
    try {
      this.apply(await this.call({}, generation))
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.publish({ error: error?.message || this.unavailable })
        this.queuePoll()
      }
    }
  }

  async send(message, discord) {
    if (!this.active) return false
    const text = String(message ?? "").trim()
    const handle = String(discord ?? "").trim()
    if (!text) {
      this.publish({ error: "Add a short note about what happened." })
      return false
    }
    if (text.length > MESSAGE_LIMIT) {
      this.publish({ error: `Keep the message to ${MESSAGE_LIMIT} characters.` })
      return false
    }
    if (!handle.replace(/^@/, "")) {
      this.publish({ error: "Add your Discord username so we can follow up." })
      return false
    }
    const generation = ++this.generation
    this.queuePoll(false)
    this.publish({ state: "sending", message: "Starting", error: "" })
    try {
      const body = await this.call({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, discord: handle }) }, generation)
      if (body === null) return false
      this.apply(body)
      return true
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.publish({ state: "idle", error: error?.message || this.unavailable })
        this.queuePoll()
      }
      return false
    }
  }
}

export const LogSendCard = {
  name: "LogSendCard",
  components: { GxState, GxNotice },
  props: { feed: { type: Object, required: true }, state: { type: Object, required: true }, title: { type: String, required: true } },
  data: () => ({ note: "", discord: "", limit: MESSAGE_LIMIT, missing: [] }),
  watch: {
    note(value) { if (value.trim()) this.missing = this.missing.filter((field) => field !== "note") },
    discord(value) { if (value.trim().replace(/^@/, "")) this.missing = this.missing.filter((field) => field !== "discord") },
  },
  methods: {
    async submit() {
      this.missing = [!this.note.trim() && "note", !this.discord.trim().replace(/^@/, "") && "discord"].filter(Boolean)
      if (this.missing.length) return this.$refs[this.missing[0]]?.focus()
      if (await this.feed.send(this.note, this.discord)) this.note = ""
    },
  },
  template: `
    <section class="gx-card gx-panel gx-starport-send" :aria-label="title">
      <div class="gx-section__header"><i class="bi bi-send" aria-hidden="true"></i><span class="gx-section__title">{{ title }}</span></div>
      <p class="gx-note"><slot /></p>
      <div class="gx-starport-send__row">
        <div class="gx-starport-send__field"><input ref="note" class="gx-field" :class="{ 'gx-field--required': missing.includes('note') }" :aria-invalid="missing.includes('note')" type="text" v-model="note" :maxlength="limit" placeholder="What happened?" aria-label="Note for the developer" :disabled="state.state === 'sending'" @keydown.enter="submit"><span class="gx-starport-send__count" aria-live="polite">{{ note.length }}/{{ limit }}</span></div>
        <input ref="discord" class="gx-field" :class="{ 'gx-field--required': missing.includes('discord') }" :aria-invalid="missing.includes('discord')" type="text" v-model="discord" maxlength="40" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Discord username" aria-label="Discord username for a follow-up" :disabled="state.state === 'sending'" @keydown.enter="submit">
        <button type="button" class="gx-btn" :disabled="state.state === 'sending'" @click="submit"><i class="bi bi-send" aria-hidden="true"></i><span>{{ title }}</span></button>
      </div>
      <GxState v-if="state.state === 'sending'" loading>{{ state.message || 'Sending logs' }}…</GxState>
      <GxNotice v-else-if="state.state === 'sent'" tone="success">{{ state.message }}</GxNotice>
      <GxNotice v-else-if="state.state === 'failed'" tone="danger">{{ state.message }}</GxNotice>
      <GxNotice v-if="state.error" tone="danger">{{ state.error }}</GxNotice>
    </section>`,
}
