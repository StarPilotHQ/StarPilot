// Read-only StarPort session logs. State is cleared on navigation and sign-out.
export const LOGS_URL = "./api/starport/logs"
export const BUNDLE_URL = `${LOGS_URL}/bundle`
export const fileUrl = (name) => `${LOGS_URL}/file/${encodeURIComponent(name)}`

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
export const MESSAGE_LIMIT = 60
const POLL_MS = 1500

// "Send StarPort logs": the device builds the same zip as Download all logs and posts it to the developer's Discord.
// Only small JSON crosses the connection, so this also works over Bluetooth.
export class StarPortLogSend {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args), schedule = (fn, ms) => setTimeout(fn, ms),
    cancel = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, schedule, cancel })
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
    const response = await this.fetcher(SEND_URL, { cache: "no-store", credentials: "same-origin", ...options })
    if (!this.active || generation !== this.generation) return null
    if (response.status === 401) {
      this.stop()
      this.unauthorized()
      return null
    }
    const body = await response.json().catch(() => null)
    if (!this.active || generation !== this.generation) return null
    if (!response.ok) throw new Error(body?.error || "Sending StarPort logs is unavailable")
    return body
  }

  apply(body) {
    if (!this.active || !body) return
    this.publish({ state: body.state, message: body.message || "", available: body.available !== false, error: "" })
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
        this.publish({ error: error?.message || "Sending StarPort logs is unavailable" })
        this.queuePoll()
      }
    }
  }

  async send(message, discord = "") {
    if (!this.active) return false
    const text = String(message ?? "").trim()
    const handle = String(discord ?? "").trim()
    if (text.length > MESSAGE_LIMIT) {
      this.publish({ error: `Keep the message to ${MESSAGE_LIMIT} characters.` })
      return false
    }
    const generation = ++this.generation
    this.queuePoll(false)
    this.publish({ state: "sending", message: "Starting", error: "" })
    try {
      const body = await this.call({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(handle ? { message: text, discord: handle } : { message: text }) }, generation)
      if (body === null) return false
      this.apply(body)
      return true
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.publish({ state: "idle", error: error?.message || "Sending StarPort logs is unavailable" })
        this.queuePoll()
      }
      return false
    }
  }
}
