import { GxDialog } from "./dialog.js"
import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { connectionError } from "./polling.js"
import { uploadPackage } from "./starport.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"

const ADDRESS = /^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$/
export const DOWNLOAD_PAGE = "https://developer.spotify.com/documentation/soloist/reference/downloads-and-updates"
const STAGES = ["reading", "unpacking", "verifying", "testing", "installing", "done"]
const CHECKS = [
  { stage: "reading", label: "Received the download", codes: ["TOO_LARGE"] },
  { stage: "unpacking", label: "Unpacked safely", codes: ["NOT_ARCHIVE", "CORRUPT", "UNSAFE", "NO_PLAYER", "WRONG_ARCH"] },
  { stage: "verifying", label: "Matches Spotify’s official download", codes: ["NOT_OFFICIAL", "OFFLINE"] },
  { stage: "testing", label: "Runs on this comma", codes: ["INCOMPATIBLE", "EXPIRED"] },
  { stage: "installing", label: "Installed", codes: ["DRIVING"] },
]

function validSetup(value) {
  return value && typeof value.enabled === "boolean" && typeof value.parked === "boolean" && typeof value.keyPresent === "boolean" &&
    value.release && typeof value.release.installed === "boolean" && value.preferences && Array.isArray(value.devices) &&
    value.import && Number.isSafeInteger(value.maxUploadBytes) && value.maxUploadBytes > 0 && value.links
}

// Name and size checks that catch the usual wrong downloads before sending anything.
export function archivePreflight(file) {
  const name = typeof file?.name === "string" ? file.name : ""
  if (/arm32|armv7|x86_64|amd64/i.test(name)) return "That’s the download for a different computer. Choose ARMv8/AArch64 Linux (soloist_release_arm64.tar.gz)."
  if (/\.tar$/i.test(name)) return "Your browser unpacked the download. Download it again and keep the .tar.gz file as it is."
  if (/^soloist$/i.test(name)) return "That’s the unpacked player. Upload the .tar.gz file exactly as it downloaded."
  if (name && !/\.(tar\.gz|tgz|gz)$/i.test(name)) return "Choose the soloist_release_arm64.tar.gz file you downloaded."
  return ""
}

export function importProblem(job) {
  if (job?.state !== "failed") return ""
  switch (job.code) {
    case "NOT_ARCHIVE": return "That isn’t the Soloist download. Choose soloist_release_arm64.tar.gz."
    case "CORRUPT": return "This file looks incomplete or damaged. Download it again and wait for it to finish."
    case "WRONG_ARCH": return "That’s the download for a different computer. On Spotify’s page, choose ARMv8/AArch64 Linux."
    case "NO_PLAYER": case "UNSAFE": return "This archive isn’t a Soloist release. Download it again from Spotify’s page."
    case "NOT_OFFICIAL": return "This file doesn’t match the build Spotify is offering right now. Spotify may have just released a new one; download it again and upload the new file."
    case "OFFLINE": return "Your comma needs an internet connection to check this download with Spotify. Connect it to Wi‑Fi or cellular and try again."
    case "EXPIRED": return "This Soloist build has expired. Download the newest one from Spotify."
    case "INCOMPATIBLE": return job.error || "This Soloist build doesn’t run on this comma yet."
    case "DRIVING": return "Put the car in Park or use offroad mode, then install again."
    case "TOO_LARGE": return "This file is too large to be the Soloist download."
    case "CANCELLED": return "Setup authorization changed. Sign in to Galaxy and try again."
    default: return job.error || "Something went wrong. Try again."
  }
}

export function installChecks(job) {
  const at = job?.state === "done" ? STAGES.length : STAGES.indexOf(job?.stage)
  const ends = CHECKS.map((_, index) => STAGES.indexOf(index + 1 < CHECKS.length ? CHECKS[index + 1].stage : "done"))
  let failed = -1
  if (job?.state === "failed") {
    failed = CHECKS.findIndex((check) => check.codes.includes(job.code))
    if (failed < 0) failed = Math.max(0, ends.findIndex((end) => at < end))
  }
  return CHECKS.map((check, index) => ({
    label: check.label,
    status: failed >= 0 ? (index < failed ? "done" : index === failed ? "failed" : "pending") :
      at >= ends[index] ? "done" : at >= STAGES.indexOf(check.stage) ? "active" : "pending",
  }))
}

export function releaseInfo(release, locale = undefined) {
  if (!release?.installed) return { level: "none", label: "Not installed" }
  const date = release.expires ? new Date(release.expires) : null
  const on = date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(locale, { month: "short", day: "numeric", year: "numeric" }) : ""
  const version = release.version ? `Version ${release.version}` : "Installed"
  if (release.expired) return { level: "expired", label: `${version} · expired${on ? ` on ${on}` : ""}` }
  if (release.level === "soon") {
    const left = release.daysLeft === 0 ? "expires within a day" : `expires in ${release.daysLeft} day${release.daysLeft === 1 ? "" : "s"}`
    return { level: "soon", label: `${version} · ${left}${on ? ` (${on})` : ""}` }
  }
  return { level: "ok", label: `${version} · ${on ? `expires on ${on}` : "expiry date unavailable"}` }
}

const STATE_TEXT = {
  missing_package: "Install Spotify Soloist to finish setup.",
  missing_key: "Add your API key to finish setup.",
  no_output: "Choose where music plays.",
  audio_unavailable: "This StarPilot build is missing Spotify’s audio component.",
  waiting_output: "Waiting for your speaker to connect.",
  starting: "Starting…",
  connecting: "Starting…",
  waiting_network: "Waiting for the comma’s network…",
  waiting: "Signing in to Spotify…",
  ready: "Ready. Pick this device in Spotify to play.",
  playing: "Playing",
  retrying: "Restarting…",
  failed: "Spotify couldn’t start.",
  expired: "This Soloist build expired. Install the newest one.",
}

export function serviceLabel(setup) {
  if (!setup?.enabled) return "Off"
  const service = setup.service
  if (!service) return "Starting…"
  const text = service.state === "ready" && service.player?.status === "paused" ? "Paused" :
    service.message && !["ready", "playing", "waiting"].includes(service.state) ? service.message : STATE_TEXT[service.state] || "Starting…"
  return service.outputWaiting ? `${text} Audio waits for ${service.outputWaiting}.` : text
}

// Where the track is now, from Soloist's anchor and the browser time elapsed since it.
export function positionNow(position, durationMs, now = Date.now()) {
  if (!position) return 0
  const value = position.ms + Math.max(0, now - position.at) * position.speed
  return Math.max(0, Math.min(durationMs ?? value, value))
}

// Moves the play anchor onto the browser's clock, since the comma's and the browser's can differ.
export function onBrowserClock(setup, now = Date.now()) {
  const position = setup?.service?.player?.position
  if (!position || !Number.isFinite(setup.clock)) return setup
  const player = { ...setup.service.player, position: { ...position, at: position.at + now - setup.clock } }
  return { ...setup, service: { ...setup.service, player } }
}

export function formatTime(ms) {
  const seconds = Math.floor((ms || 0) / 1000)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

export function muteLabel(seconds) {
  const ms = Math.round((Number(seconds) || 0) * 1000)
  return ms === 0 ? "Off" : `${ms} ms`
}

export class SpotifyFeed {
  constructor({ publish, unauthorized = () => { }, fetcher = (...args) => fetch(...args), uploader = uploadPackage,
    later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id),
    overBluetooth = () => globalThis.StarPilotApp?.bluetooth?.() === true }) {
    Object.assign(this, { publish, unauthorized, fetcher, uploader, later, cancelTimer, overBluetooth })
    this.active = false
    this.generation = 0
    this.pollTimer = null
    this.setup = null
    this.busy = false
    this.uploadProgress = null
    this.error = ""
  }

  emit() { this.publish({ setup: this.setup, busy: this.busy, uploadProgress: this.uploadProgress, error: this.error }) }

  start() { this.stop(); this.active = true; return this.refresh() }

  stop() {
    this.active = false
    this.generation++
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.pollTimer = null
    this.busy = false
    this.uploadProgress = null
    this.emit()
  }

  schedule() {
    if (!this.active) return
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    const fast = this.setup?.import?.state === "running" || this.setup?.service?.player?.connected
    // The phone Bluetooth link is slow, so poll it a lot less.
    const delay = this.overBluetooth() ? (fast ? 5000 : 15000) : (fast ? 1500 : 4000)
    this.pollTimer = this.later(() => { this.pollTimer = null; this.refresh() }, delay)
  }

  async request(path, options = {}, timeout = 8000, transport = this.fetcher) {
    const generation = this.generation
    try {
      const { response, data } = await requestJson(path, {
        fetcher: transport, timeout, later: this.later, cancel: this.cancelTimer, request: options, withResponse: true
      })
      if (!this.active || this.generation !== generation) return null
      if (response.status === 401) { this.stop(); this.unauthorized(); return null }
      if (!response.ok) throw new Error(data?.error || "Spotify setup is unavailable")
      return data
    } catch (error) {
      if (this.active && this.generation === generation) {
        this.error = error.name === "TimeoutError" ? "Your comma took too long to respond. Retrying…" : connectionError(error)
        this.emit()
      }
      return null
    }
  }

  async refresh() {
    if (!this.active) return
    const setup = await this.request("./api/spotify/setup")
    if (setup && validSetup(setup)) { this.setup = onBrowserClock(setup); if (!this.busy) this.error = "" }
    else if (setup) this.error = "Spotify setup response changed"
    this.emit()
    this.schedule()
  }

  async post(path, body, timeout = 10000) {
    if (!this.active || this.busy) return null
    this.busy = true
    this.error = ""
    this.emit()
    const result = await this.request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, timeout)
    this.busy = false
    if (result && validSetup(result)) this.setup = onBrowserClock(result)
    this.emit()
    if (result) this.refresh()
    return result
  }

  // Playback controls stay outside the busy gate so a slow command never blocks the next tap.
  async control(action, args = undefined) {
    if (!this.active) return null
    const result = await this.request("./api/spotify/control", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args === undefined ? { action } : { action, args })
    }, 5000)
    if (result) this.later(() => this.refresh(), 400)
    return result
  }

  async upload(file) {
    if (!this.active || this.busy || !this.setup || this.setup.uploadAllowed === false || this.setup.import?.state === "running" ||
      !file || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > this.setup.maxUploadBytes) return false
    this.busy = true
    this.uploadProgress = { loaded: 0, total: file.size }
    this.emit()
    const generation = this.generation
    const transport = (path, options) => this.uploader(path, options, (progress) => {
      if (this.active && generation === this.generation) { this.uploadProgress = progress; this.emit() }
    })
    const result = await this.request("./api/spotify/upload", {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file
    }, 180000, transport)
    this.busy = false
    this.uploadProgress = null
    if (result && validSetup(result)) this.setup = onBrowserClock(result)
    this.emit()
    this.schedule()
    return result !== null
  }

  async removeAll() {
    if (!this.active || this.busy) return false
    this.busy = true
    this.emit()
    const result = await this.request("./api/spotify", { method: "DELETE" }, 30000)
    this.busy = false
    if (result && validSetup(result)) this.setup = onBrowserClock(result)
    this.emit()
    return result !== null
  }
}

export const SpotifyPage = {
  components: { GxNotice, GxDialog, GalaxySettingRow },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({
    setup: null, busy: false, error: "", uploadProgress: null, now: Date.now(),
    installOpen: false, removeOpen: false, resetOpen: false, showSetup: false, downloadStarted: false,
    archiveFile: null, archiveMagicError: "", archiveChecking: false, archiveSelection: 0, installAttempted: false, installBaseline: null,
    keyValue: "", keyEditing: false, nameValue: "", pauseDraft: null, skipDraft: null,
  }),
  mounted() {
    this.feed = new SpotifyFeed({ publish: (value) => Object.assign(this, value, { now: Date.now() }), unauthorized: this.unauthorized })
    this.visibility = () => { if (document.hidden) this.feed.stop(); else this.feed.start() }
    document.addEventListener("visibilitychange", this.visibility)
    this.clock = setInterval(() => { if (this.playing) this.now = Date.now() }, 1000)
    if (!document.hidden) this.feed.start()
  },
  beforeUnmount() {
    document.removeEventListener("visibilitychange", this.visibility)
    clearInterval(this.clock)
    this.keyValue = ""
    this.feed?.stop()
  },
  watch: {
    mode() { if (!document.hidden) this.feed.start(); else this.feed.stop() },
    "setup.preferences.deviceName": { immediate: true, handler(value) { if (typeof value === "string") this.nameValue = value } },
  },
  computed: {
    release() { return releaseInfo(this.setup?.release) },
    installed() { return !!this.setup?.release.installed && !this.setup.release.expired },
    outputChosen() {
      const prefs = this.setup?.preferences
      return !!prefs && (["test", "starport"].includes(prefs.output) || ADDRESS.test(prefs.outputAddress))
    },
    phoneSource() { return this.setup?.preferences.source === "phone" },
    pauseMuteLabel() { return muteLabel(this.pauseDraft ?? this.setup?.preferences.pauseMute) },
    skipMuteLabel() { return muteLabel(this.skipDraft ?? this.setup?.preferences.skipMute) },
    setupDone() { return this.phoneSource ? !!(ADDRESS.test(this.setup?.preferences.phoneAddress || "") && this.outputChosen) :
      !!(this.installed && this.setup?.keyPresent && this.outputChosen) },
    player() { return this.setup?.service?.player || null },
    playing() { return this.player?.status === "playing" },
    track() { return this.player?.loggedIn ? this.player.track : null },
    can() { return new Set(this.player?.actions || []) },
    elapsed() { return positionNow(this.player?.position, this.track?.durationMs, this.now) },
    statusText() { return serviceLabel(this.setup) },
    outputName() {
      const prefs = this.setup?.preferences
      if (!prefs) return ""
      if (prefs.output === "starport") return "StarPort"
      return prefs.output === "test" ? "Silent test output" : prefs.outputName || prefs.outputAddress || "Not chosen"
    },
    uploadPercent() {
      const { loaded, total } = this.uploadProgress || {}
      return total > 0 ? Math.min(100, Math.round(100 * loaded / total)) : 0
    },
    onPhone() {
      return typeof navigator !== "undefined" && (navigator.userAgentData?.mobile === true ||
        /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "") || navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
    },
    downloadsHint() {
      if (typeof navigator === "undefined") return ""
      if (/iPhone|iPad|iPod/i.test(navigator.userAgent || "") || navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) return "In Files, open Downloads."
      return /Android/i.test(navigator.userAgent || "") ? "Open Downloads." : ""
    },
    archiveError() {
      if (!this.archiveFile) return ""
      const preflight = archivePreflight(this.archiveFile)
      if (preflight) return preflight
      if (!Number.isSafeInteger(this.archiveFile.size) || this.archiveFile.size <= 0) return "The selected file is empty or its size cannot be read."
      if (this.setup && this.archiveFile.size > this.setup.maxUploadBytes) return "This file is too large to be the Soloist download."
      return this.archiveMagicError
    },
    installBlocked() { return this.setup?.uploadAllowed === false },
    uploadReason() {
      if (this.installBlocked) return "Installing Spotify needs your local network or the Galaxy tunnel."
      if (!this.archiveFile) return "Choose the file you downloaded."
      if (this.archiveError) return this.archiveError
      if (this.archiveChecking) return "Checking the file…"
      if (!this.setup?.parked) return "Put the car in Park or use offroad mode to install."
      if (this.setup.import?.state === "running") return "Wait for the current install to finish."
      return ""
    },
    installState() {
      const job = this.setup?.import
      const current = this.installAttempted && job && job.started !== this.installBaseline
      if (this.uploadProgress || this.installAttempted && this.busy && !current) return "uploading"
      if (job?.state === "running") return "checking"
      if (!current) return "choose"
      if (job.state === "done" && this.setup.release.installed) return "done"
      return job.state === "failed" ? "failed" : "choose"
    },
    checks() { return installChecks(this.setup?.import) },
    problem() { return importProblem(this.setup?.import) },
    keyValid() { return /^[A-Za-z0-9._~+/=-]{16,256}$/.test(this.keyValue.trim()) },
    nameValid() { return /^[^\x00-\x1f\x7f]{1,40}$/.test(this.nameValue.trim()) },
    outputChoices() {
      return this.deviceChoices.filter((choice) => choice.audioSink)
    },
    deviceChoices() {
      const devices = this.setup?.devices || []
      return devices.map((device) => ({ value: device.address, audioSink: device.audioSink === true, label: device.name + (device.connected ? "" : " (Not Connected)") }))
    },
  },
  methods: {
    switchRow, formatTime,
    setEnabled(enabled) { return this.feed.post("./api/spotify/enable", { enabled }) },
    async saveKey() {
      if (!this.keyValid || this.busy) return
      const key = this.keyValue.trim()
      this.keyValue = ""
      const result = await this.feed.post("./api/spotify/key", { key })
      if (result) this.keyEditing = false
    },
    removeKey() { return this.feed.post("./api/spotify/maintenance", { action: "remove_key" }) },
    chooseSource(event) { return this.feed.post("./api/spotify/preferences", { source: event.target.value }) },
    choosePhone(event) { return this.feed.post("./api/spotify/preferences", { phoneAddress: event.target.value }) },
    chooseOutput(event) {
      const value = event.target.value
      if (["test", "starport"].includes(value)) return this.feed.post("./api/spotify/preferences", { output: value, outputAddress: "" })
      if (ADDRESS.test(value)) return this.feed.post("./api/spotify/preferences", { output: "bluetooth", outputAddress: value })
    },
    saveName() {
      if (!this.nameValid || this.nameValue.trim() === this.setup?.preferences.deviceName) return
      return this.feed.post("./api/spotify/preferences", { deviceName: this.nameValue.trim() })
    },
    saveCache(event) { return this.feed.post("./api/spotify/preferences", { cacheMb: Number(event.target.value) }) },
    clearCache() { return this.feed.post("./api/spotify/maintenance", { action: "clear_cache" }, 20000) },
    async resetSession() { this.resetOpen = false; return this.feed.post("./api/spotify/maintenance", { action: "reset_session" }, 20000) },
    async removeAll() { this.removeOpen = false; return this.feed.removeAll() },
    control(action, args) { return this.feed.control(action, args) },
    togglePlay() { return this.control(this.playing ? "pause" : "play") },
    commitMute(kind, event) {
      const value = Number(event.target.value)
      this[`${kind}Draft`] = null
      if (Number.isFinite(value)) return this.feed.post("./api/spotify/preferences", { [`${kind}Mute`]: value })
    },
    openInstall() { this.chooseAgain(); Object.assign(this, { installOpen: true, downloadStarted: false }) },
    closeInstall() { this.installOpen = false },
    markDownloadStarted() {
      if (!this.busy && ["choose", "failed"].includes(this.installState)) this.chooseAgain()
      this.downloadStarted = true
    },
    chooseAgain() {
      this.archiveSelection++
      Object.assign(this, { installAttempted: false, archiveFile: null, archiveMagicError: "", archiveChecking: false })
    },
    async chooseArchive(event) {
      const file = event.target.files?.[0] || null
      const selection = ++this.archiveSelection
      Object.assign(this, { archiveFile: file, archiveMagicError: "", archiveChecking: false })
      if (!file || archivePreflight(file) || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > this.setup?.maxUploadBytes) return
      this.archiveChecking = true
      let error = ""
      try {
        const bytes = new Uint8Array(await file.slice(0, 2).arrayBuffer())
        if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) error = "That isn’t the Soloist download. Choose soloist_release_arm64.tar.gz."
        else if (file.size < 1024 * 1024) error = "This file is too small to be the Soloist download. Download it again."
      } catch { error = "The file couldn’t be read. Choose it again." }
      if (selection === this.archiveSelection) Object.assign(this, { archiveMagicError: error, archiveChecking: false })
    },
    async upload() {
      if (this.busy || this.uploadReason) return false
      Object.assign(this, { installAttempted: true, installBaseline: this.setup.import?.started ?? null })
      return this.feed.upload(this.archiveFile)
    },
  },
  template: `
    <section class="gx-driving gx-spotify" aria-label="Spotify" :aria-busy="busy || undefined">
      <header class="gx-page-header">
        <h2>Spotify and Phone Audio</h2>
        <p>Play Spotify on your comma, or send music from your phone through the comma to your car.</p>
      </header>
      <GxNotice tone="danger" v-if="error && setup">{{ error }}</GxNotice>
      <GxNotice v-if="!setup" :busy="!error" :tone="error ? 'danger' : 'info'">{{ error || "Checking Spotify setup… Reconnecting automatically…" }}</GxNotice>

      <GxNotice v-if="setup?.release.installed && !phoneSource" :tone="release.level === 'expired' ? 'danger' : release.level === 'soon' ? 'warn' : 'info'"
        :title="release.label">
        <p>Soloist expires 90 days after Spotify builds it, regardless of when you install it.</p>
        <template v-if="release.level === 'soon' || release.level === 'expired'">
          <p>{{ release.level === 'expired' ? 'Install a newer build to resume Spotify playback.' : 'Install a newer build before this one expires to keep Spotify playing.' }}</p>
        </template>
        <template #actions v-if="release.level === 'soon' || release.level === 'expired'">
          <button type="button" class="gx-btn" :disabled="busy || installBlocked" @click="openInstall">Update Soloist</button>
        </template>
      </GxNotice>

      <section v-if="setup" class="gx-card gx-starport-step" aria-labelledby="gx-music-source-title">
        <h3 id="gx-music-source-title">Music Source</h3>
        <label class="gx-stack"><span>Play music from</span>
          <select class="gx-field" :value="setup.preferences.source || 'spotify'" :disabled="busy" @change="chooseSource">
            <option value="spotify">Spotify Connect on the comma</option>
            <option value="phone">Phone over Bluetooth</option>
          </select>
        </label>
        <p v-if="!phoneSource" class="gx-note">Spotify streams on the comma. Your phone controls playback in Spotify; the comma sends the music to the audio output you choose below.</p>
        <template v-if="phoneSource">
          <p class="gx-note">Pair your phone in the companion app, or tap <strong>Pair phone</strong> on the <a class="gx-link" href="#/bluetooth">Bluetooth page</a>. Then choose it below and turn on phone audio. On your phone, select the comma as the media audio output. No Spotify install or API key is needed.</p>
          <label class="gx-stack"><span>Phone</span>
            <select class="gx-field" :value="setup.preferences.phoneAddress" :disabled="busy || !setup.parked" @change="choosePhone">
              <option value="">Choose your paired phone…</option>
              <option v-for="choice in deviceChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option>
            </select>
          </label>
          <GalaxySettingRow :row="switchRow('Phone Audio', setup.enabled, 'Forward music from the selected phone. Playback and volume stay on your phone.')"
            :index="0" :busy="busy" :save-value="(index, value) => setEnabled(value === 'On')" />
          <p class="gx-note" role="status">{{ statusText }}</p>
          <label class="gx-stack"><span>Pause smoothing · {{ pauseMuteLabel }}</span>
            <input type="range" class="gx-slider" min="0" :max="setup.preferences.muteMax || 1.5" step="0.05" :value="pauseDraft ?? setup.preferences.pauseMute"
              aria-label="Pause smoothing" :disabled="busy" @input="pauseDraft = Number($event.target.value)" @change="commitMute('pause', $event)" /></label>
          <label class="gx-stack"><span>Skip smoothing · {{ skipMuteLabel }}</span>
            <input type="range" class="gx-slider" min="0" :max="setup.preferences.muteMax || 1.5" step="0.05" :value="skipDraft ?? setup.preferences.skipMute"
              aria-label="Skip smoothing" :disabled="busy" @input="skipDraft = Number($event.target.value)" @change="commitMute('skip', $event)" /></label>
          <p class="gx-note">Briefly mutes incoming audio after pause or skip. Increase if the old song lingers; decrease if the next song starts clipped. Changes apply immediately.</p>
          <p class="gx-note">Music only. Calls and microphone audio are not forwarded.</p>
        </template>
        <template v-if="setupDone && !phoneSource && !showSetup">
        <label class="gx-stack"><span>Send audio to</span>
          <select class="gx-field" :value="setup.preferences.output === 'bluetooth' ? setup.preferences.outputAddress : setup.preferences.output" :disabled="busy || !setup.parked" @change="chooseOutput">
            <option value="" disabled>Choose an audio output…</option>
            <option value="starport">Car speakers via StarPort (Android Auto)</option>
            <optgroup label="Bluetooth car stereos and speakers">
              <option v-for="choice in outputChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option>
            </optgroup>
            <option value="test">Silent test (no speaker)</option>
          </select></label>
        <p class="gx-note">Choose your car speakers through StarPort, or a Bluetooth car stereo or speaker paired with the comma. Only Bluetooth devices that support receiving music appear here.</p>
        <p v-if="!outputChoices.length" class="gx-note">No paired Bluetooth audio outputs found. Pair your car stereo or speaker on the <a class="gx-link" href="#/bluetooth">Bluetooth page</a>, or use StarPort.</p>
        </template>
        <small v-if="phoneSource && !setup.parked" class="gx-note">Put the car in Park or use offroad mode to change the phone.</small>
      </section>

      <section v-if="setup && setupDone && !phoneSource" class="gx-card gx-starport-step gx-starport-home gx-spotify-now" aria-labelledby="gx-spotify-now-title">
        <div class="gx-spotify-now__main">
          <div class="gx-spotify-now__text">
            <h3 id="gx-spotify-now-title" class="gx-truncate"><span translate="no">{{ track?.title || setup.preferences.deviceName }}</span></h3>
            <p v-if="track" class="gx-truncate"><span translate="no">{{ track.artists }}</span><template v-if="track.album"> · <span translate="no">{{ track.album }}</span></template></p>
            <p role="status" class="gx-note gx-truncate">{{ statusText }}<template v-if="setup.enabled"> · {{ outputName }}</template></p>
          </div>
        </div>
        <div v-if="track && track.durationMs" class="gx-spotify-now__progress">
          <input type="range" class="gx-slider gx-slider--galaxy" min="0" :max="track.durationMs" step="1000" :value="elapsed" :style="{ '--fill': Math.min(100, elapsed / track.durationMs * 100) + '%' }" :disabled="!can.has('seek')" aria-label="Position"
            @change="control('seek', { positionMs: Math.round(Number($event.target.value)) })" />
          <small class="gx-note">{{ formatTime(elapsed) }} / {{ formatTime(track.durationMs) }}</small>
        </div>
        <div v-if="setup.enabled && player?.loggedIn" class="gx-actions gx-spotify-now__controls">
          <button type="button" class="gx-icon-btn" aria-label="Previous" :disabled="!can.has('skip_prev')" @click="control('previous')"><i class="bi bi-skip-start-fill"></i></button>
          <button type="button" class="gx-btn gx-spotify-now__play" :aria-label="playing ? 'Pause' : 'Play'" :disabled="!can.has(playing ? 'pause' : 'play') && !!track" @click="togglePlay">
            <i :class="playing ? 'bi bi-pause-fill' : 'bi bi-play-fill'" aria-hidden="true"></i></button>
          <button type="button" class="gx-icon-btn" aria-label="Next" :disabled="!can.has('skip_next')" @click="control('next')"><i class="bi bi-skip-end-fill"></i></button>
        </div>
        <small v-if="setup.enabled && player?.loggedIn && player.volume !== null" class="gx-note">Volume {{ player.volume }}% - change in Spotify app if needed, otherwise use car volume</small>
        <div v-if="setup.enabled && player?.loggedIn && !player.active" class="gx-stack">
          <button type="button" class="gx-btn gx-btn--tonal" @click="control('activate')">
            <i class="bi bi-broadcast" aria-hidden="true"></i> Move Spotify to <span translate="no">{{ setup.preferences.deviceName || 'StarPilot' }}</span></button>
          <small class="gx-note">Takes over from the phone or speaker Spotify is playing on now. Music plays through {{ outputName }}. You might need to confirm the switch in the Spotify app.</small>
        </div>
        <GalaxySettingRow :row="switchRow('Move to ' + (setup.preferences.deviceName || 'StarPilot') + ' While Driving', setup.preferences.pullOnroad, 'While driving, takes Spotify over from your phone or another speaker whenever it is playing somewhere else.')"
          :index="1" :busy="busy" :save-value="(index, value) => feed.post('./api/spotify/preferences', { pullOnroad: value === 'On' })" />
        <GalaxySettingRow :row="switchRow('Spotify', setup.enabled, 'Turn off to stop playing and free up the comma. Your setup stays saved.')"
          :index="0" :busy="busy" :save-value="(index, value) => setEnabled(value === 'On')" />
        <div class="gx-actions">
          <button type="button" class="gx-btn gx-btn--tonal" :aria-expanded="showSetup" aria-controls="gx-spotify-steps" @click="showSetup = !showSetup">
            <i :class="showSetup ? 'bi bi-chevron-up' : 'bi bi-gear'" aria-hidden="true"></i> {{ showSetup ? 'Hide Setup' : 'Setup and Settings' }}</button>
        </div>
      </section>

      <ol v-if="setup" v-show="phoneSource || !setupDone || showSetup" id="gx-spotify-steps" class="gx-starport__steps">
        <li v-if="!phoneSource" class="gx-card gx-starport-step" :class="{ 'gx-starport-step--done': installed }">
          <div class="gx-starport-step__head">
            <span class="gx-starport-step__num" aria-hidden="true"><i v-if="installed" class="bi bi-check-lg"></i><template v-else>1</template></span>
            <div><h3>Spotify Soloist</h3><p>Spotify’s player for devices like your comma. You download it from Spotify, then upload it here.</p></div>
          </div>
          <div class="gx-starport-step__body">
            <div class="gx-starport-status">
              <span class="gx-starport-pill" :class="'gx-starport-pill--' + release.level" role="status">{{ release.label }}</span>
              <div class="gx-actions gx-starport-status__actions">
                <button v-if="!setup.release.installed" class="gx-btn" :disabled="installBlocked" @click="openInstall">Install</button>
                <button v-else-if="release.level !== 'ok'" class="gx-btn" :disabled="installBlocked" @click="openInstall">Update</button>
                <button v-else class="gx-btn gx-btn--tonal" :disabled="installBlocked" @click="openInstall">Install newer version</button>
              </div>
            </div>
            <p v-if="installBlocked" class="gx-note" role="status">Installing Spotify needs your local network or the Galaxy tunnel. Phone audio works over Bluetooth.</p>
            <p v-if="setup.import?.state === 'running' && !installOpen" class="gx-note" role="status">Installing…</p>
            <p class="gx-note">Each Soloist build works for 90 days from its build date, not its install date. Before it runs out, this step shows <strong>Update</strong>; download again the same way.</p>
          </div>
        </li>

        <li v-if="!phoneSource" class="gx-card gx-starport-step" :class="{ 'gx-starport-step--done': setup.keyPresent }">
          <div class="gx-starport-step__head">
            <span class="gx-starport-step__num" aria-hidden="true"><i v-if="setup.keyPresent" class="bi bi-check-lg"></i><template v-else>2</template></span>
            <div><h3>Your API Key</h3><p>Spotify gives each person their own key. Creating one needs Spotify Premium.</p></div>
          </div>
          <div class="gx-starport-step__body">
            <template v-if="setup.keyPresent && !keyEditing">
              <span class="gx-starport-pill gx-starport-pill--ok" role="status">Key saved on this comma</span>
              <div class="gx-actions">
                <button type="button" class="gx-btn gx-btn--tonal" @click="keyEditing = true">Replace Key</button>
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="removeKey">Remove Key</button>
              </div>
            </template>
            <template v-else>
              <a class="gx-btn gx-btn--tonal" :href="setup.links.key" target="_blank" rel="noopener noreferrer">
                <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Create Your API Key</a>
              <form class="gx-stack" @submit.prevent="saveKey">
                <label class="gx-stack"><span>Paste your key</span>
                  <input class="gx-field" type="password" v-model="keyValue" autocomplete="off" autocapitalize="off" spellcheck="false"
                    inputmode="text" maxlength="256" aria-describedby="gx-spotify-key-note" /></label>
                <div class="gx-actions">
                  <button type="submit" class="gx-btn" :disabled="!keyValid || busy">Save Key</button>
                  <button v-if="keyEditing" type="button" class="gx-btn gx-btn--tonal" @click="keyEditing = false; keyValue = ''">Cancel</button>
                </div>
              </form>
              <small id="gx-spotify-key-note" class="gx-note">The key stays on your comma and is never shown again. On your home network Galaxy isn’t encrypted, so save it from a network you trust.</small>
            </template>
          </div>
        </li>

        <li class="gx-card gx-starport-step" :class="{ 'gx-starport-step--done': outputChosen }">
          <div class="gx-starport-step__head">
            <span class="gx-starport-step__num" aria-hidden="true"><i v-if="outputChosen" class="bi bi-check-lg"></i><template v-else>3</template></span>
            <div><h3>Where Music Plays</h3><p>Send music from the comma to your car speakers through StarPort (Android Auto), or to a Bluetooth car stereo or speaker. Alerts keep playing from the comma itself.</p></div>
          </div>
          <div class="gx-starport-step__body">
            <label class="gx-stack"><span>Send audio to</span>
              <select class="gx-field" :value="setup.preferences.output === 'bluetooth' ? setup.preferences.outputAddress : setup.preferences.output" :disabled="busy || !setup.parked" @change="chooseOutput">
                <option value="" disabled>Choose an audio output…</option>
                <option value="starport">Car speakers via StarPort (Android Auto)</option>
                <optgroup label="Bluetooth car stereos and speakers">
                  <option v-for="choice in outputChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option>
                </optgroup>
                <option value="test">Silent test (no speaker)</option>
              </select></label>
            <p class="gx-note">Only Bluetooth devices that support receiving music appear here. Pairing a phone or radar detector does not make it an audio output.</p>
            <small v-if="!setup.parked" class="gx-note">Put the car in Park or use offroad mode to change this.</small>
            <p v-if="setup.preferences.output === 'starport'" class="gx-note">Music plays through the car while <a class="gx-link" href="#/starport">StarPort</a> is connected, and the car shows it as Android Auto audio. If you switch the car to the radio, playback is paused when the source supports it.</p>
            <p v-else-if="!outputChoices.length" class="gx-note">No paired Bluetooth audio outputs found. Pair your car stereo or speaker on the <a class="gx-link" href="#/bluetooth">Bluetooth</a> page first.</p>
            <p v-else-if="setup.preferences.output === 'bluetooth'" class="gx-note">Don’t see it? Pair it on the <a class="gx-link" href="#/bluetooth">Bluetooth</a> page. In your car, choose <strong>Bluetooth Audio</strong> as the source.</p>
          </div>
        </li>

        <li v-if="!phoneSource" class="gx-card gx-starport-step" :class="{ 'gx-starport-step--done': setupDone && setup.enabled && player?.loggedIn }">
          <div class="gx-starport-step__head">
            <span class="gx-starport-step__num" aria-hidden="true"><i v-if="setupDone && setup.enabled && player?.loggedIn" class="bi bi-check-lg"></i><template v-else>4</template></span>
            <div><h3>Connect Spotify</h3><p>Turn it on, then pick <strong><span translate="no">{{ setup.preferences.deviceName }}</span></strong> in the Spotify app’s device list.</p></div>
          </div>
          <div class="gx-starport-step__body">
            <GalaxySettingRow :row="switchRow('Spotify', setup.enabled, 'Runs Spotify on your comma. Turn off to stop playing; your setup stays saved.')"
              :index="0" :busy="busy" :save-value="(index, value) => setEnabled(value === 'On')" />
            <p v-if="setup.enabled" class="gx-note" role="status">{{ statusText }}</p>
            <p class="gx-note">Pair once on the same Wi‑Fi or hotspot. After pairing, keep Spotify enabled and both devices online; your comma can use cellular or a phone hotspot. Use the same Spotify account on your phone.</p>
            <form class="gx-stack" @submit.prevent="saveName">
              <label class="gx-stack"><span>Name in Spotify</span>
                <input class="gx-field" type="text" v-model="nameValue" maxlength="40" autocomplete="off" /></label>
              <button type="submit" class="gx-btn gx-btn--tonal" :disabled="busy || !nameValid || nameValue.trim() === setup.preferences.deviceName">Save Name</button>
            </form>
          </div>
        </li>
      </ol>

      <section v-if="setup && !phoneSource && (setupDone ? showSetup : setup.release.installed)" class="gx-card gx-starport-step" aria-labelledby="gx-spotify-manage-title">
        <h3 id="gx-spotify-manage-title">Storage and Account</h3>
        <label class="gx-stack"><span>Music cache</span>
          <select class="gx-field" :value="setup.preferences.cacheMb" :disabled="busy" @change="saveCache">
            <option v-for="size in setup.cacheChoices" :key="size" :value="size">{{ size >= 1024 ? (size / 1024) + ' GB' : size + ' MB' }}</option>
          </select></label>
        <div class="gx-actions">
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy || !setup.parked" @click="clearCache">Clear Cache</button>
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy || !setup.parked" @click="resetOpen = true">Use a Different Spotify Account</button>
          <button type="button" class="gx-btn gx-btn--danger" :disabled="busy || setup.import?.state === 'running'" @click="removeOpen = true">Remove Spotify</button>
        </div>
      </section>

      <section class="gx-settings gx-starport-wiki-list" aria-label="Spotify help">
        <h4>Help</h4>
        <details class="gx-starport-wiki"><summary>Why do I download Soloist myself?</summary>
          <p>Spotify asks that each person download Soloist from Spotify and use their own API key, so StarPilot can’t include it. Your comma checks that the file you upload is exactly what Spotify is offering before installing it.</p>
        </details>
        <details class="gx-starport-wiki"><summary>Spotify doesn’t show my comma</summary>
          <ol>
            <li>Keep Spotify enabled in step 4. A disconnected Bluetooth speaker can wait while you pair; music will be silent until it connects.</li>
            <li>Put your phone on the same Wi‑Fi or hotspot as your comma. Some hotspots and guest networks block devices from seeing each other.</li>
            <li>For cellular or different networks after pairing, turn off <strong>Local device visibility</strong> in Spotify → Settings → Apps and devices. Both devices need internet access.</li>
            <li>On iPhone, allow Spotify’s <strong>Local Network</strong> permission in iPhone Settings. If your phone’s hotspot blocks initial pairing, pair once on another shared Wi‑Fi network.</li>
            <li>If it still does not appear, turn Spotify off and back on here. This reconnects using your saved account; you do not need to clear the account or cache.</li>
            <li>In Spotify, tap the device icon and look for <strong><span translate="no">{{ setup?.preferences.deviceName || 'StarPilot' }}</span></strong>.</li>
          </ol>
        </details>
        <details class="gx-starport-wiki"><summary>Can I use the comma hotspot or wireless Android Auto?</summary>
          <p>The comma hotspot can provide the local connection for pairing, but the comma also needs internet access to stream. A wireless Android Auto connection alone does not guarantee Spotify discovery or internet access. After pairing, Spotify can use the saved account over the comma’s cellular connection.</p>
        </details>
        <details class="gx-starport-wiki"><summary>Does it use my data plan?</summary>
          <p>Yes, when your comma streams over cellular. Music usually uses about 40–150 MB an hour depending on quality. Songs you’ve played recently come from the cache instead.</p>
        </details>
        <details class="gx-starport-wiki"><summary>Will music cover up StarPilot’s alerts?</summary>
          <p>Music plays through your car or speaker; alerts keep playing from the comma’s own speaker. Keep the music at a level where you can still hear the comma.</p>
        </details>
        <details class="gx-starport-wiki"><summary>Can I use it with Android Auto?</summary>
          <p>Yes. Under <strong>Send audio to</strong>, choose <strong>Car speakers via StarPort (Android Auto)</strong>, and music plays through the car as Android Auto audio alongside the StarPilot screen. It needs Android Auto to be connected, so if it isn’t, music has nowhere to play.</p>
        </details>
        <details class="gx-starport-wiki"><summary>More help from Spotify</summary>
          <p><a class="gx-link" :href="setup?.links.help || '#'" target="_blank" rel="noopener noreferrer">Spotify’s Soloist getting-started guide</a></p>
        </details>
      </section>

      <GxDialog v-if="resetOpen && setup" labelledby="gx-spotify-reset-title" describedby="gx-spotify-reset-body" alert @close="resetOpen = false">
        <div><h3 id="gx-spotify-reset-title">Use a different Spotify account?</h3>
          <p id="gx-spotify-reset-body">This signs your comma out of Spotify and clears its cache. Then pick it in the Spotify app on the account you want.</p></div>
        <div class="gx-settings__controls gx-actions">
          <button type="button" class="gx-btn gx-btn--tonal" @click="resetOpen = false">Cancel</button>
          <button type="button" class="gx-btn" @click="resetSession">Sign Out</button>
        </div>
      </GxDialog>

      <GxDialog v-if="removeOpen && setup" labelledby="gx-spotify-remove-title" describedby="gx-spotify-remove-body" alert @close="removeOpen = false">
        <div><h3 id="gx-spotify-remove-title">Remove Spotify?</h3>
          <p id="gx-spotify-remove-body">This stops music and deletes Soloist, your API key, your Spotify sign-in and the music cache from this comma. Your Bluetooth devices and StarPort setup stay as they are.</p></div>
        <div class="gx-settings__controls gx-actions">
          <button type="button" class="gx-btn gx-btn--tonal" @click="removeOpen = false">Cancel</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="removeAll">Remove</button>
        </div>
      </GxDialog>

      <GxDialog v-if="installOpen && setup" labelledby="gx-spotify-install-title" class="gx-starport-sheet__dialog" @close="closeInstall">
        <div class="gx-starport-sheet__toolbar">
          <button type="button" class="gx-icon-btn" aria-label="Close" @click="closeInstall"><i class="bi bi-x-lg"></i></button>
        </div>
        <div class="gx-starport-sheet__body">
          <div v-if="installState === 'done'" class="gx-message gx-starport-sheet__done">
            <div class="gx-starport-done-badge"><i class="bi bi-check-lg" aria-hidden="true"></i></div>
            <h3 id="gx-spotify-install-title">Soloist is installed</h3>
            <p class="gx-note">{{ release.label }}</p>
            <ul class="gx-starport-checks"><li v-for="check in checks" :key="check.label" :class="'gx-starport-check--' + check.status">{{ check.label }}</li></ul>
            <button class="gx-btn gx-starport-cta" @click="closeInstall">Done</button>
          </div>
          <div v-else class="gx-stack">
            <div class="gx-stack gx-starport-sheet__head">
              <h3 id="gx-spotify-install-title">{{ setup.release.installed ? 'Install a Newer Soloist' : 'Install Spotify Soloist' }}</h3>
              <p class="gx-note">{{ setup.release.installed ? release.label + '. Your settings and Spotify sign-in stay as they are.' : 'Download it from Spotify, then send it to your comma.' }}</p>
            </div>
            <ol class="gx-starport-sheet__steps">
              <li class="gx-starport-step-card" :class="{ 'gx-starport-sheet__step--muted': installState !== 'choose' }">
                <div class="gx-starport-step-card__head">
                  <span class="gx-starport-step__num gx-starport-step-card__num">1</span>
                  <div class="gx-starport-step-card__title"><strong>Download Soloist</strong>
                    <small>From Spotify’s Downloads page, to this {{ onPhone ? 'phone' : 'computer' }}</small></div>
                </div>
                <div class="gx-starport-step-card__body">
                  <a class="gx-btn gx-btn--tonal" :href="setup.links.download" target="_blank" rel="noopener noreferrer" @click="markDownloadStarted">
                    <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open Spotify Downloads</a>
                  <ol class="gx-note gx-starport-mirror-steps">
                    <li>Download the <strong>ARMv8/AArch64 Linux</strong> build. The file is named <strong>{{ setup.archiveName }}</strong>.</li>
                    <li>Leave it as it is. Don’t unzip or open it.</li>
                  </ol>
                </div>
              </li>
              <li class="gx-starport-step-card" :class="{ 'gx-starport-sheet__step--muted': installState !== 'choose' }">
                <div class="gx-starport-step-card__head">
                  <span class="gx-starport-step__num gx-starport-step-card__num">2</span>
                  <div class="gx-starport-step-card__title"><strong>Choose the downloaded file</strong>
                    <small>{{ downloadStarted && downloadsHint ? downloadsHint : 'The file you just downloaded' }}</small></div>
                </div>
                <div class="gx-starport-step-card__body">
                  <label class="gx-btn gx-btn--tonal gx-starport-file" :class="{ 'gx-starport-file--disabled': installState !== 'choose' }">
                    <input type="file" :accept="onPhone ? undefined : '.gz,.tgz,application/gzip'" aria-label="Soloist .tar.gz download" :disabled="installState !== 'choose'" @change="chooseArchive" />
                    <i class="bi bi-folder2-open"></i> {{ archiveFile ? 'Choose a Different File' : 'Choose File' }}
                  </label>
                  <small v-if="archiveChecking" role="status" class="gx-note">Checking the file…</small>
                  <div v-if="archiveFile && !archiveError && !archiveChecking" class="gx-chip gx-starport-file-chip">
                    <i class="bi bi-file-earmark-zip"></i><span><span translate="no">{{ archiveFile.name }}</span></span><small>{{ Math.ceil(archiveFile.size / 1048576) }} MB</small>
                  </div>
                  <small v-if="archiveError" class="gx-starport-bad" role="alert">{{ archiveError }}</small>
                </div>
              </li>
              <li class="gx-starport-step-card">
                <div class="gx-starport-step-card__head">
                  <span class="gx-starport-step__num gx-starport-step-card__num">3</span>
                  <div class="gx-starport-step-card__title"><strong>Install</strong><small>Send it to your comma and check it with Spotify</small></div>
                </div>
                <div class="gx-starport-step-card__body">
                  <button v-if="installState === 'choose'" class="gx-btn gx-starport-cta" :disabled="!!uploadReason" @click="upload">Install</button>
                  <small v-if="installState === 'choose' && uploadReason && archiveFile && !archiveError" class="gx-note" role="status">{{ uploadReason }}</small>
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
                  <p v-if="error && installState === 'choose' && installAttempted" class="gx-starport-bad" role="alert">{{ error }}</p>
                </div>
              </li>
            </ol>
            <div class="gx-stack gx-starport-sheet__footer">
              <small class="gx-note gx-starport-trademark">Spotify is a trademark of Spotify AB. StarPilot is not affiliated with or endorsed by Spotify.</small>
              <small class="gx-note">The uploaded file is deleted after install. Maximum size {{ Math.floor(setup.maxUploadBytes / 1048576) }} MB.</small>
            </div>
          </div>
        </div>
      </GxDialog>
    </section>`,
}
