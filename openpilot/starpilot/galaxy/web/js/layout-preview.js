import { widgetArtMetadata } from "./widget-art.js"
import { widgetSize } from "./onroad-layout-document.js"
// hiddenOn: screens where the scene looks exactly like another one. The Small screen has no text for these
// speed-limit states, so they match Engaged there.
export const PREVIEW_SCENES = [
  { id: "general", label: "General edit" },
  { id: "engaged", label: "Engaged" },
  { id: "aol", label: "Lateral only" },
  { id: "long_only", label: "Longitudinal only" },
  { id: "experimental", label: "Manual experimental" },
  { id: "braking", label: "Braking" },
  { id: "stopped", label: "Stopped timer" },
  { id: "cem_stop_light", label: "CEM · Traffic light" },
  { id: "cem_lead", label: "CEM · Stopped lead" },
  { id: "cem_curve", label: "CEM · Curve" },
  { id: "slc_notice", label: "New Limit notice" },
  { id: "slc_pending", label: "Confirmation choices" },
  { id: "slc_max", label: "Using set speed" },
  { id: "slc_equal", label: "Equal speed limits" },
  { id: "slc_pedal", label: "Using accelerator", hiddenOn: ["compact"] },
  { id: "slc_retained", label: "Set-speed override" },
  { id: "slc_disengaged", label: "Cruise off" },
  { id: "slc_unavailable", label: "Unavailable speed limit" },
  { id: "slc_display", label: "Display-only speed limit", hiddenOn: ["compact"] },
]
const SCENES = PREVIEW_SCENES.map(({ id }) => id)

// Keep the sketch and its eye controls aligned with sample_state and the native onroad view.
export function previewScene(scene) {
  const off = scene === "slc_disengaged"
  const lateral = !off && scene !== "long_only"
  const longitudinal = !off && scene !== "aol"
  const experimental = scene === "experimental" || scene.startsWith("cem_")
  return { lateral, longitudinal, experimental,
    border: off ? "#122839" : scene === "slc_pedal" ? "#919b95" : experimental ? "#da6f25" : !longitudinal ? "#0abab5" : !lateral ? "#ff69b4" : "#167f40",
    mode: off ? "Disengaged" : scene === "slc_pedal" ? "ACC - Override" : !longitudinal ? "Always On Lateral" : experimental ? "ACC - Experimental" : "ACC - Chill" }
}

export function sceneHidesWidget(scene, profile, id, layout = {}, widgets = {}) {
  if (scene === "general") return false
  const { lateral, longitudinal } = previewScene(scene)
  if (id === "stopped_timer") return scene !== "stopped"
  if (id === "cem_gauge") return !scene.startsWith("cem_")
  if (id === "torque_bar" || profile === "compact" && id === "steering_wheel") return !lateral
  if (profile === "compact" && id === "max_speed") return !longitudinal
  if (id === "speed_limit_actions") return true
  const overlaps = (a, b) => a && b && a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y
  const box = key => {
    if (!layout[key]?.enabled || !widgets[key]) return null
    const [width, height] = widgetSize(widgets[key], layout[key])
    return { ...widgets[key], ...layout[key], width, height }
  }
  if (profile === "compact" && id === "driver_monitor" && longitudinal) return !!overlaps(box(id), box("max_speed"))
  if (profile === "compact" && id === "speed_limit") {
    return scene === "slc_unavailable"
  }
  return false
}

// The device renders one preview at a time and answers a second request with 429.
// Feeds sharing a queue take turns instead, widget art before the backdrop.
// Allow for Galaxy tunnel latency.
const PREVIEW_TIMEOUT_MS = 20000

export class PreviewQueue {
  constructor() { this.running = false; this.waiting = [] }

  acquire(priority, signal) {
    return new Promise((resolve, reject) => {
      const cancel = () => { this.waiting = this.waiting.filter(item => item !== entry); reject(new Error("Preview request cancelled")) }
      const entry = { priority, start: () => { signal.removeEventListener("abort", cancel); resolve() } }
      if (signal.aborted) { reject(new Error("Preview request cancelled")); return }
      signal.addEventListener("abort", cancel, { once: true })
      this.waiting.push(entry)
      this.waiting.sort((a, b) => a.priority - b.priority)
      this.next()
    })
  }

  next() {
    if (this.running || !this.waiting.length) return
    this.running = true
    this.waiting.shift().start()
  }

  release() { this.running = false; this.next() }
}

export class LayoutPreviewFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id),
                urls = URL, art = false, backdrop = false, queue = null }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer, urls, art, backdrop, queue })
    this.active = false
    this.version = 0
    this.timer = null
    this.request = null
    this.currentUrl = null
    this.pending = null
    // Keep one native sheet per profile and scene, so returning to either is instant.
    this.artCache = new Map()
    // The sheet last shown for a profile stands in for a scene that has not rendered yet.
    this.latestArt = new Map()
    this.failureKey = null
    this.failures = 0
  }

  pause() {
    this.active = false
    this.version++
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.timer = null
    this.pending = null
    this.inputKey = null
    this.request?.abort()
    this.request = null
    this.last = null
  }

  stop() {
    this.pause()
    this.clearImage()
    this.failureKey = null
    this.failures = 0
  }

  clearImage() {
    const urls = new Set([...this.artCache.values()].map(item => item.url))
    if (this.currentUrl) urls.add(this.currentUrl)
    for (const url of urls) this.urls.revokeObjectURL(url)
    this.artCache.clear()
    this.latestArt.clear()
    this.currentUrl = null
    this.publish({ url: null, ...(this.art ? {sprites: null} : {}) })
  }

  start() { if (this.art) this.pause(); else this.stop(); this.active = true }

  // `key` stands in for the document when only part of it changes the image (the backdrop ignores positions).
  update(document, profile, scene, dragging = false, key = document) {
    if (!this.active || !document || !["large", "compact", "projection"].includes(profile) || !SCENES.includes(scene)) return
    const documentKey = JSON.stringify(key)
    const inputKey = JSON.stringify([documentKey, profile, scene, dragging])
    if (inputKey === this.inputKey) return
    this.inputKey = inputKey
    // Picking a scene or screen is one discrete change, so only document edits are debounced.
    const debounce = documentKey === this.documentKey ? 0 : 200
    this.documentKey = documentKey
    this.version++
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.timer = null
    this.pending = null
    this.request?.abort()
    if (dragging) { this.publish({ status: "editing", error: "" }); return }
    const renderKey = JSON.stringify([documentKey, profile, scene])
    if (renderKey !== this.failureKey) { this.failureKey = renderKey; this.failures = 0 }
    if (this.art) {
      const cacheKey = profile + "\n" + scene, cached = this.artCache.get(cacheKey)
      if (cached) this.latestArt.set(profile, cacheKey)
      const shown = cached || this.artCache.get(this.latestArt.get(profile))
      this.currentUrl = shown?.url || null
      this.publish({ url: this.currentUrl, sprites: shown?.sprites || null, scene: shown?.scene || null })
      if (cached?.key === renderKey) {
        this.publish({ status: "ready", error: "" })
        return
      }
    }
    this.pending = { document: JSON.parse(JSON.stringify(document)), key, profile, scene, version: this.version, renderKey }
    // Once a real outage is reported, keep the message stable through retries.
    if (!this.art || this.failures < 2) this.publish({ status: "updating", error: "" })
    this.timer = this.later(() => { this.timer = null; this.dispatch() }, debounce)
  }

  retry() {
    if (!this.active || !this.last) return
    this.inputKey = null
    this.update(this.last.document, this.last.profile, this.last.scene, false, this.last.key)
  }

  async dispatch() {
    if (!this.active || this.request || !this.pending) return
    const item = this.pending
    this.pending = null
    this.last = item
    const request = new AbortController()
    this.request = request
    // Queue time does not consume the response timeout.
    let timeout = null
    let rejectAborted
    const aborted = new Promise((_, reject) => { rejectAborted = () => reject(new Error("Preview request cancelled")) })
    request.signal.addEventListener("abort", rejectAborted, { once: true })
    let failure = "Preview is unavailable. Reconnecting automatically…"
    let denied = false, busy = false
    const work = (async () => {
      const held = !!this.queue && await this.queue.acquire(this.art ? 0 : 1, request.signal).then(() => true)
      try {
        timeout = this.later(() => request.abort(), PREVIEW_TIMEOUT_MS)
        const response = await this.fetcher("./api/ui/layout/preview", { method: "POST", credentials: "same-origin",
          cache: "no-store", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ document: item.document, profile: item.profile, scene: item.scene, ...(this.art ? {art: true} : this.backdrop ? {art: "backdrop"} : {}) }), signal: request.signal })
        if (!this.active || item.version !== this.version || request.signal.aborted) return
        if (response.status === 401) { this.stop(); this.unauthorized(); return }
        if (response.status === 403) { denied = true; failure = "Turn off the vehicle to preview this layout."; throw new Error() }
        if (response.status === 429) { busy = true; failure = "Preview is busy. Retrying automatically…"; throw new Error() }
        if (!response.ok) { const detail = await response.json().catch(() => null); failure = detail?.error || failure; throw new Error() }
        if (!response.headers.get("Content-Type")?.toLowerCase().startsWith("image/png")) throw new Error("Preview is unavailable. Reconnecting automatically…")
        const blob = await response.blob()
        if (!this.active || item.version !== this.version || request.signal.aborted) return
        if (!blob.size || blob.size > 4 * 1024 * 1024) throw new Error("Preview is unavailable. Reconnecting automatically…")
        const sprites = this.art ? await widgetArtMetadata(blob, response.headers.get("X-Widget-Art")) : null
        if (!this.active || item.version !== this.version || request.signal.aborted) return
        const cacheKey = item.profile + "\n" + item.scene
        const previousUrl = this.art ? this.artCache.get(cacheKey)?.url : this.currentUrl
        this.currentUrl = this.urls.createObjectURL(blob)
        if (this.art) {
          this.artCache.set(cacheKey, { key: item.renderKey, url: this.currentUrl, sprites, scene: item.scene })
          this.latestArt.set(item.profile, cacheKey)
        }
        this.failures = 0
        this.publish({ status: "ready", error: "", url: this.currentUrl, scene: item.scene, profile: item.profile, ...(this.art ? {sprites} : {}) })
        if (previousUrl) this.urls.revokeObjectURL(previousUrl)
      } finally {
        // Hold the queue until the device answers, so the other feed is not turned away as busy.
        if (held) this.queue.release()
      }
    })()
    work.catch(() => {})  // A cancelled request settles after the race below has moved on.
    try {
      await Promise.race([aborted, work])
    } catch {
      if (this.active && item.version === this.version) {
        this.inputKey = null
        this.failures = denied ? 2 : this.failures + 1
        if (this.art && denied) this.clearImage()
        // Art and backdrop retry promptly, as does any feed turned away by a busy renderer; real outages back off.
        const recovering = (this.art || this.backdrop || busy) && this.failures < 2
        this.publish({ status: recovering ? "updating" : "unavailable", error: recovering ? "" : failure })
        this.timer = this.later(() => { this.timer = null; this.retry() }, recovering ? 600 : 5000)
      }
    } finally {
      if (timeout !== null) this.cancelTimer(timeout)
      request.signal.removeEventListener("abort", rejectAborted)
      if (this.request === request) this.request = null
      if (this.active && this.pending && this.timer === null) this.dispatch()
    }
  }
}
