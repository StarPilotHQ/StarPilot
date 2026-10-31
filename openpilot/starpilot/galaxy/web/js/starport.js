import { GxState } from "./state.js"
import { GxDialog } from "./dialog.js"
import { GxIconButton } from "./icon-button.js"
import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { connectionError } from "./polling.js"
import { BluetoothDeviceList } from "./bluetooth-devices.js"
import { BluetoothNotice, bluetoothConnection } from "./bluetooth-notice.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"

export function uploadTimeout(bytes) {
  return Math.max(180000, Math.ceil(bytes / (64 * 1024)) * 1000)
}

export function uploadPackage(path, options, progress, makeRequest = () => new XMLHttpRequest()) {
  return new Promise((resolve, reject) => {
    const xhr = makeRequest()
    let settled = false
    const abort = () => xhr.abort()
    const finish = (callback, value) => {
      if (settled) return
      settled = true
      options.signal?.removeEventListener("abort", abort)
      callback(value)
    }
    xhr.open("POST", path)
    xhr.withCredentials = true
    xhr.timeout = options.timeout ?? uploadTimeout(options.body?.size || 0)
    xhr.setRequestHeader("Content-Type", "application/octet-stream")
    xhr.upload.onprogress = (event) => {
      if (!settled && event.lengthComputable) progress({ loaded: event.loaded, total: event.total })
    }
    xhr.onload = () => finish(resolve, {
      status: xhr.status, ok: xhr.status >= 200 && xhr.status < 300,
      json: async () => JSON.parse(xhr.responseText)
    })
    xhr.onerror = () => finish(reject, new Error("Upload interrupted. Check your connection and retry."))
    xhr.ontimeout = () => finish(reject, new Error("Upload timed out. Check your connection and retry."))
    xhr.onabort = () => finish(reject, new Error("Upload canceled."))
    options.signal?.addEventListener("abort", abort, { once: true })
    if (options.signal?.aborted) { finish(reject, new Error("Upload canceled.")); return }
    xhr.send(options.body)
  })
}

const ADDRESS = /^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$/
const ADAPTER_MODELS = { aawireless: "AAWireless", default: "Motorola MA1", unofficial: "Unofficial dongle" }
const HEAD_UNIT_APPS = { headunitpad: "HeadunitPad", openheadunit: "Open Headunit" }
const HOTSPOT_LINK = `<a class="gx-link" href="#/galaxy">Galaxy Wi-Fi Hotspot</a>`
const WIRED_STATUS = `<div v-if="wired" class="gx-note">
  <div class="gx-starport-status">
    <span class="gx-starport-pill" :class="'gx-starport-pill--' + wiredStatus.tone" role="status">{{ wiredStatus.label }}</span>
    <a v-if="wiredJetlinkBusy" class="gx-link" href="#/manage_models">Turn off JetLink <i class="bi bi-arrow-right" aria-hidden="true"></i></a>
  </div>
  <p>{{ wiredStatus.detail }}</p>
</div>`
const CONNECT = `<button v-if="!runtime?.running" class="gx-btn gx-starport-cta" :disabled="!!connectReason" @click="connect">{{ selected && pairsHere ? 'Connect to ' + selected.name : app ? 'Connect to ' + app : adapterSetup ? 'Connect to adapter' : 'Connect to car' }}</button>
  <button v-else class="gx-btn gx-btn--tonal gx-starport-cta" @click="control('stop')">Disconnect</button>`
const CONNECTION_PROGRESS = `
  <div v-if="!showPairSteps" class="gx-starport-progress" :class="'gx-starport-progress--' + connectionProgress.tone" role="status" aria-live="polite" aria-atomic="true">
    <div class="gx-starport-progress__heading">
      <span v-if="connectionProgress.active" class="gx-spinner gx-starport-orbit" aria-hidden="true"></span>
      <i v-else :class="'bi bi-' + connectionProgress.icon" aria-hidden="true"></i>
      <div><small>{{ connectionProgress.eyebrow }}</small><strong>{{ connectionProgress.title }}</strong></div>
    </div>
    <p>{{ connectionProgress.detail }}</p>
    <div v-if="connectionProgress.active" class="gx-starport-progress__track" aria-hidden="true"><span></span></div>
  </div>`
const AUTO_CONNECT = `<GalaxySettingRow :row="switchRow('Automatic Connection', runtime?.auto_connect, 'Connect to your car whenever you start driving.')"
    :index="0" :busy="busy" :disabled="!setup.enabled || !setup.parked || !!pairing?.active"
    :save-value="(index, value) => control('auto_connect', { enabled: value === 'On' })" />`
const APP_SETUP = `
  <div v-if="app === 'HeadunitPad'" class="gx-starport-guide" role="note">
    <strong>HeadunitPad setup (iPad)</strong>
    <ol>
      <li>Install <a class="gx-link" href="https://github.com/andyching168/HeadunitPad" target="_blank" rel="noopener noreferrer">HeadunitPad from GitHub</a> on the iPad.</li>
      <li>Put the iPad on the same Wi-Fi as the comma, or turn on the ${HOTSPOT_LINK} and join its TheGalaxy network.</li>
      <li>In HeadunitPad, choose Headunit Server or Auto mode, landscape, at 800x480, 1280x720 or 1920x1080.</li>
      <li>Tap Connect here, then open HeadunitPad.</li>
    </ol>
  </div>
  <div v-else-if="app === 'Open Headunit'" class="gx-starport-guide" role="note">
    <strong>Open Headunit setup (Android tablet)</strong>
    <ol>
      <li>Install <a class="gx-link" href="https://github.com/andreknieriem/open-headunit/releases" target="_blank" rel="noopener noreferrer">Open Headunit from GitHub</a> on the tablet.</li>
      <li>Put the tablet on the same Wi-Fi as the comma, or turn on the ${HOTSPOT_LINK} and join its TheGalaxy network.</li>
      <li>In Open Headunit Settings, add WiFi to Connection mode. Under Wireless Connection, set Wireless Mode to Headunit Server and Server Mode to Auto.</li>
      <li>Set Resolution to 480p, 720p or 1080p and Video Codec to H.264 or Auto.</li>
      <li>Tap Connect here, then tap the WiFi button in Open Headunit.</li>
    </ol>
  </div>`
export const CAR_TYPES = {
  builtin: "Built-in wireless", adapter: "Wireless adapter", usb: "Wired AA", app: "App"
}
const PROMPT = new Set(["pin", "passkey", "confirmation", "authorization", "display_pin", "display_passkey"])

export function validPromptInput(prompt, value) {
  if (prompt?.kind === "pin") return /^[\x20-\x7E]{1,16}$/.test(value)
  if (prompt?.kind === "passkey") return /^[0-9]{1,6}$/.test(value)
  return ["confirmation", "authorization"].includes(prompt?.kind) && value === ""
}

export function validPairingStatus(value) {
  if (!value || typeof value.active !== "boolean" || typeof value.approved !== "boolean") return false
  if (value.devices !== undefined && (!Array.isArray(value.devices) || !value.devices.every(validDiscoveredDevice))) return false
  if (value.discovering !== undefined && typeof value.discovering !== "boolean") return false
  if (value.state !== undefined && !["idle", "pairing", "paired", "failed", "connecting", "connected"].includes(value.state)) return false
  if (value.error !== undefined && typeof value.error !== "string") return false
  const receiver = value.receiver
  if (receiver !== null && (!ADDRESS.test(receiver?.address) || typeof receiver.name !== "string" || receiver.name.length > 80)) return false
  const prompt = value.prompt
  return prompt === null || (receiver !== null && /^[0-9a-f]{32}$/.test(prompt?.id) && PROMPT.has(prompt.kind) &&
    typeof prompt.value === "string" && prompt.value.length <= 16 && typeof prompt.displayOnly === "boolean")
}

export function validDiscoveredDevice(value) {
  return value !== null && ADDRESS.test(value?.address) && typeof value.name === "string" && value.name.length <= 80 &&
    ["paired", "connected", "starport"].every((key) => typeof value[key] === "boolean")
}

function validSelected(value) {
  return value === null || (ADDRESS.test(value?.address) && typeof value.name === "string" && value.name.length <= 80)
}

function validSetup(value) {
  return value && typeof value.enabled === "boolean" && typeof value.bluetoothEnabled === "boolean" &&
    typeof value.parked === "boolean" && typeof value.installReady === "boolean" && typeof value.serviceReady === "boolean" &&
    value.identity && typeof value.identity.installed === "boolean" && value.import &&
    Number.isSafeInteger(value.maxUploadBytes) && value.maxUploadBytes > 0
}

export class StarPortFeed {
  constructor({ publish, unauthorized = () => { }, fetcher = (...args) => fetch(...args),
    uploader = uploadPackage, later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, uploader, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.controller = null
    this.timer = null
    this.pollTimer = null
    this.setup = null
    this.pairing = null
    this.selected = null
    this.runtime = null
    this.receivers = []
    this.endReason = ""
    this.busy = false
    this.refreshing = false
    this.deviceList = new BluetoothDeviceList()
    this.receiverList = new BluetoothDeviceList()
    this.uploadProgress = null
    this.error = ""
  }

  emit() {
    this.publish({
      setup: this.setup, pairing: this.pairing, selected: this.selected, runtime: this.runtime,
      receivers: this.receivers,
      endReason: this.endReason, busy: this.busy, uploadProgress: this.uploadProgress, error: this.error
    })
  }

  stop(cancelPair = false) {
    if (cancelPair && this.active && this.pairing?.active) {
      this.fetcher("./api/starport/pairing/cancel", {
        method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json" }, body: "{}"
      }).catch(() => { })
    }
    this.active = false
    this.generation++
    this.controller?.abort()
    if (this.timer !== null) this.cancelTimer(this.timer)
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.controller = this.timer = this.pollTimer = null
    this.busy = false
    this.refreshing = false
    this.deviceList.reset()
    this.receiverList.reset()
    this.uploadProgress = null
    this.pairing = null
    this.emit()
  }

  start() {
    this.stop()
    this.active = true
    return this.refresh()
  }

  async request(path, options = {}, timeout = 8000, transport = this.fetcher, background = false) {
    if (!background) {
      // A user action supersedes a pending poll, including a delayed JSON body.
      this.generation++
      this.controller?.abort()
      if (this.timer !== null) this.cancelTimer(this.timer)
      if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
      this.pollTimer = null
      this.refreshing = false
    }
    const generation = this.generation
    const controller = new AbortController()
    this.controller = controller
    if (!background) { this.busy = true; this.emit() }
    try {
      const { response, data } = await requestJson(path, {
        fetcher: transport, timeout, later: this.later, cancel: this.cancelTimer,
        request: { ...options, signal: controller.signal }, withResponse: true
      })
      if (!this.active || this.generation !== generation || controller.signal.aborted) return null
      if (response.status === 401) { this.stop(); this.unauthorized(); return null }
      if (!this.active || this.generation !== generation || controller.signal.aborted) return null
      if (!response.ok) throw new Error(data?.error || "StarPort setup is unavailable")
      if (!background) this.error = ""
      return data
    } catch (error) {
      if (this.active && this.generation === generation) {
        this.error = error.name === "TimeoutError" ? "StarPort took too long to respond. Reconnecting automatically…" : connectionError(error)
      }
      return null
    } finally {
      if (this.controller === controller) {
        this.cancelTimer(this.timer)
        this.controller = this.timer = null
        if (!background) {
          this.busy = false
          if (this.active && this.generation === generation && this.pollTimer === null) {
            this.pollTimer = this.later(() => this.refresh(), this.pairing?.active ? 1000 : 5000)
          }
        }
        this.emit()
      }
    }
  }

  async refresh() {
    if (!this.active || this.busy || this.refreshing) return
    this.refreshing = true
    if (this.pollTimer !== null) { this.cancelTimer(this.pollTimer); this.pollTimer = null }
    const generation = this.generation
    const setup = await this.request("./api/starport/setup", {}, 8000, this.fetcher, true)
    if (!this.active || this.generation !== generation) return
    const setupValid = setup && validSetup(setup)
    let healthy = !!setupValid
    if (setupValid) this.setup = setup
    else if (setup) this.error = "StarPort setup response changed"
    if (setupValid && !setup.enabled) {
      this.pairing = this.selected = this.runtime = null
      this.receivers = []
      this.deviceList.reset()
      this.receiverList.reset()
      this.emit()
    }
    if (this.active && this.generation === generation && setupValid && setup.enabled) {
      const result = await this.request("./api/starport/pairing/status", {}, 8000, this.fetcher, true)
      if (!this.active || this.generation !== generation) return
      if (result && validPairingStatus(result.pairing) && validSelected(result.selectedReceiver)) {
        const wasActive = this.pairing?.active
        if (!result.pairing.active) this.deviceList.reset()
        this.pairing = { ...result.pairing, devices: this.deviceList.update(result.pairing.devices || []) }
        this.selected = result.selectedReceiver
        this.runtime = result.runtime && typeof result.runtime === "object" ? result.runtime : null
        if (wasActive && !this.pairing.active && !this.selected && !this.endReason) this.endReason = "ended"
      }
      else { healthy = false; if (result) this.error = "Pairing status response changed" }
    }
    this.refreshing = false
    if (healthy) this.error = ""
    this.emit()
    if (this.active && this.generation === generation) {
      const delay = this.pairing?.active || this.setup?.import?.state === "running" ? 1000 : 5000
      this.pollTimer = this.later(() => this.refresh(), delay)
    }
  }

  async action(path, body = {}, timeout = 12000) {
    if (!this.active || this.busy || !this.setup?.parked || !this.setup?.enabled) return false
    const result = await this.request(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }, timeout)
    if (result === null) return false
    if (path.endsWith('/pairing')) this.endReason = ""
    if (path.endsWith('/cancel')) this.endReason = "canceled"
    await this.refresh()
    return true
  }

  async selectDevice(address) {
    if (!ADDRESS.test(address) || !this.pairing?.active || this.pairing?.prompt ||
      ["pairing", "connecting"].includes(this.pairing?.state) || this.runtime?.running ||
      !this.pairing?.devices?.some((device) => device.address === address)) return false
    return this.action("./api/starport/pairing/select", { address })
  }

  async loadReceivers() {
    if (!this.active || this.busy || !this.setup?.enabled || this.pairing?.active) return false
    const result = await this.request("./api/starport/receivers")
    if (result === null) return false
    if (!Array.isArray(result.receivers) || !result.receivers.every((car) => car !== null && validSelected(car))) {
      this.error = "Paired car list changed"
      this.emit()
      return false
    }
    this.receivers = this.receiverList.update(result.receivers)
    this.emit()
    return true
  }

  async control(action, extra = {}) {
    if (!this.active || this.busy || !this.setup?.enabled || this.pairing?.active ||
      !["start", "stop", "select_receiver", "auto_connect", "connection", "car_type", "car_profile"].includes(action)) return false
    const result = await this.request("./api/starport/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, ...extra })
    })
    if (result === null) return false
    await this.refresh()
    return true
  }

  async setEnabled(enabled, enableBluetooth = false) {
    if (!this.active || this.busy || typeof enabled !== "boolean" ||
      typeof enableBluetooth !== "boolean" || enableBluetooth && !enabled || enabled && !this.setup?.installReady) return false
    const payload = { enabled, ...(enableBluetooth ? { enableBluetooth: true } : {}) }
    const result = await this.request("./api/starport/enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }, enableBluetooth ? 45000 : 8000)
    if (result === null) return false
    await this.refresh()
    return true
  }

  async upload(file) {
    if (!this.active || this.busy || !this.setup || this.setup.import?.state === "running" ||
      !file || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > this.setup.maxUploadBytes) {
      this.error = "Choose an APK, XAPK, or APKM within the shown size limit."
      this.emit()
      return false
    }
    this.uploadProgress = { loaded: 0, total: file.size }
    const generation = this.generation + 1
    // Slow tunnel links need longer than three minutes for large packages.
    const timeout = uploadTimeout(file.size)
    const transport = (path, options) => this.uploader(path, { ...options, timeout }, (progress) => {
      if (this.active && generation === this.generation) { this.uploadProgress = progress; this.emit() }
    })
    const result = await this.request("./api/starport/upload", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" }, body: file
    }, timeout, transport)
    this.uploadProgress = null
    this.emit()
    if (result === null) return false
    await this.refresh()
    return true
  }

  async removePackage() {
    if (!this.active || this.busy || !this.setup || this.setup.import?.state === "running") return false
    const result = await this.request("./api/starport/identity", { method: "DELETE" }, 30000)
    if (result === null) return false
    await this.refresh()
    return true
  }
}

export const APKMIRROR_URL = "https://www.apkmirror.com/apk/google-inc/android-auto/"
// Signed APKPure links the comma looked up; the browser downloads the app itself.
export const DOWNLOAD_LINK = /^https:\/\/download\.pureapk\.com\/b\/X?APK\/[!-~]+$/
const WRONG_DOWNLOAD = "That’s not the Android Auto download. Download it again in step 1."

// While the comma is still trying APKPure, wait instead of sending people to APKMirror.
export function downloadLink(release, pending = false) {
  if (typeof release?.url === "string" && DOWNLOAD_LINK.test(release.url) && typeof release.version === "string" &&
    release.version.length <= 128 && /^[0-9]+(?:\.[0-9]+)+-release$/.test(release.version) &&
    Number.isSafeInteger(release.size) && release.size > 0) {
    return { direct: true, url: release.url, version: release.version.replace(/-release$/, ""), megabytes: Math.ceil(release.size / 1048576) }
  }
  return pending ? { direct: false, pending: true, url: "" } : { direct: false, url: APKMIRROR_URL }
}
const STAGES = ["reading", "searching", "decrypting", "verifying", "installing", "done"]
const CHECKS = [
  { stage: "reading", codes: ["NOT_PACKAGE", "CORRUPT", "NO_CODE", "TOO_LARGE"], label: "Android package opened" },
  { stage: "searching", codes: ["WRONG_APP", "UNVERIFIED"], label: "Google Automotive Link certificate found" },
  { stage: "decrypting", codes: ["UNSUPPORTED_VERSION"], label: "Security key unlocked" },
  { stage: "verifying", codes: ["EXPIRED", "NOT_YET_VALID"], label: "Certificate is valid" },
]

// Certificate dates are UTC instants; showing them in UTC keeps "valid until" on the day the car checks.
export function formatDate(iso, options = { month: "short", day: "numeric", year: "numeric" }, locale = undefined) {
  const date = new Date(iso)
  return typeof iso !== "string" || Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(locale, { timeZone: "UTC", ...options })
}

export function expiryInfo(identity, locale = undefined) {
  if (identity?.installed) {
    const days = Number.isSafeInteger(identity.days_left) ? identity.days_left : null
    if (days !== null && days <= 14) {
      const day = formatDate(identity.expires, { month: "short", day: "numeric" }, locale)
      const left = days <= 0 ? "Expires within a day" : `Expires in ${days} day${days === 1 ? "" : "s"}`
      return { level: "soon", label: day ? `${left} (${day})` : left }
    }
    const until = formatDate(identity.expires, undefined, locale)
    return { level: "ready", label: until ? `Ready · Valid until ${until}` : "Ready" }
  }
  if (identity?.expired) {
    const on = formatDate(identity.expires, undefined, locale)
    return { level: "expired", label: on ? `Expired on ${on}` : "Expired" }
  }
  if (identity?.error) return { level: "broken", label: "Needs reinstalling" }
  return { level: "none", label: "Not set up" }
}

// Name-only checks that catch the common wrong downloads before a long upload.
// APKMirror names the real app "…-apkmirror.com.apk", so "apkmirror" alone is fine.
export function packagePreflight(file) {
  const name = typeof file?.name === "string" ? file.name : ""
  if (/installer|apkmirror\.helper/i.test(name)) {
    return "This is the APKMirror Installer app, not Android Auto. Go back and tap Download APK on the StarPort page itself."
  }
  if (/com\.google\.android\.gms|play[ ._-]?services/i.test(name)) {
    return "This is Google Play Services, not Android Auto. Go back and download Android Auto by Google LLC."
  }
  if (name && !/\.(apk|xapk|apkm|zip)$/i.test(name)) return "That’s not an Android package. Choose the .apk, .xapk, .apkm, or .zip file you downloaded."
  return ""
}

export function importProblem(job, fileName = "", locale = undefined) {
  if (job?.state !== "failed") return ""
  switch (job.code) {
    case "NOT_PACKAGE": case "CORRUPT":
      return "This file looks incomplete or damaged. Download it again and wait for the download to finish."
    case "NO_CODE": return "This file is only part of the app. Download the full Android Auto APK, or the XAPK/APKM bundle."
    case "WRONG_APP":
      return packagePreflight({ name: fileName }) ||
        `Wrong app selected${fileName ? ` (${fileName})` : ""}. Choose Android Auto by Google LLC.`
    case "UNVERIFIED": return "No phone certificate issued by Google’s Automotive Link root was found. Choose another Android Auto release."
    case "EXPIRED": {
      const on = formatDate(job.expires, undefined, locale)
      return `This Android Auto version expired${on ? ` on ${on}` : ""}. Download a newer stable release.`
    }
    case "NOT_YET_VALID": return "This version isn’t valid yet. Check that the comma’s date and time are correct."
    case "UNSUPPORTED_VERSION":
      return job.error || "This version stores its key differently. Choose another Android Auto release."
    case "TOO_LARGE": return "This file is too large to be the Android Auto app."
    case "CANCELLED": return "Setup authorization changed. Sign in to Galaxy and try again."
    default: return job.error || "Something went wrong. Try again."
  }
}

export function installChecks(job, locale = undefined) {
  const at = job?.state === "done" ? STAGES.length : STAGES.indexOf(job?.stage)
  const ends = CHECKS.map((_, index) => STAGES.indexOf(index + 1 < CHECKS.length ? CHECKS[index + 1].stage : "done"))
  let failed = -1
  if (job?.state === "failed") {
    failed = CHECKS.findIndex((check) => check.codes.includes(job.code))
    if (failed < 0) failed = Math.max(0, ends.findIndex((end) => at < end))
  }
  return CHECKS.map((check, index) => {
    const label = index === CHECKS.length - 1 && job?.state === "done" && formatDate(job.expires, undefined, locale) ?
      `Certificate valid until ${formatDate(job.expires, { month: "long", day: "numeric", year: "numeric" }, locale)}` : check.label
    const status = failed >= 0 ? (index < failed ? "done" : index === failed ? "failed" : "pending") :
      at >= ends[index] ? "done" : at >= STAGES.indexOf(check.stage) ? "active" : "pending"
    return { label, status }
  })
}

export function connectionProgress(runtime, { wired = false, app = "", selected = null } = {}) {
  const state = runtime?.state || "idle"
  const base = { active: false, tone: "ready", icon: "arrow-right-circle", eyebrow: "NEXT · CONNECT" }
  if (state === "streaming") return { ...base, tone: "success", icon: "check-circle-fill", eyebrow: "CONNECTED", title: "Your dashboard is live", detail: "StarPilot is showing on your screen. You’re ready to drive." }
  if (state === "suspended") return { ...base, title: "Connected · car screen in use", detail: "Open Android Auto on your car’s screen to return to StarPilot." }
  if (state === "error" || state === "backoff") return { ...base, active: state === "backoff" && !!runtime?.running, tone: "warning", icon: "exclamation-circle", eyebrow: state === "backoff" ? "RECONNECTING" : "NEEDS ATTENTION", title: state === "backoff" ? "Trying your connection again" : "Connection needs attention", detail: runtime.error || runtime.detail || "Check your device, then try connecting again." }
  if (runtime?.running && state !== "idle") {
    const titles = { wifi_info: "Getting car Wi-Fi details", joining_wifi: "Joining car Wi-Fi", connecting_bluetooth: "Connecting over Bluetooth", discovering: "Finding Android Auto", rfcomm: "Starting wireless setup", wifi_start: "Waiting for car Wi-Fi", connecting_tcp: "Opening the connection", authenticating: "Securing your connection", negotiating: "Preparing your dashboard", waiting_for_usb: "Waiting for your USB cable", usb_accessory: "Starting USB connection", waiting_for_ipad: "Open HeadunitPad on your iPad", waiting_for_tablet: "Open Open Headunit on your tablet", stopping: "Disconnecting" }
    return { ...base, active: true, tone: "active", eyebrow: "CONNECTION IN PROGRESS", title: titles[state] || runtime.label || "Connecting to your device", detail: runtime.detail || (wired ? "Keep the data cable connected and the car’s screen on." : app ? "Keep the app open and both devices on the same Wi-Fi." : "Keep your car’s screen on. We’ll move to the next step automatically.") }
  }
  return { ...base, title: wired ? "Connect your USB cable" : app ? `Ready for ${app}` : selected ? `Ready for ${selected.name}` : "Pair your device first", detail: wired ? "Use the car’s Android Auto data port, then tap Connect below." : app ? "Complete the app setup above, then tap Connect below." : selected ? "Tap Connect to bring StarPilot to your dashboard." : "Follow the pairing steps above, then connect to your dashboard." }
}

export const StarPortPage = {
  components: { GxState, GxIconButton, GxNotice, BluetoothNotice, GxDialog, GalaxySettingRow },
  props: {
    mode: { type: String, required: true }, localAccess: { type: Boolean, required: true },
    unauthorized: { type: Function, required: true }
  },
  data: () => ({
    bluetooth: bluetoothConnection(),
    setup: null, pairing: null, selected: null, runtime: null, receivers: [], endReason: "", busy: false, error: "",
    pairValue: "", packageFile: null, uploadProgress: null, installOpen: false, removeOpen: false, installAttempted: false, installBaseline: null,
    downloadStarted: false, packageMagicError: "", packageChecking: false, packageSelection: 0,
    installFileName: "", pairWhenReady: false, enableOpen: false, showSteps: false, starting: false, supportOpen: false
  }),
  mounted() {
    this.feed = new StarPortFeed({ publish: (value) => Object.assign(this, value), unauthorized: this.unauthorized })
    this.visibility = () => {
      if (document.hidden) this.feed.stop(true)
      else this.begin()
    }
    document.addEventListener("visibilitychange", this.visibility)
    if (!document.hidden) this.begin()
  },
  beforeUnmount() { this.finishEnable(false); document.removeEventListener("visibilitychange", this.visibility); this.feed?.stop(true) },
  watch: {
    mode() { if (!document.hidden) this.begin(); else this.feed?.stop(true) },
    localAccess() { if (!document.hidden) this.begin(); else this.feed?.stop(true) },
    'pairing.prompt.id'() { this.pairValue = "" },
    'pairing.active'(active, was) { if (was && !active) this.loadReceivers() },
    // Find car on a fresh device turns Android Auto on first, then searches once the service is up.
    'setup.serviceReady'() { this.resumePairing() },
    busy(value) { if (!value) this.resumePairing() },
    'setup.enabled'(enabled) { if (!enabled) this.pairWhenReady = false },
  },
  computed: {
    connectionProgress() { return connectionProgress(this.runtime, { wired: this.wired, app: this.app, selected: this.selected }) },
    canRespond() { return validPromptInput(this.pairing?.prompt, this.pairValue) },
    expiry() { return expiryInfo(this.setup?.identity) },
    projecting() { return this.runtime?.state === "streaming" },
    installedVersion() {
      const version = this.setup?.identity.installed && this.setup.identity.app_version
      return typeof version === "string" ? version.replace(/-release.*$/, "") : ""
    },
    supportReady() { return !!(this.setup?.enabled && this.setup.identity.installed) },
    // Returning drivers see a status card; the setup steps fold away below it.
    wired() { return this.runtime?.connection === "wired" },
    app() { return HEAD_UNIT_APPS[this.runtime?.connection] || "" },
    homeTitle() {
      if (this.wired) return "Wired Android Auto"
      if (this.app) return "Headunit Emulator"
      return this.projecting ? "Connected to " + this.connectionName : this.runtime?.running ? "Connecting to " + this.connectionName : this.connectionName
    },
    wirelessHome() { return !this.wired && !this.app },
    connectionName() { return this.app === "HeadunitPad" ? "iPad" : this.app ? "tablet" : this.wired ? "car" : this.selected?.name || "car" },
    // Waiting or retrying is not a connection: only a live link blocks changes, and those stop the wait first.
    linked() { return ["streaming", "suspended"].includes(this.runtime?.state) },
    waiting() { return !!this.runtime?.running && !this.linked },
    supportCollapsed() { return this.supportReady && this.setup.installReady && !this.supportOpen },
    setupDone() {
      return !!(this.supportReady && (this.selected || this.wired || this.app) && !this.pairing?.active &&
        !(this.adapterSetup && this.runtime?.car_profile_source !== "override"))
    },
    wiredStatus() {
      if (this.runtime?.wired_blocked) return { tone: "expired", label: "Wired StarPort not enabled", detail: this.runtime.wired_blocked }
      if (this.wiredJetlinkBusy) return { tone: "expired", label: "Wired StarPort not enabled", detail: "JetLink is using the USB-C port. Turn off JetLink, then return here." }
      if (!this.setup?.enabled) return { tone: "expired", label: "Wired StarPort not enabled", detail: "Turn on StarPort in step 1." }
      return { tone: "ready", label: "Wired StarPort enabled", detail: "Connect the car’s Android Auto USB data port to the comma’s USB-C port with a data-capable cable." }
    },
    appRow() {
      return { label: "App", value: this.app || "Off", choices: ["Off", ...Object.values(HEAD_UNIT_APPS)], available: true, action: true,
        reason: "Project to a tablet app on the same Wi-Fi as the comma or the Galaxy Wi-Fi Hotspot instead of the car." }
    },
    carType() { return this.runtime?.car_type || (this.wired ? "usb" : this.app ? "app" : "builtin") },
    adapterSetup() { return this.carType === "adapter" },
    pairsHere() { return this.carType === "builtin" || this.carType === "adapter" },
    findLabel() { return this.adapterSetup ? "adapter" : "car" },
    carTypeLabels() { return { ...CAR_TYPES, app: this.app ? `App (${this.app})` : CAR_TYPES.app } },
    wiredUnavailable() { return !this.wired && !!this.runtime?.wired_blocked },
    carTypeRow() {
      const labels = this.carTypeLabels
      return { label: "What’s your device?", value: labels[this.carType], choices: Object.values(labels), available: true, action: true,
        reason: this.wired ? "Use a data cable from the car to your comma." : this.app ? "Use a tablet on the same Wi-Fi as your comma." : this.adapterSetup ? "Pair the USB adapter, such as AAWireless or Motorola MA1." : "Pair directly with your car’s wireless Android Auto." }
    },
    adapterRow() {
      const chosen = this.runtime?.car_profile_source === "override"
      return { label: "Adapter", value: !chosen ? "Choose one" : ADAPTER_MODELS[this.runtime.car_profile] || ADAPTER_MODELS.default,
        choices: [...(!chosen ? ["Choose one"] : []), ...Object.values(ADAPTER_MODELS)], available: true, action: true,
        reason: "AAWireless gets video and car-data settings tuned for that dongle. Motorola MA1 uses the regular pairing. Pick Unofficial dongle for other wireless dongles that will not connect; it tries alternate handshakes and logs more." }
    },
    appDisabled() {
      return !this.setup?.enabled || !this.setup.parked || !!this.pairing?.active || this.linked
    },
    wiredJetlinkBusy() { return this.wired && !!this.runtime?.wired_busy },
    download() { return downloadLink(this.setup?.download, this.setup?.downloadPending === true) },
    uploadPercent() {
      const { loaded, total } = this.uploadProgress || {}
      return total > 0 ? Math.min(100, Math.round(100 * loaded / total)) : 0
    },
    downloadsHint() {
      if (typeof navigator === "undefined") return ""
      if (/iPhone|iPad|iPod/i.test(navigator.userAgent || "") || navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) return "In Files, open Downloads."
      return /Android/i.test(navigator.userAgent || "") ? "Open Downloads." : ""
    },
    packageError() {
      if (!this.packageFile) return ""
      const preflight = packagePreflight(this.packageFile)
      if (preflight) return preflight
      if (!Number.isSafeInteger(this.packageFile.size) || this.packageFile.size <= 0) return "The selected file is empty or its size cannot be read."
      if (this.setup && this.packageFile.size > this.setup.maxUploadBytes) return "The selected package exceeds the maximum size shown below."
      return this.packageMagicError
    },
    uploadReason() {
      if (this.bluetooth) return "Package uploads are not available over Bluetooth. Connect using Galaxy Wi-Fi or LAN."
      if (!this.packageFile) return "Choose your Android Auto package."
      if (this.packageError) return this.packageError
      if (this.packageChecking) return "Checking downloaded file…"
      if (!this.setup) return "Load setup status first."
      if (!this.setup.enabled && !this.setup.installReady) return "The StarPort display and encoder must be installed before uploading."
      if (this.setup.import?.state === "running") return "Wait for the current package check to finish."
      return ""
    },
    installState() {
      const job = this.setup?.import
      const current = this.installAttempted && job && job.started !== this.installBaseline
      if (this.uploadProgress || this.installAttempted && this.busy && !current) return "uploading"
      if (job?.state === "running") return "checking"
      if (!current) return "choose"
      if (job.state === "done" && this.setup.identity.installed) return "done"
      return job.state === "failed" ? "failed" : "choose"
    },
    checks() { return installChecks(this.setup?.import) },
    problem() { return importProblem(this.setup?.import, this.installFileName) },
    hasPackage() { return !!(this.setup?.identity.installed || this.setup?.identity.expired || this.setup?.identity.error) },
    onPhone() {
      return typeof navigator !== "undefined" && (navigator.userAgentData?.mobile === true ||
        /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "") || navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
    },
    removeReason() {
      if (!this.setup) return "Load setup status first."
      if (this.setup.import?.state === "running") return "Wait for the current package check to finish."
      return ""
    },
    // Bluetooth pairing and the car's Wi-Fi handoff need no Android Auto package; only projection does.
    pairingReason() {
      if (!this.setup) return "Load setup status first."
      if (!this.setup.installReady) return "This build is missing the Android Auto display or encoder."
      if (this.setup.enabled && !this.setup.serviceReady) return "Waiting for the Android Auto service to start."
      if (!this.setup.bluetoothEnabled && this.setup.enabled) return "Turn on Bluetooth on the Bluetooth page."
      if (this.adapterSetup && this.runtime?.car_profile_source !== "override") return "Choose your adapter model first."
      if (!this.setup.parked) return "Use offroad mode or Park before starting pairing."
      if (this.linked) return "Disconnect from your car before pairing."
      return ""
    },
    connectReason() {
      if (!this.selected && !this.wired && !this.app) return this.adapterSetup ? "Pair your adapter in step 2 first." : "Pair your car in step 2 first."
      if (!this.setup?.enabled) return "Turn on StarPort in step 1."
      if (!this.setup.serviceReady) return "Waiting for the Android Auto service to start."
      if (this.pairing?.active) return "Finish or cancel pairing first."
      if (this.adapterSetup && this.runtime?.car_profile_source !== "override") return "Choose your adapter model in step 2 first."
      if (this.wired && this.runtime?.wired_blocked) return this.runtime.wired_blocked
      if (this.wiredJetlinkBusy) return "Turn off Jetlink first."
      return ""
    },
    pairSteps() {
      const state = this.pairing?.state, active = !!this.pairing?.active
      const paired = ["paired", "connected"].includes(state)
      const picked = !!(this.pairing?.receiver || this.pairing?.approved || ["pairing", "connecting"].includes(state) || paired)
      const what = this.adapterSetup ? "adapter" : "car"
      const failed = state === "failed" || !!this.pairing?.error
      const rank = paired ? 5 : state === "connecting" ? 4 : this.pairing?.approved ? 3 : this.pairing?.prompt ? 2 : picked ? 2 : active ? 1 : 0
      const steps = [
        `Start the search${this.starting ? "…" : ""}`,
        `Put your ${what} in pairing mode, then pick it from the list`,
        this.pairing?.prompt ? "Confirm the code on both screens" : "Pair with your device",
        "Finish Bluetooth pairing",
        "Finish connecting to your device"
      ]
      return steps.map((label, index) => ({ label, status: index < rank ? "done" : index === rank ? (failed ? "failed" : "active") : "pending" }))
    },
    showPairSteps() { return this.starting || !!this.pairing?.active },
    waitingForCar() {
      return this.pairing?.active && !this.pairing?.receiver && !this.pairing?.prompt && !this.pairing?.approved &&
        !["pairing", "connecting", "paired", "connected", "failed"].includes(this.pairing?.state)
    },
    deviceSelectionBlocked() {
      return !!this.pairingReason || !this.pairing?.active ||
        !!this.pairing?.prompt || ["pairing", "connecting"].includes(this.pairing?.state) || !!this.linked
    },
  },
  methods: {
    switchRow,
    stopWaiting() { return this.waiting ? this.control("stop") : null },
    afterWaiting(next) {
      const stopping = this.stopWaiting()
      return stopping ? stopping.then((stopped) => stopped && next()) : next()
    },
    useCar(address) { return this.afterWaiting(() => this.control("select_receiver", { address })) },
    saveCarType(index, value) {
      const labels = this.carTypeLabels
      const carType = Object.keys(labels).find(key => labels[key] === value)
      if (carType === "usb" && this.wiredUnavailable) {
        this.feed.error = `Wired AA is unavailable: ${this.runtime.wired_blocked}.`
        this.feed.emit()
        return false
      }
      if (carType === this.carType) return false
      return carType ? this.afterWaiting(() => this.control("car_type", { carType })) : false
    },
    saveAdapter(index, value) {
      const profile = Object.keys(ADAPTER_MODELS).find(key => ADAPTER_MODELS[key] === value)
      return profile ? this.afterWaiting(() => this.control("car_profile", { profile })) : false
    },
    saveApp(index, value) {
      const connection = Object.keys(HEAD_UNIT_APPS).find(key => HEAD_UNIT_APPS[key] === value) || "wireless"
      return this.afterWaiting(() => this.control("connection", { connection }))
    },
    async begin() {
      await this.feed.start()
      if (this.setup?.enabled && this.setup.serviceReady && !this.pairing?.active) this.loadReceivers()
    },
    refresh() { return this.feed.refresh() },
    setEnabled(enabled) {
      if (!enabled) {
        this.pairWhenReady = false
        this.finishEnable(false)
        return this.feed.setEnabled(false)
      }
      if (!this.setup?.installReady || this.busy || this.enableOpen) return Promise.resolve(false)
      if (this.setup.bluetoothEnabled) return this.feed.setEnabled(true)
      this.enableOpen = true
      return new Promise((resolve) => { this.enableResolve = resolve })
    },
    finishEnable(enabled) {
      const resolve = this.enableResolve
      this.enableResolve = null
      this.enableOpen = false
      resolve?.(enabled)
    },
    cancelEnable() { if (!this.busy) this.finishEnable(false) },
    async confirmEnable() {
      if (!this.enableOpen || this.busy) return false
      const enabled = await this.feed.setEnabled(true, true)
      this.finishEnable(enabled)
      return enabled
    },
    async beginPairing() {
      this.starting = true
      try { return await this.startPairing() } finally { this.starting = false }
    },
    startPairing() {
      if (this.pairingReason) return false
      const stopping = this.stopWaiting()
      if (stopping) return stopping.then((stopped) => stopped && this.startPairing())
      if (!this.setup.enabled) return this.setEnabled(true).then((enabled) => {
        this.pairWhenReady = enabled
        return this.resumePairing()
      })
      return this.feed.action("./api/starport/pairing")
    },
    resumePairing() {
      if (!this.pairWhenReady || !this.setup?.enabled || !this.setup.serviceReady || this.busy) return false
      this.pairWhenReady = false
      return this.startPairing()
    },
    selectDevice(address) { return this.feed.selectDevice(address) },
    cancelPairing() { return this.feed.action("./api/starport/pairing/cancel") },
    respond(accepted) {
      const prompt = this.pairing?.prompt
      if (!prompt || prompt.displayOnly || accepted && !this.canRespond) return
      const value = accepted && ["pin", "passkey"].includes(prompt.kind) ? this.pairValue : ""
      return this.feed.action("./api/starport/pairing/response", { prompt_id: prompt.id, accepted, value })
    },
    openInstall() {
      this.chooseAgain()
      Object.assign(this, { installOpen: true, downloadStarted: false })
    },
    closeInstall() { this.installOpen = false },
    markDownloadStarted() {
      if (!this.busy && ["choose", "failed"].includes(this.installState)) this.chooseAgain()
      this.downloadStarted = true
    },
    chooseAgain() {
      this.packageSelection++
      Object.assign(this, { installAttempted: false, packageFile: null, packageMagicError: "", packageChecking: false })
    },
    async choosePackage(event) {
      const file = event.target.files?.[0] || null
      const selection = ++this.packageSelection
      Object.assign(this, { packageFile: file, packageMagicError: "", packageChecking: false })
      // Leave useful name/size errors intact; never read the full package into browser memory.
      if (!file || packagePreflight(file) || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > this.setup?.maxUploadBytes) return
      this.packageChecking = true
      let error = ""
      try {
        if (file.size < 5 * 1024 * 1024) error = WRONG_DOWNLOAD
        else {
          const bytes = new Uint8Array(await file.slice(0, 4).arrayBuffer())
          if (bytes.length !== 4 || ![0x50, 0x4b, 0x03, 0x04].every((byte, i) => bytes[i] === byte)) error = WRONG_DOWNLOAD
        }
      } catch { error = WRONG_DOWNLOAD }
      if (selection === this.packageSelection) Object.assign(this, { packageMagicError: error, packageChecking: false })
    },
    async upload() {
      if (this.busy || this.uploadReason) return false
      const file = this.packageFile
      Object.assign(this, { installAttempted: true, installBaseline: this.setup.import?.started ?? null, installFileName: file.name || "" })
      if (!this.setup.enabled && !await this.setEnabled(true)) { this.installAttempted = false; return false }
      return this.feed.upload(file)
    },
    openRemove() { if (!this.removeReason) this.removeOpen = true },
    closeRemove() { this.removeOpen = false },
    async removePackage() {
      if (this.removeReason) { this.removeOpen = false; return false }
      this.removeOpen = false
      return this.feed.removePackage()
    },
    connect() {
      if (this.connectReason) return false
      if (!this.setup.identity.installed) { this.openInstall(); return false }
      return this.control("start")
    },
    loadReceivers() { return this.feed.loadReceivers() },
    control(action, extra = {}) { return this.feed.control(action, extra) },
  },
  template: `
    <section class="gx-driving" aria-label="StarPort setup" :inert="busy" :aria-busy="busy || undefined">
      <header class="gx-page-header">
        <h2>StarPort</h2>
        <p>Show the StarPilot driving view on your car’s screen. Choose your device, follow the setup steps, then connect.</p>
      </header>
      <GxNotice tone="info" title="Supported setups">StarPort officially supports your car’s built-in Android Auto, the AAWireless and Motorola MA1 wireless adapters, and the Open Headunit and HeadunitPad tablet apps. Other adapters and apps may work, but they aren’t tested and may not connect reliably.</GxNotice>
      <GxNotice tone="warn" v-if="!wired && !app && runtime?.dedicated_wifi_interface === false">StarPort uses the comma’s Wi-Fi to connect to your car and disconnects it from your current Wi-Fi network. Use the Galaxy hotspot for local access during projection. Remote Galaxy access requires Internet on the comma.</GxNotice>
      <GxNotice tone="danger" v-if="error && setup">{{ error }}</GxNotice>
      <GxNotice v-if="!setup" :busy="!error" :tone="error ? 'danger' : 'info'">{{ error || "Checking StarPort setup… Reconnecting automatically…" }}</GxNotice>
        <section v-if="setup" class="gx-card gx-starport-step" :class="{ 'gx-starport-step--done': supportReady }">
          <div class="gx-starport-step__head">
            <span class="gx-starport-step__num" aria-hidden="true"><i v-if="supportReady" class="bi bi-check-lg"></i><template v-else>1</template></span>
            <div><h3>StarPort Support</h3><p v-if="supportCollapsed" role="status">{{ expiry.label }}</p><p v-else>Turn on StarPort and install support. Wireless pairing also needs Bluetooth on; Galaxy will offer to enable it.</p></div>
            <button v-if="supportReady && setup.installReady" type="button" class="gx-btn gx-btn--tonal" :aria-expanded="!supportCollapsed" aria-controls="gx-starport-support" @click="supportOpen = !supportOpen">{{ supportCollapsed ? 'Manage' : 'Hide' }}</button>
          </div>
          <div id="gx-starport-support" v-show="!supportCollapsed" class="gx-starport-step__body">
            <p v-if="!setup.installReady" class="gx-starport-pill gx-starport-pill--expired" role="status">Not available on this build — the StarPort display and encoder are missing.</p>
            <template v-else>
              <div class="gx-starport-status">
                <span class="gx-starport-pill" :class="'gx-starport-pill--' + expiry.level" role="status">{{ expiry.label }}</span>
                <button v-if="hasPackage" type="button" class="gx-icon-btn gx-recordings__danger" aria-label="Delete Package" :disabled="!!removeReason" :title="removeReason || 'Delete Package'" @click="openRemove"><i class="bi bi-trash3"></i></button>
                <span v-if="setup.enabled && !setup.serviceReady" class="gx-note" role="status">Starting…</span>
                <div class="gx-actions gx-starport-status__actions">
                  <button v-if="!setup.identity.installed" class="gx-btn" @click="openInstall">{{ setup.identity.expired || setup.identity.error ? 'Reinstall' : 'Install support' }}</button>
                  <button v-else-if="expiry.level === 'soon'" class="gx-btn" @click="openInstall">Update</button>
                  <button v-else class="gx-btn gx-btn--tonal" @click="openInstall">Install newer version</button>
                </div>
              </div>
              <GalaxySettingRow :row="switchRow('StarPort', setup.enabled, 'Turn off StarPort to stop connecting to your car. Your setup and paired car stay saved.')"
                :index="0" :busy="busy" :disabled="enableOpen" :save-value="(index, value) => setEnabled(value === 'On')" />
              <div v-if="!setup.bluetoothEnabled"><p class="gx-note">Bluetooth is off. Turn on StarPort to enable it when prompted, or use Bluetooth under Tools.</p>
                <a class="gx-btn gx-btn--tonal" href="#/bluetooth">Open Bluetooth</a></div>
              <p v-if="setup.import?.state === 'running' && !installOpen" class="gx-note" role="status">Checking your package…</p>
            </template>
            <details class="gx-starport-wiki"><summary>Stuck installing? Common problems</summary>
              <ul>
                <li><strong>The download button opens APKMirror instead:</strong> the comma couldn’t reach APKPure just now. Download the newest Android Auto release from APKMirror, or close this window and try again in a few minutes.</li>
                <li><strong>“This is the APKMirror Installer app”:</strong> you downloaded the helper app. Go back to the Android Auto release page and use <strong>Download APK Bundle</strong> there.</li>
                <li><strong>“Not Android Auto” or an unverified file:</strong> make sure the app is <strong>Android Auto</strong> by <strong>Google LLC</strong>, and that the variant name ends in <strong>‑release</strong> (not beta or alpha).</li>
                <li><strong>Certificate expired:</strong> that release is too old. Download again in step 1; it always gets the newest release.</li>
                <li><strong>The file won’t pick or was rejected:</strong> don’t unzip it. Use the .apk, .xapk or .apkm file exactly as downloaded. A .zip name from Safari is fine too.</li>
                <li><strong>Upload is slow or stops:</strong> stay on this page and keep the comma awake and on the same Wi-Fi until it finishes.</li>
              </ul>
              <ul>
                <li><strong>Why is this needed?</strong> Car screens only talk to devices that present Google’s Android Auto certificate. Your comma reads it from your own copy of the app, on the device. No Android app is installed on the comma.</li>
              </ul>
            </details>
          </div>
        </section>
      <ol v-if="setup" id="gx-starport-steps" class="gx-starport__steps">
        <li v-show="!setupDone || showSteps" class="gx-card gx-starport-step" :class="{ 'gx-starport-step--done': !!selected && !pairing?.active }">
          <div class="gx-starport-step__head">
            <span class="gx-starport-step__num" aria-hidden="true"><i v-if="selected && !pairing?.active" class="bi bi-check-lg"></i><template v-else>2</template></span>
            <div><h3>{{ carType === 'usb' ? 'Set Up Wired AA' : carType === 'app' ? 'Set Up Your App' : adapterSetup ? 'Pair Your Adapter' : 'Pair Your Car' }}</h3><p>{{ adapterSetup ? 'Put the adapter in pairing mode, then tap Find adapter. Your car doesn’t need to be paired.' : pairsHere ? 'Open your car’s phone pairing screen, then tap Find car. You can do this before step 1 is finished.' : 'No Bluetooth pairing is needed for this setup.' }}</p></div>
          </div>
          <div class="gx-starport-step__body">
            <button v-if="setupDone && showSteps" class="gx-btn gx-btn--tonal" @click="showSteps = false">Hide Setup</button>
            <GalaxySettingRow :row="carTypeRow" :index="0" :busy="busy" :disabled="appDisabled" :save-value="saveCarType" />
            <GxNotice v-if="wiredUnavailable" tone="warn">Wired AA is unavailable: {{ runtime.wired_blocked }}.</GxNotice>
            <GalaxySettingRow v-if="carType === 'app'" :row="appRow" :index="0" :busy="busy" :disabled="appDisabled" :save-value="saveApp" />
            <GalaxySettingRow v-if="adapterSetup" :row="adapterRow" :index="0" :busy="busy" :disabled="appDisabled" :save-value="saveAdapter" />
            ${AUTO_CONNECT}
            <template v-if="carType === 'usb'">
              ${WIRED_STATUS}
            </template>
            <template v-else-if="carType === 'app'">
              ${APP_SETUP}
            </template>
            <template v-if="pairsHere">
            <ol v-if="adapterSetup" class="gx-starport-list" aria-label="Adapter pairing">
              <li class="gx-starport-row">
                <i class="bi bi-usb-symbol" aria-hidden="true"></i>
                <span class="gx-starport-row__text"><strong>Adapter</strong><small>{{ selected ? selected.name : 'Put it in pairing mode, then tap Find adapter.' }}</small></span>
                <span class="gx-starport-pill" :class="selected ? 'gx-starport-pill--ready' : ''">{{ selected ? 'Paired' : 'Not paired' }}</span>
              </li>
            </ol>
            <GxNotice v-if="!pairing?.active && runtime?.pairing_note" tone="warn">{{ runtime.pairing_note }}</GxNotice>
            <ul v-if="!pairing?.active && receivers.length" class="gx-starport-list" aria-label="Paired cars">
              <li v-for="car in receivers" :key="car.address" class="gx-starport-row">
                <i class="bi bi-car-front" aria-hidden="true"></i>
                <span class="gx-starport-row__text"><strong>{{ car.name }}</strong><small>{{ selected?.address === car.address ? 'Used for Android Auto' : 'Paired' }}</small></span>
                <span v-if="selected?.address === car.address" class="gx-starport-pill gx-starport-pill--ready"><i class="bi bi-check-lg"></i> Selected</span>
                <button v-else class="gx-btn gx-btn--tonal" :disabled="!setup.parked || linked" @click="useCar(car.address)">Use</button>
              </li>
            </ul>
            <p v-else-if="!pairing?.active && selected" class="gx-starport-row"><i class="bi bi-car-front" aria-hidden="true"></i><span class="gx-starport-row__text"><strong>{{ selected.name }}</strong><small>Used for Android Auto</small></span></p>
            <p v-if="!pairing?.active && endReason === 'canceled'" class="gx-note" role="status">Pairing canceled. Start again when your car is ready.</p>
            <p v-else-if="!pairing?.active && endReason" class="gx-note" role="status">The pairing window ended without choosing a car. Try again when your car’s pairing screen is open.</p>
            <ol v-if="showPairSteps" class="gx-starport-list" aria-label="Pairing progress">
              <li v-for="step in pairSteps" :key="step.label" class="gx-starport-row" :class="'gx-starport-row--' + step.status" :aria-current="step.status === 'active' ? 'step' : undefined">
                <i v-if="step.status === 'done'" class="bi bi-check-circle-fill gx-starport-ok" aria-hidden="true"></i>
                <span v-else-if="step.status === 'active'" class="gx-spinner gx-starport-orbit gx-starport-orbit--small" aria-hidden="true"></span>
                <i v-else class="bi bi-circle" aria-hidden="true"></i>
                <span class="gx-starport-row__text"><strong>{{ step.label }}</strong></span>
              </li>
            </ol>
            <p v-if="waitingForCar" role="status">{{ pairing.discovering ? "Searching for nearby cars and adapters…" : "Choose your car or wireless adapter below." }}</p>
            <p v-else-if="pairing?.active && pairing.approved && (!pairing.state || pairing.state === 'idle')" role="status">Approved {{ pairing.receiver?.name }}. Finishing Bluetooth pairing…</p>
            <p v-if="pairing?.state === 'pairing' && !pairing.prompt && !pairing.approved" role="status">Pairing with {{ pairing.receiver?.name || 'the selected device' }}…</p>
            <p v-if="pairing?.state === 'connecting'" role="status">Connecting to {{ pairing.receiver?.name || 'the selected device' }}…</p>
            <p v-if="['paired', 'connected'].includes(pairing?.state)" role="status"><i class="bi bi-check-circle-fill gx-starport-ok"></i> {{ pairing.receiver?.name || 'Device' }} {{ pairing.state === 'connected' ? 'connected' : 'paired' }}.</p>
            <GxNotice tone="danger" v-if="pairing?.error">{{ pairing.error }}</GxNotice>
            <ul v-if="pairing?.active" class="gx-starport-list" aria-label="Nearby devices">
              <li v-if="!pairing.devices?.length && waitingForCar" class="gx-starport-row gx-note" role="status"><span class="gx-spinner" aria-hidden="true"></span>Nothing found yet. Keep your car’s pairing screen open.</li>
              <li v-for="device in pairing.devices || []" :key="device.address" class="gx-starport-row">
                <i :class="device.starport ? 'bi bi-car-front' : 'bi bi-bluetooth'" aria-hidden="true"></i>
                <span class="gx-starport-row__text"><strong>{{ device.name }}</strong><small>{{ device.starport ? 'StarPort ready' : 'Bluetooth device' }} · {{ device.connected ? 'Connected' : device.paired ? 'Paired before' : 'Nearby' }}</small></span>
                <button class="gx-btn gx-btn--tonal" :disabled="deviceSelectionBlocked" @click="selectDevice(device.address)">{{ device.paired ? 'Connect' : 'Pair' }}</button>
              </li>
            </ul>
            <div v-if="pairing?.prompt" class="gx-starport-prompt" role="status"><strong>{{ pairing.receiver.name }}</strong>
              <p>{{ pairing.prompt.kind === 'confirmation' ? 'Check that this code matches your car, then confirm.' :
                pairing.prompt.kind === 'pin' ? 'Enter the PIN shown by your car.' :
                pairing.prompt.kind === 'passkey' ? 'Enter your car’s passkey.' :
                pairing.prompt.kind === 'authorization' ? 'Allow this car to pair?' : 'Enter this code in your car.' }}</p>
              <strong v-if="pairing.prompt.value" class="gx-starport-prompt__code">{{ pairing.prompt.value }}</strong>
              <input v-if="['pin','passkey'].includes(pairing.prompt.kind)" class="gx-field" type="text" maxlength="16" autocomplete="off"
                :inputmode="pairing.prompt.kind === 'passkey' ? 'numeric' : 'text'" :aria-label="pairing.prompt.kind === 'pin' ? 'Car PIN' : 'Car passkey'" v-model="pairValue" />
              <div v-if="!pairing.prompt.displayOnly" class="gx-driving__actions gx-actions">
                <button class="gx-btn gx-btn--tonal" @click="respond(false)">Reject</button>
                <button class="gx-btn" :disabled="!canRespond" @click="respond(true)">{{ pairing.prompt.kind === 'pin' ? 'Send PIN' : pairing.prompt.kind === 'passkey' ? 'Send Passkey' : 'Confirm' }}</button></div>
            </div>
            <p v-if="pairWhenReady" class="gx-note" role="status"><span class="gx-spinner" aria-hidden="true"></span> Turning on Android Auto, then searching…</p>
            <p v-else-if="!pairing?.active && pairingReason" class="gx-note" role="status">{{ pairingReason }}</p>
            <div class="gx-driving__actions gx-actions">
              <button v-if="!pairing?.active" class="gx-btn" :class="{ 'gx-btn--tonal': !!selected }" :disabled="pairWhenReady || !!pairingReason" @click="beginPairing"><span v-if="starting" class="gx-spinner" aria-hidden="true"></span><i v-else class="bi bi-search"></i> {{ starting ? 'Starting search…' : selected ? 'Pair another ' + findLabel : 'Find ' + findLabel }}</button>
              <button v-else class="gx-btn gx-btn--tonal" @click="cancelPairing">Cancel Search / Pairing</button>
            </div>
            <details class="gx-starport-wiki"><summary>Stuck pairing? Pairing order and wireless adapters</summary>
              <ul>
                <li><strong>Keep this page open while pairing</strong> so the car’s PIN or code doesn’t time out.</li>
                <li><strong>Cars with built-in wireless Android Auto:</strong> open the car’s phone pairing screen, then tap <strong>Find car</strong> above. Choose your car, then confirm the code on both screens.</li>
                <li><strong>Wireless adapter</strong> (AAWireless or Motorola MA1): choose Wireless adapter above, put the adapter in pairing mode and pair it here Your car doesn’t need to be paired. Adapter names often look like AndroidAuto-XXXX.</li>
              </ul>
              <p><strong>Start fresh:</strong></p>
              <ol>
                <li>Forget the car on the comma: Bluetooth under Tools → Forget.</li>
                <li>Delete the comma from the car’s Bluetooth list.</li>
                <li>Turn the car off and on (or unplug the adapter for 5 seconds).</li>
                <li>Pair again here.</li>
              </ol>
              <a class="gx-btn gx-btn--tonal" href="#/bluetooth">Open Bluetooth</a>
            </details>
            </template>
            ${CONNECTION_PROGRESS}
            ${CONNECT}
            <p v-if="connectReason" class="gx-note" role="status">{{ connectReason }}</p>
            <p v-else-if="!setup.identity.installed" class="gx-note">Finish step 1 first — Connect will walk you through it.</p>
            <details class="gx-starport-wiki"><summary>Stuck connecting? Wi-Fi and screen troubleshooting</summary>
              <ul>
                <li v-if="!wired && !app"><strong>How it connects:</strong> after Bluetooth, the car (or adapter) creates its own hidden 5 GHz Wi-Fi network and tells the comma how to join. The picture streams over that Wi-Fi.</li>
                <li><strong>First connection:</strong> stay in Park or offroad mode, and keep the car’s screen on.</li>
                <li><strong>Black or frozen screen:</strong> tap Disconnect, wait a few seconds, then connect again. If it keeps happening, turn the car off and on.</li>
                <li><strong>Car says the phone isn’t compatible:</strong> make sure wireless Android Auto (sometimes called “smartphone connection”) is turned on in the car’s settings, and that step 1 shows Ready.</li>
                <li><strong>Wired AA:</strong> turn on Wired AA in step 2, plug the car’s data USB port into the comma’s USB-C port with a data-capable cable, and drive. Bluetooth pairing is not needed.</li>
              </ul>
            </details>
          </div>
        </li>

      </ol>
      <section v-if="setup && setupDone && !showSteps" class="gx-card gx-starport-step gx-starport-home" aria-labelledby="gx-starport-home-title">
        <div class="gx-starport-step__head">
          <span class="gx-starport-step__num" :class="{ 'gx-starport-home__icon--live': projecting }" aria-hidden="true"><i class="bi bi-car-front"></i></span>
          <div><h3 id="gx-starport-home-title">{{ homeTitle }}</h3>
            <p role="status">{{ runtime?.running && runtime.state !== 'streaming' ? connectionProgress.title : projecting ? 'Showing StarPilot on your car’s screen.' : wired ? wiredStatus.label : runtime?.auto_connect ? 'Connects automatically when you start driving.' : 'Ready to connect.' }}</p></div>
        </div>
        <div class="gx-starport-step__body">
          <p v-if="expiry.level === 'soon'" class="gx-starport-pill gx-starport-pill--soon" role="status">{{ expiry.label }}</p>
          <GalaxySettingRow :row="carTypeRow" :index="0" :busy="busy" :disabled="appDisabled" :save-value="saveCarType" />
          <GalaxySettingRow v-if="adapterSetup" :row="adapterRow" :index="0" :busy="busy" :disabled="appDisabled" :save-value="saveAdapter" />
          <GalaxySettingRow v-if="app" :row="appRow" :index="0" :busy="busy" :disabled="appDisabled" :save-value="saveApp" />
          ${AUTO_CONNECT}
          <ul v-if="wirelessHome" class="gx-starport-list" aria-label="Setup steps">
            <li class="gx-starport-row"><i class="bi bi-shield-check" aria-hidden="true"></i>
              <span class="gx-starport-row__text"><strong>1. Android Auto support</strong><small>{{ expiry.label }}</small></span>
              <span class="gx-starport-pill" :class="supportReady ? 'gx-starport-pill--ready' : ''">{{ supportReady ? 'Ready' : 'Not ready' }}</span></li>
            <li class="gx-starport-row"><i class="bi bi-bluetooth" aria-hidden="true"></i>
              <span class="gx-starport-row__text"><strong>2. {{ adapterSetup ? 'Adapter paired' : 'Car paired' }}</strong><small>{{ selected ? selected.name : 'Not paired yet.' }}</small></span>
              <span class="gx-starport-pill" :class="selected ? 'gx-starport-pill--ready' : ''">{{ selected ? 'Paired' : 'Not paired' }}</span></li>
            <li class="gx-starport-row"><i class="bi bi-car-front" aria-hidden="true"></i>
              <span class="gx-starport-row__text"><strong>3. Connect</strong><small>{{ projecting ? 'Showing on your car’s screen.' : runtime?.running ? connectionProgress.title : 'Tap Connect below.' }}</small></span>
              <span class="gx-starport-pill" :class="projecting ? 'gx-starport-pill--ready' : ''">{{ projecting ? 'Connected' : runtime?.running ? 'In progress' : 'Waiting' }}</span></li>
          </ul>
          ${WIRED_STATUS}
          ${APP_SETUP}
          ${CONNECTION_PROGRESS}
          ${CONNECT}
          <div class="gx-actions">
            <a class="gx-btn gx-btn--tonal" href="#/theme_maker/starport"><i class="bi bi-layout-text-window" aria-hidden="true"></i> Customize Layout</a>
            <button type="button" class="gx-btn gx-btn--tonal" :aria-expanded="showSteps" aria-controls="gx-starport-steps" @click="showSteps = !showSteps">
              <i :class="showSteps ? 'bi bi-chevron-up' : 'bi bi-gear'" aria-hidden="true"></i> {{ showSteps ? 'Hide Setup' : 'Setup and Pairing' }}</button>
          </div>
        </div>
      </section>
      <ol v-if="setup" class="gx-starport__steps">
        <li v-show="!setupDone || showSteps" class="gx-card gx-starport-step">
          <div class="gx-starport-step__head">
            <span class="gx-starport-step__num" aria-hidden="true">3</span>
            <div><h3>Customize Your Dashboard</h3><p>Once connected, customize your dashboard setup.</p></div>
          </div>
          <div class="gx-starport-step__body">
            <a class="gx-btn gx-btn--tonal gx-starport-cta" href="#/starport/audio"><i class="bi bi-spotify" aria-hidden="true"></i> Spotify and Phone Audio</a>
            <a class="gx-btn gx-btn--tonal gx-starport-cta" href="#/theme_maker/starport">Customize Layout</a>
            <a class="gx-btn gx-btn--tonal gx-starport-cta" href="#/navigation/maps"><i class="bi bi-map" aria-hidden="true"></i> Offline Road Maps</a>
            <p class="gx-note">Save road lines for the map overlay so it keeps drawing roads with no signal.</p>
          </div>
        </li>
      </ol>

      <section v-if="setup" class="gx-card gx-starport-step" aria-labelledby="gx-starport-help-title">
        <div class="gx-starport-step__head">
          <span class="gx-starport-step__num" aria-hidden="true"><i class="bi bi-question-circle"></i></span>
          <div><h3 id="gx-starport-help-title">Still having trouble?</h3><p>Our wiki has detailed help for pairing, connection status, other phones, wireless adapters, and screen problems.</p></div>
        </div>
        <div class="gx-starport-step__body">
          <div class="gx-driving__actions gx-actions">
            <a class="gx-btn" href="https://wiki.firestar.link/exclusive/android-auto/" target="_blank" rel="noopener noreferrer">Read the Android Auto troubleshooting guide</a>
            <a class="gx-btn gx-btn--tonal" href="#/logs/starport"><i class="bi bi-journal-text"></i> Connection Logs</a>
          </div>
        </div>
      </section>

      <GxDialog v-if="enableOpen && setup" :inert="busy" labelledby="gx-starport-enable-title" describedby="gx-starport-enable-body" alert @close="cancelEnable">
          <div><h3 id="gx-starport-enable-title">Enable Bluetooth?</h3>
            <p id="gx-starport-enable-body">Android Auto requires Bluetooth for setup and connecting to your car. Bluetooth is currently off. Enable Bluetooth and turn on Android Auto?</p></div>
          <div class="gx-settings__controls gx-actions">
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="cancelEnable">Cancel</button>
            <button type="button" class="gx-btn" :disabled="busy" @click="confirmEnable">{{ busy ? 'Enabling…' : 'Enable Bluetooth and Turn On' }}</button>
          </div>
      </GxDialog>

      <GxDialog v-if="removeOpen && setup" :inert="busy" labelledby="gx-starport-remove-title" describedby="gx-starport-remove-body" alert @close="closeRemove">
          <div><h3 id="gx-starport-remove-title">Remove StarPort support?</h3>
            <p id="gx-starport-remove-body">This deletes the Android Auto certificate and key from this comma. StarPort will disconnect first if connected. Your comma won’t connect to your car until you set it up again.</p></div>
          <div class="gx-settings__controls gx-actions">
            <button type="button" class="gx-btn gx-btn--tonal" @click="closeRemove">Cancel</button>
            <button type="button" class="gx-btn gx-btn--danger" @click="removePackage">Remove</button>
          </div>
      </GxDialog>

      <GxDialog v-if="installOpen && setup" labelledby="gx-starport-install-title" class="gx-starport-sheet__dialog" @close="closeInstall">
        <div class="gx-starport-sheet__toolbar">
          <button type="button" class="gx-icon-btn" aria-label="Close" @click="closeInstall"><i class="bi bi-x-lg"></i></button>
        </div>
        <div class="gx-starport-sheet__body">
          <div v-if="bluetooth"><h3 id="gx-starport-install-title">Android Auto package installation</h3>
            <BluetoothNotice feature="Android Auto package downloads and uploads" /></div>
          <div v-else-if="installState === 'done'" class="gx-message gx-starport-sheet__done">
            <div class="gx-starport-done-badge"><i class="bi bi-check-lg" aria-hidden="true"></i></div>
            <h3 id="gx-starport-install-title">You’re all set</h3>
            <p class="gx-note">{{ expiry.label }}</p>
            <p v-if="!setup.enabled" class="gx-note">Turn on StarPort in step 1 to connect to your car.</p>
            <ul class="gx-starport-checks"><li v-for="check in checks" :key="check.label" :class="'gx-starport-check--' + check.status">{{ check.label }}</li></ul>
            <button class="gx-btn gx-starport-cta" @click="closeInstall">Done</button>
          </div>
          <div v-else class="gx-stack">
            <div class="gx-stack gx-starport-sheet__head">
              <div class="gx-cluster gx-starport-sheet__lead">
                <div>
                  <h3 id="gx-starport-install-title">{{ setup.identity.installed ? 'Install a Newer Version' : 'Set Up StarPort' }}</h3>
                  <p v-if="setup.identity.installed" class="gx-note"><template v-if="installedVersion">Current version: {{ installedVersion }} · </template>{{ expiry.label }}. Your paired car and settings stay as they are.</p>
                  <p v-else class="gx-note">One-time setup so your car accepts your comma.</p>
                </div>
              </div>
            </div>
            <ol class="gx-starport-sheet__steps">
              <li class="gx-starport-step-card" :class="{ 'gx-starport-sheet__step--muted': installState !== 'choose' }">
                <div class="gx-starport-step-card__head">
                  <span class="gx-starport-step__num gx-starport-step-card__num">1</span>
                  <div class="gx-starport-step-card__title">
                    <strong>Download Android Auto</strong>
                    <small>{{ download.direct ? 'Saves the app to this ' + (onPhone ? 'phone' : 'computer') : download.pending ? 'Looking up the newest release…' : 'Get the newest release from APKMirror' }}<template v-if="installedVersion"> · You have {{ installedVersion }}</template></small>
                  </div>
                </div>
                <div class="gx-starport-step-card__body">
                  <a v-if="download.direct" class="gx-btn gx-btn--tonal" :href="download.url" rel="noreferrer" download @click="markDownloadStarted">
                    <i class="bi bi-download" aria-hidden="true"></i> Download Android Auto {{ download.version }} · {{ download.megabytes }} MB
                  </a>
                  <button v-else-if="download.pending" class="gx-btn gx-btn--tonal" type="button" disabled>
                    <i class="bi bi-hourglass-split" aria-hidden="true"></i> Finding the newest Android Auto…
                  </button>
                  <a v-else class="gx-btn gx-btn--tonal" :href="download.url" target="_blank" rel="noopener noreferrer" @click="markDownloadStarted">
                    <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open APKMirror
                  </a>
                  <ol v-if="!download.direct && !download.pending" class="gx-note gx-starport-mirror-steps">
                    <li>Open <strong>Android Auto</strong> on APKMirror, then open the <strong>All versions</strong> list and click through the versions to compare their release dates.</li>
                    <li>Open the version with the <strong>newest release date</strong>.</li>
                    <li>Scroll down to <strong>Available downloads</strong>. Under <strong>Variant</strong>, click the <strong>17.x.xxxx‑release</strong> or <strong>18.x.xxxx‑release</strong> entry.</li>
                    <li>Click <strong>Download APK Bundle</strong> (or <strong>Download APK</strong>). You get an <strong>.apk</strong>, <strong>.xapk</strong> or <strong>.apkm</strong> file. Leave it zipped.</li>
                  </ol>
                  <small v-if="!download.direct && !download.pending" class="gx-note">You don’t need the APKMirror Installer app.</small>
                  <small v-if="download.direct" class="gx-note">From APKPure, a third-party mirror (<a class="gx-link" href="https://apkpure.com/terms.html" target="_blank" rel="noopener noreferrer">terms</a>). Don’t unzip it.</small>
                </div>
              </li>
              <li class="gx-starport-step-card" :class="{ 'gx-starport-sheet__step--muted': installState !== 'choose' }">
                <div class="gx-starport-step-card__head">
                  <span class="gx-starport-step__num gx-starport-step-card__num">2</span>
                  <div class="gx-starport-step-card__title">
                    <strong>Choose the downloaded file</strong>
                    <small>{{ downloadStarted && downloadsHint ? downloadsHint : 'The file you just downloaded' }}</small>
                  </div>
                </div>
                <div class="gx-starport-step-card__body">
                  <label class="gx-btn gx-btn--tonal gx-starport-file" :class="{ 'gx-starport-file--disabled': installState !== 'choose' }">
                    <input type="file" :accept="onPhone ? undefined : '.apk,.xapk,.apkm,.zip'" aria-label="Android Auto APK, XAPK, APKM, or ZIP" :disabled="installState !== 'choose'" @change="choosePackage" />
                    <i class="bi bi-folder2-open"></i> {{ packageFile ? 'Choose a Different File' : 'Choose File' }}
                  </label>
                  <small v-if="packageChecking" role="status" class="gx-note">Checking downloaded file…</small>
                  <div v-if="packageFile && !packageError && !packageChecking" class="gx-chip gx-starport-file-chip">
                    <i class="bi bi-file-earmark-zip"></i>
                    <span>{{ packageFile.name }}</span>
                    <small>{{ Math.ceil(packageFile.size / 1048576) }} MB</small>
                  </div>
                  <small v-if="packageError" class="gx-starport-bad" role="alert">{{ packageError }}</small>
                </div>
              </li>
              <li class="gx-starport-step-card">
                <div class="gx-starport-step-card__head">
                  <span class="gx-starport-step__num gx-starport-step-card__num">3</span>
                  <div class="gx-starport-step-card__title">
                    <strong>Install</strong>
                    <small>Send it to your comma and check the certificate</small>
                  </div>
                </div>
                <div class="gx-starport-step-card__body">
                  <button v-if="installState === 'choose'" class="gx-btn gx-starport-cta" :disabled="!!uploadReason" @click="upload">
                    {{ setup.enabled ? 'Install' : 'Turn On and Install' }}
                  </button>
                  <small v-if="installState === 'choose' && uploadReason && packageFile && !packageError" class="gx-note" role="status">{{ uploadReason }}</small>
                  <div v-if="installState === 'uploading'" role="status" aria-live="polite" class="gx-stack">
                    <div class="gx-update-progress__track" role="progressbar" aria-label="Sending to your comma" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="uploadPercent">
                      <div class="gx-update-progress__fill" :style="{ width: uploadPercent + '%' }"></div>
                    </div>
                    <small>Sending to your comma… {{ uploadPercent }}%</small>
                  </div>
                  <ul v-if="['checking', 'failed'].includes(installState)" class="gx-starport-checks" aria-live="polite">
                    <li v-for="check in checks" :key="check.label" :class="'gx-starport-check--' + check.status">{{ check.label }}</li>
                  </ul>
                  <GxNotice v-if="installState === 'failed'" tone="danger">
                    <p>{{ problem }}</p>
                    <template #actions><button class="gx-btn gx-btn--tonal" @click="chooseAgain">Choose Another File</button></template>
                  </GxNotice>
                  <GxNotice v-if="error && installState === 'choose' && installAttempted" tone="danger">{{ error }}</GxNotice>
                </div>
              </li>
            </ol>
            <div class="gx-stack gx-starport-sheet__footer">
              <small class="gx-note gx-starport-trademark">Google and Android Auto are trademarks of Google LLC. StarPilot is not affiliated with or endorsed by Google or APKPure.</small>
              <small class="gx-note">The uploaded file is deleted after import. Maximum size {{ Math.floor(setup.maxUploadBytes / 1048576) }} MB.</small>
            </div>
          </div>
        </div>
      </GxDialog>
    </section>`,
}
