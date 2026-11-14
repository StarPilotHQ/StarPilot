import { GxDialog } from "./dialog.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { connectionError } from "./polling.js"
import { GalaxySelect } from "./galaxy-select.js"
import { reportSave } from "./save-feedback.js"

// Read-only model identity; a saved label never proves what modeld loaded.
export class ModelStatusFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancel })
    this.active = false
    this.generation = 0
    this.displayGeneration = 0
    this.request = null
    this.poll = null
    this.expiry = null
  }

  stop() {
    this.active = false
    this.generation++
    this.displayGeneration++
    this.request?.abort()
    this.request = null
    if (this.poll !== null) this.cancel(this.poll)
    if (this.expiry !== null) this.cancel(this.expiry)
    this.poll = this.expiry = null
    this.publish({ status: "idle", data: null, error: "" })
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
    if (this.poll !== null) this.cancel(this.poll)
    this.poll = null
    const request = new AbortController()
    this.request = request
    const timeout = this.later(() => request.abort(), 2500)
    try {
      const response = await this.fetcher("./api/models/status", { signal: request.signal, cache: "no-store" })
      if (!this.active || generation !== this.generation || request.signal.aborted) return
      if (response.status === 401) {
        this.stop()
        this.unauthorized()
        return
      }
      if (!response.ok) throw new Error("Model status is unavailable")
      const data = await response.json()
      if (!this.active || generation !== this.generation || request.signal.aborted) return
      const states = ["unavailable", "loading", "identity-unavailable", "active", "stale", "failed"]
      const variants = [null, "small", "chestnut"]
      const digest = data?.artifactSha256
      const ids = Array.isArray(data?.catalog) ? data.catalog.map(m => m?.id) : []
      if (data?.schemaVersion !== 1 || !ids.length || new Set(ids).size !== ids.length ||
          data.catalog.some(m => typeof m.id !== "string" || !m.id || typeof m.name !== "string" || typeof m.selectable !== "boolean") ||
          !ids.includes(data.requestedId) || !(data.loadedId === null || ids.includes(data.loadedId)) || !variants.includes(data.variant) ||
          !states.includes(data.health) || ![null, "chestnut-load-failed", "chestnut-run-stalled", "selected-load-failed"].includes(data.fallbackReason) ||
          !(digest === null || typeof digest === "string" && /^[0-9a-f]{64}$/.test(digest)) ||
          typeof data.pendingNextStart !== "boolean" ||
          (data.pendingNextStart && (data.loadedId === null || data.requestedId === data.loadedId)) ||
          (data.loadedId === null ? data.variant !== null || digest !== null : data.variant === null || digest === null) ||
          (data.health === "active" && data.loadedId === null)) {
        throw new Error("Invalid model status")
      }
      this.publish({ status: "ready", data, error: "" })
      if (this.expiry !== null) this.cancel(this.expiry)
      const displayGeneration = ++this.displayGeneration
      this.expiry = this.later(() => {
        if (this.active && displayGeneration === this.displayGeneration) {
          this.publish({ status: "stale", data: null, error: "Model status refresh delayed" })
        }
      }, 1500)
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.displayGeneration++
        if (this.expiry !== null) this.cancel(this.expiry)
        this.expiry = null
        this.publish({ status: "unavailable", data: null, error: error.message })
      }
    } finally {
      this.cancel(timeout)
      if (generation === this.generation) this.request = null
      if (this.active && generation === this.generation) this.poll = this.later(() => this.load(), 1000)
    }
  }
}


const TAB_KEY = "galaxy.modelManager.libraryTab"
const TABS = new Set(["both", "gpu", "comma"])

function readTab() {
  try {
    const value = localStorage.getItem(TAB_KEY)
    return TABS.has(value) ? value : "both"
  } catch {
    return "both"
  }
}

function saveTab(value) {
  const tab = TABS.has(value) ? value : "both"
  try { localStorage.setItem(TAB_KEY, tab) } catch {}
  return tab
}

function matchesHardware(model, tab) {
  if (tab === "gpu") return model?.requiresGpu === true
  if (tab === "comma") return model?.requiresGpu === false
  return true
}

function hardwareLabel(model) {
  if (model?.requiresGpu === true) return "GPU · External"
  if (model?.requiresGpu === false) return "Comma · On-device"
  return "Hardware unknown"
}

export function hardwareTag(model) {
  if (model?.requiresGpu === true) return { label: "eGPU", tone: "egpu", title: "Big model: runs on an external GPU" }
  if (model?.requiresGpu === false) return { label: "Small", tone: "small", title: "Small model: runs on the comma itself" }
  return { label: "Hardware unknown", tone: "unknown", title: "" }
}

function bytes(value) {
  return Number.isSafeInteger(value) && value > 0 ? value : null
}

function formatBytes(value) {
  return value >= 1e9 ? `${(value / 1e9).toFixed(2)} GB` : `${(value / 1e6).toFixed(1)} MB`
}

function fileSizeText(model) {
  const installed = bytes(model?.fileSizeBytes)
  const declared = bytes(model?.declaredSizeBytes || model?.artifactSize)
  const downloaded = bytes(model?.downloadedBytes)
  if (model?.partial || model?.sizeStatus === "partial") {
    return `Partial: ${downloaded ? formatBytes(downloaded) : "unknown"} / ${declared ? formatBytes(declared) : "unknown"}`
  }
  if (installed && declared && installed !== declared) return `${formatBytes(installed)} · size mismatch`
  if (installed) return formatBytes(installed)
  if (declared) return formatBytes(declared)
  return "Unavailable"
}

export function releasedText(value) {
  const date = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00Z`) : null
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : typeof value === "string" ? value : ""
}

export function modelFacts(model) {
  return [fileSizeText(model), model?.version, releasedText(model?.released), model?.builtin ? "Built-in" : ""].filter(Boolean).join(" · ")
}

export function formatRate(value) {
  return Number.isFinite(value) && value > 0 ? `${(value / 1e6).toFixed(value >= 1e7 ? 0 : 1)} MB/s` : ""
}

export function formatEta(seconds, short = false) {
  if (!Number.isFinite(seconds) || seconds < 0) return ""
  if (seconds < 45) return short ? "under 1 min" : "less than a minute left"
  const minutes = Math.round(seconds / 60)
  const span = minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr ${minutes % 60} min`
  return short ? `~${span}` : `about ${span} left`
}

const PHASES = { catalog: "Checking the model catalog", verifying: "Verifying download", installing: "Installing model" }

// Progress panel shape; a percent in a text-only progress line still draws a bar.
export function describeProgress(data, models = []) {
  const detail = data?.downloadProgress
  const label = value => models.find(m => m.value === value)?.label || ""
  if (detail && typeof detail === "object") {
    const total = bytes(detail.total), received = Math.max(0, Number(detail.received) || 0)
    const phase = detail.phase === "failed" ? "failed" : PHASES[detail.phase] ? detail.phase : "downloading"
    const count = Number(detail.count) > 1 ? Number(detail.count) : 0
    return {
      phase, failed: phase === "failed", name: label(detail.model), error: typeof detail.error === "string" ? detail.error : "",
      indeterminate: phase !== "downloading" && phase !== "failed",
      title: PHASES[phase] || "",
      percent: total ? Math.min(100, Math.floor(received * 100 / total)) : null,
      amount: total ? `${formatBytes(received)} of ${formatBytes(total)}` : "",
      rate: phase === "downloading" ? formatRate(detail.rate) : "",
      eta: phase === "downloading" && total && detail.rate > 0 && received > 0 ? formatEta((total - received) / detail.rate) : "",
      etaShort: phase === "downloading" && total && detail.rate > 0 && received > 0 ? formatEta((total - received) / detail.rate, true) : "",
      queue: count ? `Model ${Math.min(Number(detail.index) || 1, count)} of ${count}` : "",
    }
  }
  const text = typeof data?.progress === "string" ? data.progress : ""
  const percent = /(\d{1,3})%/.exec(text)
  return { phase: "downloading", failed: false, name: label(data?.modelToDownload), error: "", indeterminate: !percent, title: percent ? "" : text,
    percent: percent ? Math.min(100, Number(percent[1])) : null, amount: "", rate: "", eta: "", etaShort: "", queue: "" }
}


const actionPaths = { "select-small": "active", "select-big": "active", favorite: "preferences", unfavorite: "preferences",
  "enable-randomizer": "preferences", "disable-randomizer": "preferences", exclude: "preferences", include: "preferences",
  download: "download", downloadAll: "download_all", cancel: "cancel", delete: "delete", refresh: "refresh_manifest", jetlink: "jetlink" }
const actionCapabilities = { "select-small": "select", "select-big": "select", favorite: "favorites", unfavorite: "favorites",
  "enable-randomizer": "randomizer", "disable-randomizer": "randomizer", exclude: "exclusions", include: "exclusions",
  download: "download", downloadAll: "downloadAll", cancel: "cancel", delete: "delete", refresh: "refresh", jetlink: "jetlink" }

export function fitsModelProfile(model, profile) {
  return Array.isArray(model?.profiles) ? model.profiles.includes(profile) : profile === "big" ? model?.requiresGpu === true : model?.requiresGpu === false
}

export function modelActionAllowed(data, action, model = null) {
  if (!data || data.capabilities?.[actionCapabilities[action]] !== true) return false
  if (data.isOnroad !== false && !["favorite", "unfavorite", "cancel", "select-small", "select-big"].includes(action)) return false
  if (action === "jetlink") return data.jetlink !== undefined
  if (["select-small", "select-big"].includes(action)) {
    if (action === "select-big" && data.jetlinkBig && data.isOnroad !== false) return false
    if (data.downloading && data.refreshing !== true || data.randomizer === true) return false
    if (action === "select-big" && model === null) return true
    // the manager marks what each Active can take: installed models, or under Jetlink a big one with an ONNX
    return model?.selectable === true && fitsModelProfile(model, action === "select-big" ? "big" : "small")
  }
  if (action === "download") return !data.downloading && !!model && !model.installed && model.downloadAvailable === true
  if (action === "downloadAll") return !data.downloading && data.models.some(m => !m.installed && m.downloadAvailable === true)
  if (action === "cancel") return data.downloading === true
  if (action === "delete") return !data.downloading && !!model && !model.builtin && model.deletable !== false &&
    (model.installed || model.partial) && ![data.currentModel, data.activeSmallModel, data.activeBigModel].includes(model.value)
  if (["exclude", "include"].includes(action)) return !!model
  if (["enable-randomizer", "disable-randomizer"].includes(action)) return !data.downloading
  return ["favorite", "unfavorite"].includes(action) ? !!model : action === "refresh" && !data.downloading
}

export class ModelManagerFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancel })
    this.active = false
    this.generation = 0
    this.request = this.poll = this.expiry = null
    this.data = null
    this.saving = false
    this.lastError = ""
  }
  stop() {
    this.active = false
    this.generation++
    this.request?.abort()
    this.request = null
    if (this.poll !== null) this.cancel(this.poll)
    this.poll = null
    if (this.expiry !== null) this.cancel(this.expiry)
    this.expiry = null
    this.data = null
    this.saving = false
    this.publish({ loading: false, data: null, error: "" })
  }
  start() {
    this.stop()
    this.active = true
    return this.load()
  }
  async requestJson(path, payload) {
    const generation = ++this.generation
    if (payload) this.lastError = ""
    this.request?.abort()
    if (this.poll !== null) this.cancel(this.poll)
    this.poll = null
    const request = new AbortController()
    this.request = request
    const timeout = this.later(() => {
      if (!this.active || generation !== this.generation) return
      request.abort()
      this.generation++
      this.request = null
      this.data = null
      this.saving = false
      this.lastError = payload ? "The result is unknown. Checking automatically…" : "Model Manager is unavailable. Reconnecting automatically…"
      this.publish({ loading: false, data: null, error: this.lastError })
      this.poll = this.later(() => this.load(), 2000)
    }, 10000)
    try {
      const response = await this.fetcher(`./api/models/${path}`, { signal: request.signal, cache: "no-store", credentials: "same-origin",
        ...(payload ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) } : {}) })
      if (!this.active || generation !== this.generation || request.signal.aborted) return null
      const data = await response.json()
      if (!this.active || generation !== this.generation || request.signal.aborted) return null
      if (response.status === 401 || ["access_unavailable", "setup_required"].includes(data?.code)) {
        this.stop()
        this.unauthorized()
        return null
      }
      if (!response.ok) throw new Error(data?.message || data?.error || "Model request failed")
      return data
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.data = null
        this.lastError = connectionError(error)
        this.publish({ loading: false, data: null, error: this.lastError })
      }
      return null
    } finally {
      this.cancel(timeout)
      if (this.request === request) this.request = null
    }
  }
  async load() {
    if (!this.active || this.saving) return null
    if (!this.data && !this.lastError) this.publish({ loading: true, data: null, error: "" })
    const data = await this.requestJson("manager")
    if (!this.active) return null
    if (data) {
      if (data.schemaVersion !== 1 || !Array.isArray(data.models) ||
          data.models.some(m => !m || typeof m.value !== "string" || typeof m.label !== "string") ||
          typeof data.isOnroad !== "boolean" || typeof data.capabilities !== "object" || data.capabilities === null) {
        this.data = null
        this.publish({ loading: false, data: null, error: "Invalid Model Manager response" })
      } else {
        this.data = data
        this.lastError = ""
        if (this.expiry !== null) this.cancel(this.expiry)
        this.expiry = null
        if (data.jetlink?.active === true) {
          this.expiry = this.later(() => {
            this.expiry = null
            if (!this.active || this.data !== data) return
            this.data = { ...data, jetlink: { ...data.jetlink, active: false, state: "checking execution", runtimeState: "unavailable" } }
            this.publish({ loading: false, data: this.data, error: "" })
          }, 1500)
        }
        this.publish({ loading: false, data, error: "" })
      }
    }
    if (this.poll === null) this.poll = this.later(() => this.load(), this.data?.jetlink?.mode !== "off" && this.data?.jetlink ? 1000 : 2000)
    return this.data
  }
  async action(action, model = null, extra = {}) {
    if (!this.active || this.saving || !this.data) return null
    if (model !== null) {
      model = this.data.models.find(m => m.value === model.value)
      if (!model) return null
    }
    if (!modelActionAllowed(this.data, action, model)) return null
    const data = this.data
    const key = model?.value || ""
    let payload = action === "jetlink" ? extra : {}
    if (action.startsWith("select-")) payload = { profile: action === "select-big" ? "big" : "small", model: key }
    else if (["favorite", "unfavorite"].includes(action)) {
      const userFavorites = data.models.filter(m => m.userFavorite && m.value !== key).map(m => m.value)
      if (action === "favorite") userFavorites.push(key)
      payload = { userFavorites }
    } else if (["exclude", "include"].includes(action)) {
      const blacklistedModels = (data.blacklistedModels || []).filter(value => value !== key)
      if (action === "exclude") blacklistedModels.push(key)
      payload = { blacklistedModels }
    } else if (["enable-randomizer", "disable-randomizer"].includes(action)) payload = { randomizer: action === "enable-randomizer" }
    else if (["download", "delete"].includes(action)) payload = { model: key }
    if (["download", "downloadAll"].includes(action)) payload.allowGpuWithoutGpu = extra.allowGpuWithoutGpu === true
    if (typeof data.view === "string") payload.view = data.view
    this.saving = true
    this.data = null
    const result = await this.requestJson(actionPaths[action], payload)
    if (!this.active) return null
    this.saving = false
    const error = this.lastError
    await this.load()
    if (error && this.active) this.publish({ loading: false, data: this.data, error })
    return result
  }
}

// Model Laboratory: one model steers, another paces, via ./api/models/laboratory.
export const laboratoryModels = data => data?.models || []
export const laboratoryReady = data => laboratoryModels(data).filter(m => m.modelLabEligible === true && m.modelLabArtifactAvailable === true && m.modelLabArtifactInstalled === true)

export const laboratoryStatusLabel = model => ({
  unsupported: "Not supported", unpublished: "Not published", checking: "Checking download",
  missing: "Not downloaded", "runtime-unavailable": "Downloaded", "gpu-unavailable": "Connect Chestnut",
  installed: "Downloaded",
})[model.modelLabStatus] || (model.modelLabArtifactInstalled ? "Downloaded" : "Not downloaded")

export function laboratorySelectionError(data, configuration) {
  if (!data) return "Waiting for parked device status"
  if (data.isOnroad !== false) return "Turn off the vehicle before changing the model pair"
  if (!data.runtimeSupported) return data.runtimeUnavailableReason || "Model Laboratory runtime is unavailable"
  if (!data.chestnutReady) return "Connect a firmware-ready Chestnut to enable a pair"
  const ready = laboratoryReady(data)
  if (ready.length < 2) return "Download at least two eGPU variants to compose a pair"
  if (!configuration.lateralModel || !configuration.longitudinalModel) return "Choose two downloaded eGPU variants"
  if (configuration.lateralModel === configuration.longitudinalModel) return "Choose two different models"
  if (![configuration.lateralModel, configuration.longitudinalModel].every(id => ready.some(m => m.value === id))) return "Both models must have downloaded eGPU variants"
  const selected = ready.filter(m => [configuration.lateralModel, configuration.longitudinalModel].includes(m.value))
  if (selected.every(m => m.small === false)) return "Two Big models cannot run together at the required speed. Choose a Small model for one role."
  if (selected.every(m => m.modelLabChestnut === false)) return "Choose at least one model with a Chestnut variant"
  return ""
}

export function laboratoryActionAllowed(data, action, model, configuration = data?.configuration) {
  if (!data || data.capabilities?.[["enable", "disable", "save"].includes(action) ? "configure" : action] !== true) return false
  if (action === "cancel") return data.download?.downloading === true && typeof data.download.jobId === "string"
  if (data.isOnroad !== false || data.download?.downloading) return false
  if (action === "enable") return !laboratorySelectionError(data, configuration)
  if (action === "disable") return data.configuration.enabled === true
  if (action === "download") return laboratoryModels(data).some(m => m.value === model?.value && m.modelLabEligible === true && m.modelLabArtifactAvailable === true && !m.modelLabArtifactInstalled)
  if (action === "delete") return model?.small === true && model.modelLabChestnut !== false && laboratoryReady(data).some(m => m.value === model?.value) &&
    !(data.configuration.enabled && [data.configuration.lateralModel, data.configuration.longitudinalModel].includes(model.value))
  if (action === "save") {
    if (data.configuration.enabled) return !laboratorySelectionError(data, configuration)
    const ready = laboratoryReady(data)
    const picks = [configuration?.lateralModel, configuration?.longitudinalModel]
    return picks.every(id => id === "" || ready.some(m => m.value === id)) && (picks[0] === "" || picks[0] !== picks[1])
  }
  return action === "refresh"
}

export function laboratoryDraft(data, draft, dirty) {
  const configuration = { ...data.configuration, ...(dirty ? { lateralModel: draft.lateralModel, longitudinalModel: draft.longitudinalModel } : {}) }
  if (!dirty) {
    const ready = laboratoryReady(data)
    if (!configuration.lateralModel) configuration.lateralModel = ready[0]?.value || ""
    if (!configuration.longitudinalModel) configuration.longitudinalModel = ready.find(m => m.value !== configuration.lateralModel)?.value || ""
  }
  return configuration
}

function validLaboratoryPayload(data) {
  return data?.schemaVersion === 1 && typeof data.isOnroad === "boolean" && typeof data.chestnutReady === "boolean" &&
    typeof data.runtimeSupported === "boolean" && typeof data.configuration?.enabled === "boolean" &&
    [data.configuration.lateralModel, data.configuration.longitudinalModel].every(id => typeof id === "string") &&
    data.capabilities && typeof data.capabilities === "object" && typeof data.download?.downloading === "boolean" &&
    typeof data.runtime?.active === "boolean" && typeof data.runtime?.requested === "boolean" && Array.isArray(data.models) &&
    data.models.every(m => typeof m?.value === "string" && typeof m.label === "string") &&
    new Set(data.models.map(m => m.value)).size === data.models.length
}

export class LaboratoryFeed extends ModelManagerFeed {
  stop() {
    if (this.expiry != null) this.cancel(this.expiry)
    this.expiry = null
    super.stop()
  }
  async load() {
    if (!this.active || this.saving) return null
    const data = await this.requestJson("laboratory")
    if (!this.active) return null
    if (data) {
      this.data = validLaboratoryPayload(data) ? data : null
      this.publish({ loading: false, data: this.data, error: this.data ? "" : "Invalid Model Laboratory response" })
      if (this.expiry != null) this.cancel(this.expiry)
      this.expiry = this.later(() => {
        this.data = null
        this.publish({ loading: false, data: null, error: "Laboratory status refresh delayed" })
      }, 6000)
    }
    if (this.poll === null) this.poll = this.later(() => this.load(), 2000)
    return this.data
  }
  async action(action, model = null, configuration) {
    if (!this.active || this.saving || !laboratoryActionAllowed(this.data, action, model, configuration)) return null
    const paths = { enable: "laboratory", disable: "laboratory", save: "laboratory", download: "laboratory/download", delete: "laboratory/delete", cancel: "cancel", refresh: "refresh_manifest" }
    const pair = { lateralModel: configuration?.lateralModel, longitudinalModel: configuration?.longitudinalModel }
    const payload = action === "enable" || action === "disable" ? { ...pair, enabled: action === "enable" } :
      action === "save" ? { ...pair, enabled: this.data.configuration.enabled } :
      action === "cancel" ? { jobId: this.data.download.jobId } : model ? { model: model.value } : {}
    this.saving = true
    const result = await this.requestJson(paths[action], payload)
    if (!this.active) return null
    this.saving = false
    const error = this.lastError
    await this.load()
    if (error && this.active) this.publish({ loading: false, data: this.data, error })
    return result
  }
}

const LAB_CHOICE_PROBLEMS = new Set(["Choose two different models", "Both models must have downloaded eGPU variants", "Choose two downloaded eGPU variants",
  "Two Big models cannot run together at the required speed. Choose a Small model for one role.", "Choose at least one model with a Chestnut variant"])

export function matchesSearch(model, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return true
  const text = [model?.label, model?.value, model?.series, model?.version, model?.released].filter(Boolean).join(" ").toLowerCase()
  return words.every(word => text.includes(word))
}

const modelBytes = model => bytes(model?.fileSizeBytes) || bytes(model?.declaredSizeBytes || model?.artifactSize) || 0
export const MODEL_SORTS = Object.freeze({
  release_date: (a, b) => (Date.parse(b.released) || 0) - (Date.parse(a.released) || 0),
  alphabetical: () => 0,
  size: (a, b) => modelBytes(b) - modelBytes(a),
})

const LAB_REASON_STATES = new Set(["unsupported", "unpublished", "checking"])

const ModelDownloadProgress = {
  props: { label: { type: String, required: true }, percent: { type: Number, default: null }, gradientId: { type: String, required: true } },
  template: `<div class="gx-pair-ring" role="progressbar" :aria-label="label" aria-valuemin="0" aria-valuemax="100"
    :aria-valuenow="percent ?? undefined" :title="percent === null ? label : label + ': ' + percent + '%'">
    <svg class="gx-pair-ring__svg" viewBox="-.8 -.8 17.6 17.6" aria-hidden="true">
      <defs>
        <linearGradient :id="gradientId" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style="stop-color: var(--secondary, #38bdf8)" />
          <stop offset="100%" style="stop-color: var(--primary, #9d72ff)" />
        </linearGradient>
      </defs>
      <path class="gx-pair-ring__track" d="M4.406 3.342A5.53 5.53 0 0 1 8 2c2.69 0 4.923 2 5.166 4.579C14.758 6.804 16 8.137 16 9.773 16 11.569 14.502 13 12.687 13H3.781C1.708 13 0 11.366 0 9.318c0-1.763 1.266-3.223 2.942-3.593.143-.863.698-1.723 1.464-2.383z" />
      <path class="gx-pair-ring__fill" pathLength="100" d="M4.406 3.342A5.53 5.53 0 0 1 8 2c2.69 0 4.923 2 5.166 4.579C14.758 6.804 16 8.137 16 9.773 16 11.569 14.502 13 12.687 13H3.781C1.708 13 0 11.366 0 9.318c0-1.763 1.266-3.223 2.942-3.593.143-.863.698-1.723 1.464-2.383z" :stroke="'url(#' + gradientId + ')'" />
    </svg>
    <span v-if="percent !== null" class="gx-pair-ring__text">{{ percent }}%</span>
  </div>`,
}

export const ModelsPage = {
  name: "ModelsPage",
  components: { GxDialog, GxState, GalaxySelect, GxNotice, ModelDownloadProgress },
  props: {
    mode: { type: String, required: true },
    unauthorized: { type: Function, required: true },
    focus: { type: String, default: "" },
  },
  data() {
    return {
      polarisAbout: false, loading: true, error: "", message: "", trackingProgress: false, busy: "", selectionUncertain: true, disposed: false,
      sortMode: "release_date", userFilter: "all", communityFilter: "all", tab: readTab(), pairableFilter: false, search: "", failureDismissed: false,
      models: [], currentModel: "", activeSmallModel: "", activeBigModel: "", capabilities: {},
      summary: { installed: 0, missing: 0, total: 0 }, status: { isOnroad: true },
      runtime: { status: "idle", data: null, error: "" }, dialog: null, pendingChoice: null,
      lab: null, labError: "", labDirty: false, labConfig: { enabled: false, lateralModel: "", longitudinalModel: "" }, focused: false,
      pairView: null,
    }
  },
  computed: {
    currentLabel() { return this.models.find(m => m.value === this.currentModel)?.label || this.currentModel || "" },
    runningModelLabel() {
      if (!this.selectionUncertain && this.status.jetlink?.active) return "Jetlink remote model"
      if (this.runtime.status === "unavailable" || this.runtime.status === "stale") return "Status unavailable"
      if (this.runtime.status !== "ready" || !this.runtime.data) return "Checking…"
      const model = this.runtime.data
      const pair = model.laboratory
      if (pair?.active) return [pair.lateralModel, pair.longitudinalModel].map(id => model.catalog.find(m => m.id === id)?.name || id).join("/")
      if (pair?.lateralModel) return "Laboratory output unavailable"
      if (model.health === "unavailable") return "No running model"
      if (!model.loadedId) return model.health === "loading" ? "Loading…" : "Identity unverified"
      return model.catalog.find(item => item.id === model.loadedId)?.name || model.loadedId
    },
    pendingNextStart() {
      const data = this.runtime.status === "ready" ? this.runtime.data : null
      return data?.pendingNextStart === true || data?.laboratory?.pendingNextStart === true
    },
    laboratoryEnabled() { return !this.selectionUncertain && this.status.laboratory?.enabled === true },
    installedSmallModels() { return this.models.filter(m => (m.value === this.activeSmallModel || m.selectable) && fitsModelProfile(m, "small")) },
    installedBigModels() { return this.models.filter(m => (m.value === this.activeBigModel || m.selectable) && fitsModelProfile(m, "big")) },
    // the manager words it: under Jetlink, the Jetlink app's choice
    bigNoneLabel() { return this.status.bigNoneLabel || "None — always use Active Small" },
    activeSmallLabel() {
      const id = this.pendingChoice?.action === "select-small" ? this.pendingChoice.value : this.activeSmallModel
      return this.models.find(m => m.value === id)?.label || id || "None selected"
    },
    activeBigLabel() {
      const id = this.pendingChoice?.action === "select-big" ? this.pendingChoice.value : this.activeBigModel
      return id ? (this.models.find(m => m.value === id)?.label || id) : this.bigNoneLabel
    },
    libraryTab() { return TABS.has(this.tab) ? this.tab : "both" },
    tabs() {
      const gpu = this.models.filter(m => m.requiresGpu === true).length
      return [{ id: "comma", long: "On the comma", short: "Comma", count: this.models.length - gpu },
        { id: "gpu", long: "Needs GPU or Jetlink", short: "GPU", count: gpu },
        { id: "both", long: "All", short: "All", count: this.models.length }]
    },
    sorted() {
      return this.models.filter(m => matchesHardware(m, this.libraryTab))
        .filter(m => this.userFilter === "all" || !!m.userFavorite === (this.userFilter === "yes"))
        .filter(m => this.communityFilter === "all" || !!m.communityFavorite === (this.communityFilter === "yes"))
        .filter(m => !this.pairableFilter || this.isPairable(m))
        .filter(m => matchesSearch(m, this.search))
        .sort((a, b) => (MODEL_SORTS[this.sortMode]?.(a, b) || 0) ||
          (a.label || a.value).localeCompare(b.label || b.value, undefined, { sensitivity: "base", numeric: true }))
    },
    progressInfo() {
      const info = describeProgress(this.status, this.models)
      if (this.status.downloading === true) return this.status.refreshing === true && !this.status.downloadProgress ? { ...info, indeterminate: true, title: "Checking the model catalog" } : info
      return info.failed && !this.failureDismissed ? info : null
    },
    progressTitle() {
      const info = this.progressInfo
      if (!info) return ""
      if (info.failed) return info.name ? `Couldn’t download ${info.name}` : "Download stopped"
      return info.title || (info.name ? `Downloading ${info.name}` : "Downloading")
    },
    retryDownloadRequest() {
      if (this.busy) return null
      const detail = this.status.downloadProgress
      const model = this.models.find(m => m.value === detail?.model)
      if (this.status.downloadVariant === "amd") {
        const row = this.labRow(model)
        return row && this.labActionAvailable("download", row) ? { laboratory: true, action: "download", model: row } : null
      }
      const action = detail?.count > 1 || !model ? "downloadAll" : "download"
      return this.actionAvailable(action, model) ? { action, model } : null
    },
    cardProgressLabel() {
      const info = this.progressInfo
      if (!info) return ""
      if (info.indeterminate) return info.title || "Working…"
      return [`Downloading ${info.percent ?? 0}%`, info.rate, info.etaShort].filter(Boolean).join(" · ")
    },
    cancelLabel() {
      const place = this.progressInfo?.queue?.replace(/^Model /, "")
      return place ? `Cancel · ${place}` : "Cancel download"
    },
    gpuNote() {
      if (this.status.jetlinkBig === true) return "Jetlink runs Active Big on its host, which downloads the model itself. A model chosen in the Jetlink app shows here once the comma connects."
      return this.status.gpuAvailable ? "" : "An external GPU is not detected. Active Small is used; your Big selection is saved for later."
    },
    jetlinkOpen() { return !!this.status.jetlink && this.status.jetlink.mode !== "off" },
    driveMode() { return this.pairView ?? (this.lab?.configuration.enabled ? "pair" : "single") },
    labRows() { return new Map(laboratoryModels(this.lab).map(m => [m.value, m])) },
    labReady() { return laboratoryReady(this.lab) },
    labCandidates() { return this.labReady.filter(m => m.value !== this.labConfig.lateralModel) },
    labSelectionError() { return laboratorySelectionError(this.lab, this.labConfig) },
    labRuntimeState() { return !this.lab ? "Status unavailable" : !this.lab.runtimeSupported ? "Unavailable" : this.lab.runtime.active ? "Pair active" : this.lab.runtime.requested ? "Pair requested" : "Inactive" },
    labChoiceProblem() { return LAB_CHOICE_PROBLEMS.has(this.labSelectionError) },
    labLocked() { return !this.lab || this.lab.isOnroad || this.lab.download?.downloading },
  },
  watch: {
    "status.downloading"(value) { if (value) this.failureDismissed = false },
    loading(value) { if (!value) this.focusLaboratory() },
  },
  created() {
    this.feed = new ModelStatusFeed({ publish: update => { this.runtime = update }, unauthorized: this.unauthorized })
    this.manager = new ModelManagerFeed({ publish: update => {
      this.loading = update.loading
      this.error = update.error
      this.selectionUncertain = !update.data
      this.capabilities = update.data?.capabilities || {}
      if (!update.data && this.status.jetlink) this.status = { ...this.status, jetlink: { ...this.status.jetlink, active: false, state: "unavailable" } }
      if (update.data) {
        const p = update.data
        this.models = p.models
        this.currentModel = p.currentModel || ""
        this.activeSmallModel = p.activeSmallModel || ""
        this.activeBigModel = p.activeBigModel || ""
        this.summary = p.summary || { installed: 0, missing: 0, total: 0 }
        this.status = p
        if (this.trackingProgress && p.progress) {
          this.message = p.progress
          if (!p.downloading) this.trackingProgress = false
        }
      }
    }, unauthorized: this.unauthorized })
    this.laboratory = new LaboratoryFeed({ publish: update => {
      this.labError = update.error
      this.lab = update.data
      if (update.data) this.labConfig = laboratoryDraft(update.data, this.labConfig, this.labDirty)
    }, unauthorized: this.unauthorized })
  },
  mounted() {
    this.visibility = () => {
      if (document.hidden) { this.finishDialog(false); this.feed.stop(); this.manager.stop(); this.laboratory?.stop() }
      else if (this.mode === "local") { this.feed.start(); this.manager.start(); this.laboratory?.start() }
    }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local" && !document.hidden) { this.feed.start(); this.manager.start(); this.laboratory?.start() }
  },
  beforeUnmount() {
    this.disposed = true
    this.finishDialog(false)
    document.removeEventListener("visibilitychange", this.visibility)
    this.feed.stop()
    this.manager.stop()
    this.laboratory?.stop()
  },
  methods: {
    focusLaboratory() {
      if (this.focus !== "laboratory" || this.focused) return
      if (this.driveMode !== "pair") this.pairView = "pair"
      this.$nextTick(() => {
        const card = this.$refs.laboratory
        if (!card) return
        this.focused = true
        card.scrollIntoView({ block: "start" })
      })
    },
    labRow(model) { return this.labRows.get(model?.value) },
    isPairable(model) { return this.labRow(model)?.modelLabArtifactAvailable === true },
    labStatusLabel: laboratoryStatusLabel,
    labReason(model) { const row = this.labRow(model); return row && LAB_REASON_STATES.has(row.modelLabStatus) ? row.modelLabReason : "" },
    pairKind(row) { return row.small ? "eGPU variant" : "Big model" },
    labDownloadLabel(m) { return m.small ? (m.modelLabChestnut === false ? "Download Small model" : "Download pair variants") : "Download Big model" },
    labModelLabel(id) { return this.lab?.models.find(m => m.value === id)?.label || id || "Not selected" },
    labActionAvailable(action, model = null) { return laboratoryActionAllowed(this.lab, action, model, this.labConfig) },
    changeLateral(value) {
      this.labConfig.lateralModel = value
      if (this.labConfig.longitudinalModel === value) this.labConfig.longitudinalModel = this.labCandidates[0]?.value || ""
      this.labDirty = true
      return this.saveLabPair()
    },
    changeLongitudinal(value) {
      this.labConfig.longitudinalModel = value
      this.labDirty = true
      return this.saveLabPair()
    },
    async saveLabPair() {
      if (!this.labActionAvailable("save")) return null
      const result = await reportSave(() => this.runLabAction("save"))
      if (!result && !this.disposed) {
        this.labDirty = false
        if (this.lab) this.labConfig = laboratoryDraft(this.lab, this.labConfig, false)
      }
      if (result && !this.disposed && this.driveMode === "pair" && !this.lab?.configuration.enabled && this.labActionAvailable("enable")) return this.enablePair()
      return result
    },
    enablePair() { return reportSave(() => this.runLabAction("enable")) },
    async setDriveMode(mode) {
      if (mode === this.driveMode) return null
      if (mode === "single") {
        this.pairView = "single"
        const result = this.lab?.configuration.enabled ? await reportSave(() => this.runLabAction("disable")) : true
        this.pairView = null
        return result
      }
      this.pairView = "pair"
      return this.labActionAvailable("enable") ? this.enablePair() : null
    },
    activeRoles(model) {
      const roles = []
      if (model.value === this.activeSmallModel) roles.push("Active Small")
      if (model.value === this.activeBigModel) roles.push("Active Big")
      if (!roles.length && model.value === this.currentModel) roles.push("Active")
      return roles
    },
    activeButtonLabel(model) {
      return this.activeRoles(model).join(" · ") || "Active"
    },
    pairRoles(model) {
      const pair = this.lab?.configuration
      if (!pair) return []
      return [[pair.lateralModel, "Pair · lateral"], [pair.longitudinalModel, "Pair · longitudinal"]]
        .filter(([id]) => id && id === model.value).map(([, role]) => role)
    },
    showPairable() {
      this.pairableFilter = true
      this.setTab("comma")
      this.$nextTick(() => this.$refs.library?.scrollIntoView({ block: "start" }))
    },
    clearFilters() {
      Object.assign(this, { search: "", pairableFilter: false, userFilter: "all", communityFilter: "all" })
      this.setTab("both")
    },
    async runLabAction(action, model = null) {
      if (this.disposed || this.busy || !this.labActionAvailable(action, model)) return
      this.busy = `lab-${action}:${model?.value || ""}`
      this.message = ""
      try {
        if (action === "delete" && !await this.confirmAction({ title: "Delete eGPU variant", confirmLabel: "Delete", danger: true,
          message: `Delete the eGPU variant for "${model.label}"? The normal on-device model will not be removed.` })) return
        if (this.disposed) return
        const result = await this.laboratory.action(action, model, this.labConfig)
        if (!this.disposed && result) {
          this.message = action === "save" ? "Model pair saved for the next start." : result.message || "Updated"
          this.trackingProgress = action === "download"
          if (["enable", "disable", "delete", "save"].includes(action)) {
            this.labDirty = false
            if (this.lab) this.labConfig = laboratoryDraft(this.lab, this.labConfig, false)
          }
          this.manager?.load()
        }
        return result
      } finally { if (!this.disposed) this.busy = "" }
    },
    isPolaris(model) {
      const series = typeof model?.series === "string" ? model.series.trim().toLowerCase() : ""
      return series === "polaris" || series === "polaris series" || ["spz_polaris_r3a", "spz_polaris_r3b"].includes(model?.value)
    },
    polarisTitleParts(model) {
      const label = model.label || model.value
      const emoji = label.search(/\p{Extended_Pictographic}/u)
      return emoji < 0 ? [label, ""] : [label.slice(0, emoji), label.slice(emoji)]
    },
    hardwareLabel, hardwareTag, fileSizeText, releasedText, modelFacts,
    // "active" while this model downloads, "queued" while Download Missing works through the rest
    downloadStage(model) {
      if (!this.status.downloading) return ""
      if (this.status.modelToDownload === model.value) return "active"
      if (!this.status.downloadAll || model.installed || !model.downloadAvailable) return ""
      const wanted = this.models.filter(m => !m.installed && m.downloadAvailable).length
      return model.gpuAvailable === false && this.status.downloadProgress?.count < wanted ? "" : "queued"
    },
    isPairDownloading(model) {
      if (!model || !this.lab?.download?.downloading) return false
      const row = this.labRow(model)
      const variant = row?.small && row.modelLabChestnut !== false ? "amd" : "standard"
      return this.lab.download.variant === variant && this.lab.download.model === model.value
    },
    pairDownloadPercent(model) {
      if (!this.isPairDownloading(model)) return null
      if (this.progressInfo?.percent != null) return this.progressInfo.percent
      const match = /(\d{1,3})%/.exec(this.lab?.download?.progress || "")
      return match ? Math.min(100, Number(match[1])) : null
    },
    setTab(value) { this.tab = saveTab(value) },
    retryDownload() {
      const request = this.retryDownloadRequest
      if (!request) return null
      return request.laboratory ? this.runLabAction(request.action, request.model) : this.runAction(request.action, request.model)
    },
    actionAvailable(action, model = null) { return !this.selectionUncertain && modelActionAllowed(this.status, action, model) },
    canAction(action, model = null) { return !this.busy && this.actionAvailable(action, model) },
    rowState(model) {
      if ([this.currentModel, this.activeSmallModel, this.activeBigModel].includes(model.value)) return "active"
      if (this.status.downloading) return this.status.downloadAll || this.status.modelToDownload === model.value ? "cancellable" : "busy"
      return model.installed ? "installed" : "available"
    },
    confirmAction(dialog) {
      return new Promise(resolve => {
        this.dialog = dialog
        this.resolveDialog = resolve
        this.$nextTick(() => this.$refs.confirmButton?.focus())
      })
    },
    finishDialog(value) {
      this.resolveDialog?.(value)
      this.resolveDialog = null
      this.dialog = null
    },
    toggleRandomizer(event) {
      const enabled = event.target.checked
      event.target.checked = this.status.randomizer === true
      return this.runAction(enabled ? "enable-randomizer" : "disable-randomizer")
    },
    toggleJetlinkCharge(event) {
      const enabled = event.target.checked
      event.target.checked = this.status.jetlink.chargePhone === true
      return this.configureJetlink(this.status.jetlink.mode, enabled)
    },
    async configureJetlink(mode, chargePhone = this.status.jetlink?.chargePhone === true) {
      if (!this.canAction("jetlink")) return
      this.pendingChoice = { action: "jetlink", value: mode }
      this.busy = "jetlink:"
      try {
        const result = await reportSave(() => this.manager.action("jetlink", null, { mode, chargePhone }))
        if (result) this.message = result.message
      } finally { this.pendingChoice = null; this.busy = "" }
    },
    async runAction(action, model = null) {
      if (this.disposed || !this.canAction(action, model)) return
      if (action.startsWith("select-")) this.pendingChoice = { action, value: model?.value || "" }
      this.busy = `${action}:${model?.value || ""}`
      this.message = ""
      this.trackingProgress = false
      try {
        let allowGpuWithoutGpu = false
        if (action === "enable-randomizer" && !await this.confirmAction({ title: "Model Randomizer", message: "Choose a different installed, verified model each start. Excluded models are skipped. Favorites do not change the random pool.", confirmLabel: "Enable" })) return
        if (action === "delete" && !await this.confirmAction({ title: "Delete Model", message: `Delete local files for "${model.label}"?`, confirmLabel: "Delete", danger: true })) return
        const needsGpu = action === "download" ? model.requiresGpu && !model.gpuAvailable : action === "downloadAll" &&
          this.models.some(m => !m.installed && m.requiresGpu && !m.gpuAvailable && m.downloadAvailable === true)
        if (needsGpu) {
          allowGpuWithoutGpu = await this.confirmAction({ title: "No External GPU Detected", confirmLabel: "Download Anyway",
            message: "These model files require an external GPU. You can download them now, but they cannot run until a compatible GPU is connected and detected. GPU model files can be large. Downloading does not activate a model." })
          if (!allowGpuWithoutGpu) return
        }
        if (this.disposed) return
        const perform = () => this.manager.action(action, model, { allowGpuWithoutGpu })
        const result = await (["enable-randomizer", "disable-randomizer", "include", "exclude"].includes(action) ? reportSave(perform) : perform())
        if (!this.disposed && result) {
          this.trackingProgress = ["download", "downloadAll", "refresh"].includes(action)
          this.message = (this.trackingProgress && this.status.progress) || result.message ||
            (action.startsWith("select-") ? "Selection saved for the next onroad cycle." : "Saved.")
          if (["download", "downloadAll", "cancel", "delete", "refresh"].includes(action)) this.laboratory?.load()
        }
      } finally { if (!this.disposed) { this.pendingChoice = null; this.busy = "" } }
    },
  },

  template: `
    <div class="gx-view gx-model-manager" :inert="!!busy" :aria-busy="!!busy || undefined">
      <GxState v-if="mode !== 'local'">Models are unavailable in the offline preview.</GxState>
      <GxState v-else-if="loading" loading>Reading installed models and available downloads…</GxState>

      <template v-else>
        <header class="gx-settings__header gx-page-header"><div><h2>Models</h2>
          <p>Choose what drives your car, pair models, and manage downloads.</p></div></header>
        <GxNotice v-if="message" tone="info">{{ message }}</GxNotice>
        <GxNotice v-if="error" tone="danger" title="Models">{{ error }}</GxNotice>
        <GxNotice v-if="status.isOnroad" tone="warn">Selections apply next onroad cycle. Park before downloading models or changing Jetlink.</GxNotice>

        <GxNotice v-if="progressInfo && progressInfo.failed" tone="danger" :title="progressTitle">
          {{ progressInfo.error || 'The download stopped.' }}<template v-if="progressInfo.percent !== null"> It stopped at {{ progressInfo.percent }}%.</template>
          <template #actions>
            <button type="button" class="gx-btn" :disabled="!retryDownloadRequest" @click="retryDownload()"><i aria-hidden="true" class="bi bi-arrow-repeat"></i> Try again</button>
            <button type="button" class="gx-btn gx-btn--tonal" @click="failureDismissed = true">Dismiss</button>
          </template>
        </GxNotice>


        <section class="gx-card gx-model-driving">
          <div class="gx-panel gx-model-driving__body">
            <div class="gx-tabs gx-model-mode" role="group" aria-label="Driving models">
              <button type="button" :aria-pressed="driveMode === 'single'" :disabled="!!busy || labLocked && driveMode !== 'single'" @click="setDriveMode('single')"><i aria-hidden="true" class="bi bi-cpu"></i> Single model</button>
              <button type="button" :aria-pressed="driveMode === 'pair'" :disabled="!!busy || labLocked" @click="setDriveMode('pair')"><i aria-hidden="true" class="bi bi-bezier2"></i> Model pair</button>
            </div>

            <div class="gx-panel-stage" :style="{'--panel-direction': driveMode === 'pair' ? 1 : -1}">
              <Transition name="gx-panel" @before-enter="$event.inert = false" @before-leave="$event.inert = true">
              <div :key="driveMode" class="gx-model-driving__body">
            <template v-if="driveMode === 'single'">
              <div class="gx-model-driving__summary" role="group" aria-label="Active models">
                <div class="gx-model-control gx-model-summary-item">
                  <span class="gx-model-summary-item__meta"><i aria-hidden="true" class="bi bi-cpu"></i> Active Small · On-device</span>
                  <strong class="gx-model-summary-item__value">{{ activeSmallLabel }}</strong>
                </div>
                <div class="gx-model-control gx-model-summary-item">
                  <span class="gx-model-summary-item__meta"><i aria-hidden="true" class="bi bi-gpu-card"></i> Active Big · External</span>
                  <div class="gx-model-summary-item__row">
                    <strong v-if="activeBigModel" class="gx-model-summary-item__value">{{ activeBigLabel }}</strong>
                    <strong v-else class="gx-model-summary-item__value gx-model-summary-item__value--muted">{{ bigNoneLabel }}</strong>
                    <button v-if="activeBigModel" type="button" class="gx-icon-btn gx-model-summary-item__clear" :disabled="selectionUncertain || !actionAvailable('select-big')" aria-label="Clear Active Big model" title="Clear Active Big (use Active Small)" @click="runAction('select-big')"><i aria-hidden="true" class="bi bi-x-lg"></i></button>
                  </div>
                </div>
              </div>
              <div class="gx-model-driving__foot">
                <div class="gx-row gx-row--borderless gx-model-driving__random" :class="{disabled: !actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')}">
                  <span class="gx-row__info gx-row__label"><i aria-hidden="true" class="bi bi-shuffle"></i> Model Randomizer <small class="gx-model-driving__random-hint">tries a different model each drive</small></span>
                  <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Model Randomizer" :checked="status.randomizer === true" :disabled="!actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')" @change="toggleRandomizer($event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
                </div>
                <span class="gx-chip gx-model-driving__running">Running now: {{ runningModelLabel }}<template v-if="pendingNextStart"> · saved changes pending next start</template></span>
              </div>
              <p v-if="status.randomizer" class="gx-model-reason">A different verified model is chosen each start. {{ status.gpuAvailable && activeBigModel ? 'Big GPU' : 'Small on-device' }} models are used. {{ (status.blacklistedModels || []).length }} excluded. Favorites only mark your preferred models.</p>
              <p v-if="gpuNote" class="gx-model-reason">{{ gpuNote }}</p>
              <p class="gx-model-reason">{{ laboratoryEnabled ? 'Small/Big selections are saved as fallback choices. Turn off Laboratory to use them normally at the next start.' : 'Selections take effect the next time the driving model starts.' }}</p>
            </template>

            <div v-else id="gx-model-laboratory" ref="laboratory" class="gx-model-laboratory">
              <div class="gx-model-driving__foot">
                <div class="gx-actions gx-tags">
                  <span class="gx-chip">{{ lab ? (lab.chestnutReady ? 'Chestnut ready' : 'Chestnut required') : 'Waiting for device status' }}</span>
                  <span v-if="lab" class="gx-chip" :class="{'gx-chip--selected': labRuntimeState === 'Pair active'}">{{ labRuntimeState }}</span>
                </div>
                <span class="gx-chip gx-model-driving__running">Running now: {{ runningModelLabel }}<template v-if="pendingNextStart"> · saved changes pending next start</template></span>
              </div>
              <GxNotice v-if="labError" tone="danger" title="Model Laboratory">{{ labError }}</GxNotice>
              <GxNotice v-if="lab?.configurationError" tone="danger">{{ lab.configurationError }}</GxNotice>
              <p v-if="!lab && !labError" class="gx-note">Loading Model Laboratory…</p>
              <template v-if="lab">
                <p class="gx-model-reason">Pair lateral judgment from one model with longitudinal judgment from another.</p>
                <div class="gx-model-driving__selects">
                  <div class="gx-model-control">
                    <label class="gx-row__label" for="gx-lab-lateral">Lateral model <small class="gx-model-driving__random-hint">steering &amp; path</small></label>
                    <GalaxySelect id="gx-lab-lateral" class="gx-field" aria-label="Lateral model" :disabled="labLocked" :value="labConfig.lateralModel" @change="changeLateral($event.target.value)">
                      <option value="">Choose a model</option>
                      <option v-for="m in labReady" :key="m.value" :value="m.value">{{ m.label }} · {{ m.version }}</option>
                    </GalaxySelect>
                  </div>
                  <div class="gx-model-control">
                    <label class="gx-row__label" for="gx-lab-longitudinal">Longitudinal model <small class="gx-model-driving__random-hint">speed &amp; lead pacing</small></label>
                    <GalaxySelect id="gx-lab-longitudinal" class="gx-field" aria-label="Longitudinal model" :disabled="labLocked" :value="labConfig.longitudinalModel" @change="changeLongitudinal($event.target.value)">
                      <option value="">Choose a model</option>
                      <option v-for="m in labCandidates" :key="m.value" :value="m.value">{{ m.label }} · {{ m.version }}</option>
                    </GalaxySelect>
                  </div>
                </div>
                <p class="gx-model-reason">{{ labModelLabel(labConfig.lateralModel) }} steers · {{ labModelLabel(labConfig.longitudinalModel) }} paces</p>
                <p class="gx-model-reason">The pair takes effect the next time the driving model starts.</p>
                <p v-if="!lab.runtimeSupported && labSelectionError !== lab.runtimeUnavailableReason" class="gx-model-reason">{{ lab.runtimeUnavailableReason || 'Pair runtime is unavailable.' }}</p>
                <GxNotice v-if="labSelectionError" :tone="labChoiceProblem ? 'danger' : 'info'">{{ labSelectionError }}</GxNotice>
                <GxNotice v-if="lab.runtime.error" tone="danger">{{ lab.runtime.error }}</GxNotice>
                <div class="gx-model-driving__foot">
                  <span class="gx-model-reason">Verified downloads: {{ labReady.length }} of {{ lab.summary?.published || 0 }} published for pairing.</span>
                  <button type="button" class="gx-btn gx-btn--tonal" @click="showPairable"><i aria-hidden="true" class="bi bi-bezier2"></i> Show pairable models</button>
                </div>
                <p class="gx-model-reason">Pairing increases inference time. Two Big models cannot run together at the required speed. Choose a Small model for one role. Other combinations can also cause model lag warnings. When available, the comma GPU runs one Small role while Chestnut runs the other role.</p>
              </template>
            </div>
              </div>
              </Transition>
            </div>
          </div>
        </section>

        <section v-if="status.jetlink" class="gx-card gx-model-jetlink">
          <details :open="jetlinkOpen">
            <summary class="gx-section__header"><i aria-hidden="true" class="bi bi-usb-symbol"></i><span class="gx-section__title">Jetlink</span><span class="gx-section__count">{{ status.jetlink.mode === 'off' ? 'Off' : status.jetlink.state }}</span></summary>
            <div class="gx-panel gx-stack">
              <div class="gx-field-group">
                <label for="gx-jetlink-mode" class="gx-row__label">Remote model connection</label>
                <GalaxySelect id="gx-jetlink-mode" class="gx-field" :value="pendingChoice?.action === 'jetlink' ? pendingChoice.value : status.jetlink.mode" :disabled="!actionAvailable('jetlink')" @change="configureJetlink($event.target.value)">
                  <option value="off">Off</option>
                  <option value="usb" :disabled="!status.jetlink.supported || status.jetlink.chestnut">USB computer</option>
                  <option value="ios" :disabled="!status.jetlink.supported || status.jetlink.chestnut">iPhone / iPad</option>
                </GalaxySelect>
              </div>
              <p class="gx-note" role="status"><span class="gx-chip">{{ status.jetlink.state }}</span><span v-if="status.jetlink.model && !status.jetlink.active"> · selected: {{ status.jetlink.model }}</span></p>
              <p v-if="status.jetlink.progress">{{ status.jetlink.progress }}</p>
              <p v-if="status.jetlink.reason" class="gx-model-reason">{{ status.jetlink.reason }}</p>
              <p v-if="status.jetlink.chestnut" class="gx-model-reason">Chestnut is connected. Jetlink stays off while Chestnut is fitted.</p>
              <p class="gx-model-reason">Prepared means the remote model is built, not active. The local Small model remains available. Small and Chestnut catalog selections are unchanged.</p>
              <div v-if="status.jetlink.mode === 'ios'" class="gx-row" :class="{disabled: !actionAvailable('jetlink')}">
                <span class="gx-row__info gx-row__label">Charge phone over USB</span>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Charge phone over USB" :checked="status.jetlink.chargePhone === true" :disabled="!actionAvailable('jetlink')" @change="toggleJetlinkCharge($event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
              </div>
              <p class="gx-model-reason">Set up parked with a USB 3 data cable and separate power. Keep the computer awake; keep the iPhone app open and unlocked.</p>
              <div class="gx-actions"><a class="gx-btn gx-btn--tonal" href="https://github.com/StarPilotHQ/StarPilot/blob/Dom/openpilot/starpilot/models/JETLINK.md" target="_blank" rel="noopener noreferrer"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open Jetlink setup guide</a></div>
            </div>
          </details>
        </section>

        <section ref="library" class="gx-card gx-model-library">
          <div class="gx-panel gx-model-toolbar">
            <div class="gx-model-searchbar">
              <div class="gx-model-search-wrap">
                <i aria-hidden="true" class="bi bi-search gx-model-search__icon"></i>
                <input class="gx-field gx-model-search" type="search" v-model.trim="search" placeholder="Search models…" aria-label="Search models">
              </div>
              <div class="gx-actions gx-model-toolbar__actions">
                <button v-if="status.downloading" type="button" class="gx-btn gx-btn--danger" :disabled="!actionAvailable('cancel')" @click="runAction('cancel')"><i aria-hidden="true" class="bi bi-stop-circle"></i> {{ cancelLabel }}</button>
                <button v-else type="button" class="gx-btn" :disabled="!actionAvailable('downloadAll')" @click="runAction('downloadAll')"><i aria-hidden="true" class="bi bi-download"></i> Download all<template v-if="summary.missing"> ({{ summary.missing }})</template></button>
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('refresh')" aria-label="Check for new models" title="Check for new models" @click="runAction('refresh')"><i aria-hidden="true" v-if="busy === 'refresh:' || status.refreshing" class="bi bi-arrow-repeat gx-spin"></i><i aria-hidden="true" v-else class="bi bi-arrow-clockwise"></i> <span class="gx-model-toolbar__label">Check for new models</span></button>
              </div>
            </div>
            <div class="gx-model-toolbar__row">
              <div class="gx-guided__segmented gx-model-tabs" role="radiogroup" aria-label="Show models">
                <label v-for="t in tabs" :key="t.id" class="gx-btn gx-btn--tonal gx-guided__choice">
                  <input class="gx-sr-only" type="radio" name="gx-model-tab" :checked="libraryTab === t.id" @change="setTab(t.id)">
                  <span class="gx-model-tabs__long">{{ t.long }}</span><span class="gx-model-tabs__short">{{ t.short }}</span> <span class="gx-model-tabs__count">{{ t.count }}</span>
                </label>
              </div>
              <div class="gx-settings-tabs gx-model-filters" role="group" aria-label="Sort and filter models">
                <GalaxySelect class="gx-field gx-model-sort" aria-label="Sort models" :value="sortMode" @change="sortMode = $event.target.value">
                  <option value="release_date">Newest first</option>
                  <option value="alphabetical">Name A–Z</option>
                  <option value="size">Largest first</option>
                </GalaxySelect>
                <button type="button" class="gx-chip" :aria-pressed="userFilter === 'yes'" @click="userFilter = userFilter === 'yes' ? 'all' : 'yes'"><i class="bi bi-star" aria-hidden="true"></i> Favorites</button>
                <button type="button" class="gx-chip" :aria-pressed="communityFilter === 'yes'" @click="communityFilter = communityFilter === 'yes' ? 'all' : 'yes'"><i class="bi bi-people" aria-hidden="true"></i> Community</button>
                <button type="button" class="gx-chip" :aria-pressed="pairableFilter" @click="pairableFilter = !pairableFilter"><i class="bi bi-bezier2" aria-hidden="true"></i> Pairable</button>
              </div>
            </div>
          </div>

          <div v-if="!sorted.length" class="gx-empty">No models match these filters. <button type="button" class="gx-btn gx-btn--tonal" @click="clearFilters">Show all models</button></div>
          <ul v-else class="gx-model-list" aria-label="Models">
            <li class="gx-model-item" :class="{'gx-model-item--pair': driveMode === 'pair', 'gx-model-card--polaris': isPolaris(m), 'gx-model-card--selected': rowState(m) === 'active'}" v-for="m in sorted" :key="m.value">
              <template v-if="driveMode === 'pair'">
                <div class="gx-model-item__row gx-model-item__row--pair">
                  <div class="gx-model-item__ver">
                    <span v-if="m.version" class="gx-model-spec gx-model-spec--ver">{{ m.version }}</span>
                  </div>
                  <div class="gx-model-item__main">
                    <div class="gx-model-item__title">
                      <strong v-if="isPolaris(m)" :title="m.value"><span class="gx-model-card__title">{{ polarisTitleParts(m)[0] }}</span><span>{{ polarisTitleParts(m)[1] }}</span></strong>
                      <strong v-else :title="m.value">{{ m.label || m.value }}</strong>
                      <button v-if="isPolaris(m)" type="button" class="gx-icon-btn gx-model-about" aria-label="About Polaris models" title="About Polaris models" @click="polarisAbout = true"><i class="bi bi-question-circle" aria-hidden="true"></i></button>
                    </div>
                    <div class="gx-model-item__meta">
                      <span class="gx-model-item__hw" :class="{'gx-model-item__hw--gpu': m.requiresGpu}" :title="hardwareTag(m).title || null"><i aria-hidden="true" class="bi" :class="m.requiresGpu ? 'bi-gpu-card' : 'bi-cpu'"></i> {{ m.requiresGpu ? 'External' : 'On-device' }}</span>
                      <span v-if="m.builtin" class="gx-model-specs"><span class="gx-model-spec">Built-in</span></span>
                    </div>
                    <div class="gx-model-item__tagstrip">
                      <span v-for="role in pairRoles(m)" :key="role" class="gx-chip">{{ role }}</span>
                      <span v-if="isPairable(m)" class="gx-model-tag gx-model-tag--pair" title="An eGPU variant is published, so this model can be part of a model pair"><i aria-hidden="true" class="bi bi-bezier2"></i> Pairable</span>
                      <span v-else class="gx-chip">Single-model only</span>
                      <span v-if="isPolaris(m)" class="gx-chip gx-polaris-badge"><span class="gx-polaris-badge__star" aria-hidden="true">✦</span> StarPilot Custom</span>
                      <span v-if="m.communityFavorite" class="gx-chip">Community Favorite</span>
                    </div>
                  </div>
                  <div class="gx-model-item__date">{{ releasedText(m.released) }}</div>
                  <div class="gx-model-item__actions gx-model-item__actions--pair">
                    <div class="gx-model-item__icons gx-model-item__icons--pair">
                      <span class="gx-model-item__icon-slot">
                        <ModelDownloadProgress v-if="downloadStage(m) === 'active' && !isPairDownloading(m) && progressInfo"
                          :label="'Downloading ' + m.label" :percent="progressInfo.percent" :gradient-id="'gx-ring-main-' + m.value" />
                        <span v-else-if="m.installed" class="gx-pair-cloud-done" title="Model downloaded">
                          <i aria-hidden="true" class="bi bi-cloud-check-fill"></i>
                        </span>
                        <button v-else-if="m.downloadAvailable" type="button" class="gx-icon-btn gx-pair-cloud-btn" :disabled="!actionAvailable('download', m)" :aria-label="'Download ' + m.label" :title="'Download ' + m.label + ' (' + fileSizeText(m) + ')'" @click="runAction('download', m)">
                          <i aria-hidden="true" class="bi bi-cloud-arrow-down"></i>
                        </button>
                      </span>
                      <span class="gx-model-item__icon-slot">
                        <button v-if="m.installed && !m.builtin" type="button" class="gx-icon-btn gx-text-danger" :disabled="!actionAvailable('delete', m)" :aria-label="'Delete files for ' + m.label" :title="'Delete files for ' + m.label" @click="runAction('delete', m)">
                          <i aria-hidden="true" class="bi bi-trash"></i>
                        </button>
                      </span>
                      <span class="gx-model-item__icon-slot">
                        <button type="button" class="gx-icon-btn" :disabled="!actionAvailable(m.userFavorite ? 'unfavorite' : 'favorite', m)" :aria-label="(m.userFavorite ? 'Remove from your favorites: ' : 'Add to your favorites: ') + m.label" :title="m.userFavorite ? 'Remove from your favorites' : 'Add to your favorites'" @click="runAction(m.userFavorite ? 'unfavorite' : 'favorite', m)">
                          <i aria-hidden="true" class="bi" :class="m.userFavorite ? 'bi-star-fill' : 'bi-star'"></i>
                        </button>
                      </span>
                    </div>
                  </div>
                </div>
                <template v-for="row in [labRow(m)]" :key="m.value + '-pair'">
                  <div v-if="row?.small || row?.modelLabEligible" class="gx-model-item__row gx-model-item__row--pair gx-model-item__subrow">
                    <div class="gx-model-item__ver">
                      <span class="gx-model-item__sub-indent" aria-hidden="true"><i class="bi bi-arrow-return-right"></i></span>
                    </div>
                    <div class="gx-model-item__main">
                      <div class="gx-model-item__title">
                        <span class="gx-model-lab__label"><i aria-hidden="true" class="bi bi-bezier2"></i> {{ pairKind(row) }}</span>
                      </div>
                      <div class="gx-model-item__meta">
                        <span class="gx-model-item__hw gx-model-item__hw--gpu"><i aria-hidden="true" class="bi bi-gpu-card"></i> External</span>
                        <span class="gx-model-specs">
                          <span class="gx-model-spec">For model pair</span>
                          <span v-if="!row.modelLabEligible || !row.modelLabArtifactAvailable" class="gx-model-spec">{{ labStatusLabel(row) }}</span>
                        </span>
                      </div>
                    </div>
                    <div class="gx-model-item__date"></div>
                    <div class="gx-model-item__actions gx-model-item__actions--pair">
                      <div class="gx-model-item__icons gx-model-item__icons--pair">
                        <span class="gx-model-item__icon-slot">
                          <ModelDownloadProgress v-if="isPairDownloading(m)" :label="'Downloading ' + pairKind(row) + ': ' + m.label"
                            :percent="pairDownloadPercent(m)" :gradient-id="'gx-ring-lab-' + m.value" />
                          <button v-else-if="row.modelLabEligible && row.modelLabArtifactAvailable && !row.modelLabArtifactInstalled" type="button" class="gx-icon-btn gx-pair-cloud-btn" :disabled="!labActionAvailable('download', row)" :aria-label="labDownloadLabel(row) + ': ' + m.label" :title="labDownloadLabel(row) + ': ' + m.label" @click="runLabAction('download', row)">
                            <i aria-hidden="true" class="bi bi-cloud-arrow-down"></i>
                          </button>
                          <span v-else-if="row.modelLabArtifactInstalled" class="gx-pair-cloud-done" :title="pairKind(row) + ' downloaded'">
                            <i aria-hidden="true" class="bi bi-cloud-check-fill"></i>
                          </span>
                        </span>
                        <span class="gx-model-item__icon-slot">
                          <button v-if="row.small && row.modelLabChestnut !== false && row.modelLabArtifactInstalled" type="button" class="gx-icon-btn gx-text-danger" :disabled="!labActionAvailable('delete', row)" :aria-label="'Delete eGPU variant: ' + m.label" title="Delete eGPU variant" @click="runLabAction('delete', row)">
                            <i aria-hidden="true" class="bi bi-trash"></i>
                          </button>
                        </span>
                        <span class="gx-model-item__icon-slot"></span>
                      </div>
                    </div>
                  </div>
                </template>
              </template>
              <div v-else class="gx-model-item__row">
                <div class="gx-model-item__ver">
                  <span v-if="m.version" class="gx-model-spec gx-model-spec--ver">{{ m.version }}</span>
                </div>
                <div class="gx-model-item__main">
                  <div class="gx-model-item__title">
                    <strong v-if="isPolaris(m)" :title="m.value"><span class="gx-model-card__title">{{ polarisTitleParts(m)[0] }}</span><span>{{ polarisTitleParts(m)[1] }}</span></strong>
                    <strong v-else :title="m.value">{{ m.label || m.value }}</strong>
                    <span v-if="isPolaris(m)" class="gx-chip gx-polaris-badge"><span class="gx-polaris-badge__star" aria-hidden="true">✦</span> StarPilot Custom</span>
                    <button v-if="isPolaris(m)" type="button" class="gx-icon-btn gx-model-about" aria-label="About Polaris models" title="About Polaris models" @click="polarisAbout = true"><i class="bi bi-question-circle" aria-hidden="true"></i></button>
                    <span v-if="m.communityFavorite" class="gx-chip">Community Favorite</span>
                    <span v-if="pairableFilter && isPairable(m)" class="gx-model-tag gx-model-tag--pair" title="An eGPU variant is published, so this model can be part of a model pair"><i aria-hidden="true" class="bi bi-bezier2"></i> Pairable</span>
                  </div>
                  <div class="gx-model-item__meta">
                    <span class="gx-model-item__hw" :class="{'gx-model-item__hw--gpu': m.requiresGpu}" :title="hardwareTag(m).title || null"><i aria-hidden="true" class="bi" :class="m.requiresGpu ? 'bi-gpu-card' : 'bi-cpu'"></i> {{ m.requiresGpu ? 'External' : 'On-device' }}</span>
                    <span v-if="m.builtin" class="gx-model-specs"><span class="gx-model-spec">Built-in</span></span>
                  </div>
                  <p v-if="m.unavailableReason && !downloadStage(m) && !(rowState(m) === 'available' && m.downloadAvailable && m.unavailableReason === 'Download required')" class="gx-model-reason gx-model-card__reason">{{ m.unavailableReason }}</p>
                  <div v-if="status.randomizer || m.blacklisted" class="gx-model-random" :class="{disabled: !actionAvailable(m.blacklisted ? 'include' : 'exclude', m)}">
                    <span class="gx-row__label">Include in Randomizer</span>
                    <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Include ' + m.label + ' in Randomizer'" :checked="!m.blacklisted" :disabled="!actionAvailable(m.blacklisted ? 'include' : 'exclude', m)"
                      @change="$event.target.checked=!m.blacklisted; runAction(m.blacklisted ? 'include' : 'exclude', m)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
                  </div>
                </div>
                <div class="gx-model-item__date">{{ releasedText(m.released) }}</div>
                <div class="gx-model-item__actions">
                  <div class="gx-model-item__primary">
                    <div v-if="downloadStage(m) === 'active' && progressInfo" class="gx-model-card__progress" role="progressbar" :aria-label="'Downloading ' + m.label" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="progressInfo.indeterminate ? undefined : progressInfo.percent ?? undefined">
                      <span class="gx-model-card__progress-fill" :class="{'gx-update-progress__fill--indeterminate': progressInfo.indeterminate}" :style="!progressInfo.indeterminate && progressInfo.percent !== null ? {width: progressInfo.percent + '%'} : {}"></span>
                      <span class="gx-model-card__progress-label">{{ cardProgressLabel }}</span>
                    </div>
                    <button v-else-if="downloadStage(m) === 'queued'" type="button" class="gx-btn gx-btn--tonal" disabled><i aria-hidden="true" class="bi bi-hourglass-split"></i> Waiting in the queue</button>
                    <button v-else-if="rowState(m) === 'active'" type="button" class="gx-btn gx-btn--tonal gx-model-item__active-btn" disabled><i aria-hidden="true" class="bi bi-check-lg"></i> {{ activeButtonLabel(m) }}</button>
                    <button v-else-if="m.installed" type="button" class="gx-btn" :disabled="!actionAvailable(m.requiresGpu ? 'select-big' : 'select-small', m)" @click="runAction(m.requiresGpu ? 'select-big' : 'select-small', m)"><i aria-hidden="true" class="bi bi-play-fill"></i> Set Active {{ m.requiresGpu ? 'Big' : 'Small' }}</button>
                    <button v-else-if="m.downloadAvailable" type="button" class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('download', m)" :aria-label="'Download ' + m.label" @click="runAction('download', m)"><i aria-hidden="true" class="bi bi-download"></i> Download · {{ fileSizeText(m) }}</button>
                    <button v-else type="button" class="gx-btn gx-btn--tonal" disabled>Not available yet</button>
                  </div>
                  <div class="gx-model-item__icons">
                    <span class="gx-model-item__icon-slot">
                      <button v-if="m.installed && !m.builtin" type="button" class="gx-icon-btn gx-text-danger" :disabled="!actionAvailable('delete', m)" :aria-label="'Delete files for ' + m.label" :title="'Delete files for ' + m.label" @click="runAction('delete', m)"><i aria-hidden="true" class="bi bi-trash"></i></button>
                    </span>
                    <span class="gx-model-item__icon-slot">
                      <button type="button" class="gx-icon-btn" :disabled="!actionAvailable(m.userFavorite ? 'unfavorite' : 'favorite', m)" :aria-label="(m.userFavorite ? 'Remove from your favorites: ' : 'Add to your favorites: ') + m.label" :title="m.userFavorite ? 'Remove from your favorites' : 'Add to your favorites'" @click="runAction(m.userFavorite ? 'unfavorite' : 'favorite', m)">
                        <i aria-hidden="true" class="bi" :class="m.userFavorite ? 'bi-star-fill' : 'bi-star'"></i>
                      </button>
                    </span>
                  </div>
                </div>
              </div>
            </li>
          </ul>
        </section>

      </template>

      <GxDialog v-if="polarisAbout" labelledby="gx-polaris-about-title" @close="polarisAbout = false">
        <h3 id="gx-polaris-about-title">About Polaris models</h3>
        <p>Polaris models aren't made or endorsed by comma. They're community fine-tunes of comma's RDFv4.</p>
        <p>Turns, everyday steering, gas and brake are each taught by whichever comma model handles them best, with real human drives where none do.</p>
        <p>They're experimental. Stay alert and ready to take over.</p>
        <p>Tell us how they drive at <a href="https://submit.firestar.link" target="_blank" rel="noopener noreferrer">submit.firestar.link</a>.</p>
        <div class="gx-settings__controls gx-actions"><button type="button" class="gx-btn" @click="polarisAbout = false">Got it</button></div>
      </GxDialog>
      <GxDialog v-if="dialog" labelledby="gx-model-dialog-title" @close="finishDialog(false)">
          <h3 id="gx-model-dialog-title">{{ dialog.title }}</h3><p>{{ dialog.message }}</p>
          <div class="gx-settings__controls gx-actions"><button class="gx-btn gx-btn--tonal" type="button" @click="finishDialog(false)">Cancel</button><button ref="confirmButton" class="gx-btn" :class="{'gx-btn--danger': dialog.danger}" type="button" @click="finishDialog(true)">{{ dialog.confirmLabel }}</button></div>
      </GxDialog>
    </div>
  `,
}
