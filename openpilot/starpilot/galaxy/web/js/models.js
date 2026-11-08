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
  if (model?.requiresGpu === true) return "GPU · External";
  if (model?.requiresGpu === false) return "Comma · On-device";
  return "Hardware unknown";
}

function bytes(value) {
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function formatBytes(value) {
  return value >= 1e9 ? `${(value / 1e9).toFixed(2)} GB` : `${(value / 1e6).toFixed(1)} MB`;
}

function fileSizeText(model) {
  const installed = bytes(model?.fileSizeBytes);
  const declared = bytes(model?.declaredSizeBytes || model?.artifactSize);
  const downloaded = bytes(model?.downloadedBytes);
  if (model?.partial || model?.sizeStatus === "partial") {
    return `Partial: ${downloaded ? formatBytes(downloaded) : "unknown"} / ${declared ? formatBytes(declared) : "unknown"}`;
  }
  if (installed && declared && installed !== declared) return `${formatBytes(installed)} · size mismatch`;
  if (installed) return formatBytes(installed);
  if (declared) return formatBytes(declared);
  return "Unavailable";
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

// One shape for the progress panel; old backends only send a text line, so a percent in it still draws a bar.
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
  download: "download", downloadAll: "download_all", cancel: "cancel", delete: "delete", refresh: "refresh_manifest", jetlink: "jetlink", "disable-laboratory": "laboratory" }
const actionCapabilities = { "select-small": "select", "select-big": "select", favorite: "favorites", unfavorite: "favorites",
  "enable-randomizer": "randomizer", "disable-randomizer": "randomizer", exclude: "exclusions", include: "exclusions",
  download: "download", downloadAll: "downloadAll", cancel: "cancel", delete: "delete", refresh: "refresh", jetlink: "jetlink", "disable-laboratory": "configureLaboratory" }

export function fitsModelProfile(model, profile) {
  return Array.isArray(model?.profiles) ? model.profiles.includes(profile) : profile === "big" ? model?.requiresGpu === true : model?.requiresGpu === false
}

export function modelActionAllowed(data, action, model = null) {
  if (!data || data.capabilities?.[actionCapabilities[action]] !== true) return false
  if (data.isOnroad !== false && !["favorite", "unfavorite", "cancel", "select-small", "select-big"].includes(action)) return false
  if (action === "disable-laboratory") return data.laboratory?.enabled === true && !data.downloading
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
    if (action === "disable-laboratory") payload = { ...data.laboratory, enabled: false }
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
    if (typeof data.view === "string" && action !== "disable-laboratory") payload.view = data.view
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

export const ModelsPage = {
  name: "ModelsPage",
  components: { GxDialog, GxState, GalaxySelect, GxNotice },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data() {
    return { polarisAbout: false, loading: true, error: "", message: "", trackingProgress: false, busy: "", selectionUncertain: true, disposed: false,
      sortMode: "release_date", userFilter: "all", communityFilter: "all", tab: readTab(), failureDismissed: false,
      models: [], currentModel: "", activeSmallModel: "", activeBigModel: "", capabilities: {},
      summary: { installed: 0, missing: 0, total: 0 }, status: { isOnroad: true },
      runtime: { status: "idle", data: null, error: "" }, dialog: null, pendingChoice: null }
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
    laboratoryEnabled() { return !this.selectionUncertain && this.status.laboratory?.enabled === true },
    laboratoryRunning() { return this.runtime.status === "ready" && !!this.runtime.data?.laboratory?.lateralModel },
    installedSmallModels() { return this.models.filter(m => (m.value === this.activeSmallModel || m.selectable) && fitsModelProfile(m, "small")) },
    installedBigModels() { return this.models.filter(m => (m.value === this.activeBigModel || m.selectable) && fitsModelProfile(m, "big")) },
    // the manager words it: under Jetlink, the Jetlink app's choice
    bigNoneLabel() { return this.status.bigNoneLabel || "None — always use Active Small" },
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
        .sort((a, b) => (this.sortMode === "release_date" ? (Date.parse(b.released) || 0) - (Date.parse(a.released) || 0) : 0) ||
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
  },
  watch: {
    "status.downloading"(value) { if (value) this.failureDismissed = false },
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
  },
  mounted() {
    this.visibility = () => {
      if (document.hidden) { this.finishDialog(false); this.feed.stop(); this.manager.stop() }
      else if (this.mode === "local") { this.feed.start(); this.manager.start() }
    }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local" && !document.hidden) { this.feed.start(); this.manager.start() }
  },
  beforeUnmount() {
    this.disposed = true
    this.finishDialog(false)
    document.removeEventListener("visibilitychange", this.visibility)
    this.feed.stop()
    this.manager.stop()
  },
  methods: {
    isPolaris(model) {
      const series = typeof model?.series === "string" ? model.series.trim().toLowerCase() : ""
      return series === "polaris" || series === "polaris series" || ["spz_polaris_r3a", "spz_polaris_r3b"].includes(model?.value)
    },
    polarisTitleParts(model) {
      const label = model.label || model.value
      const emoji = label.search(/\p{Extended_Pictographic}/u)
      return emoji < 0 ? [label, ""] : [label.slice(0, emoji), label.slice(emoji)]
    },
    hardwareLabel, fileSizeText, modelFacts,
    // "active" while this model downloads, "queued" while Download Missing works through the rest
    downloadStage(model) {
      if (!this.status.downloading) return ""
      if (this.status.modelToDownload === model.value) return "active"
      if (!this.status.downloadAll || model.installed || !model.downloadAvailable) return ""
      const wanted = this.models.filter(m => !m.installed && m.downloadAvailable).length
      return model.gpuAvailable === false && this.status.downloadProgress?.count < wanted ? "" : "queued"
    },
    setTab(value) { this.tab = saveTab(value) },
    retryDownload() {
      const detail = this.status.downloadProgress
      const model = this.models.find(m => m.value === detail?.model)
      this.failureDismissed = true
      return detail?.count > 1 || !model ? this.runAction("downloadAll") : this.runAction("download", model)
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
          this.message = (action === "disable-laboratory" ? "Laboratory is off for the next start. Choose your Small and Big models below." : "") || (this.trackingProgress && this.status.progress) || result.message ||
            (action === "disable-laboratory" ? "Laboratory is off for the next start. Choose your Small and Big models below." : action.startsWith("select-") ? "Selection saved for the next onroad cycle." : "Saved.")
        }
      } finally { if (!this.disposed) { this.pendingChoice = null; this.busy = "" } }
    },
  },

  template: `
    <div class="gx-view gx-model-manager" :inert="!!busy" :aria-busy="!!busy || undefined">
      <GxState v-if="mode !== 'local'">Model Manager is unavailable in the offline preview.</GxState>
      <GxState v-else-if="loading" loading>Reading installed models and available downloads…</GxState>

      <template v-else>
        <header class="gx-settings__header gx-page-header"><div><h2>Model Manager</h2>
          <p>Choose what drives your car and manage downloads.</p></div></header>
        <GxNotice v-if="message" tone="info">{{ message }}</GxNotice>
        <GxNotice v-if="error" tone="danger" title="Model Manager">{{ error }}</GxNotice>
        <GxNotice v-if="status.isOnroad" tone="warn">Selections apply next onroad cycle. Park before downloading models or changing Jetlink.</GxNotice>

        <GxNotice v-if="progressInfo && progressInfo.failed" tone="danger" :title="progressTitle">
          {{ progressInfo.error || 'The download stopped.' }}<template v-if="progressInfo.percent !== null"> It stopped at {{ progressInfo.percent }}%.</template>
          <template #actions>
            <button type="button" class="gx-btn" :disabled="!actionAvailable('downloadAll')" @click="retryDownload()"><i aria-hidden="true" class="bi bi-arrow-repeat"></i> Try again</button>
            <button type="button" class="gx-btn gx-btn--tonal" @click="failureDismissed = true">Dismiss</button>
          </template>
        </GxNotice>

        <section v-if="laboratoryEnabled || laboratoryRunning" class="gx-card">
          <div class="gx-section__header"><i aria-hidden="true" class="bi bi-bezier2"></i><span class="gx-section__title">Model Laboratory</span></div>
          <div class="gx-panel gx-stack">
            <p v-if="laboratoryEnabled">Laboratory is on. Its lateral/longitudinal pair overrides the Small/Big selections below at the next model start.</p>
            <p v-else>Laboratory is off for the next start. The currently loaded pair stays in use until then.</p>
            <p v-if="laboratoryRunning">Loaded pair: {{ runningModelLabel }}<span v-if="runtime.data.laboratory.pendingNextStart"> · saved changes pending next start</span></p>
            <p v-else-if="laboratoryEnabled">{{ runtime.status === 'ready' && runtime.data?.laboratory?.health === 'fallback' ? runtime.data.laboratory.error : 'The pair is not currently verified as running.' }}</p>
            <div class="gx-actions">
              <a class="gx-btn gx-btn--tonal" href="#/model_laboratory">Open Laboratory</a>
              <button v-if="laboratoryEnabled" type="button" class="gx-btn" :disabled="!actionAvailable('disable-laboratory')" @click="runAction('disable-laboratory')">Turn off Laboratory</button>
            </div>
            <p v-if="laboratoryEnabled && status.isOnroad" class="gx-note">Park to turn off Laboratory. Changes apply at the next model start.</p>
          </div>
        </section>

        <section class="gx-card gx-model-driving">
          <div class="gx-panel gx-model-driving__body">
            <div class="gx-model-driving__selects">
              <div class="gx-model-control">
                <label class="gx-row__label" for="gx-model-small">Active Small · On-device</label>
                <GalaxySelect id="gx-model-small" class="gx-field" :value="pendingChoice?.action === 'select-small' ? pendingChoice.value : activeSmallModel" :disabled="selectionUncertain || capabilities.select !== true || status.randomizer" @change="runAction('select-small', installedSmallModels.find(m => m.value === $event.target.value))">
                  <option v-for="m in installedSmallModels" :key="m.value" :value="m.value" :disabled="!m.selectable">{{ m.label || m.value }}</option>
                </GalaxySelect>
              </div>
              <div class="gx-model-control">
                <label class="gx-row__label" for="gx-model-big">Active Big · External GPU</label>
                <GalaxySelect id="gx-model-big" class="gx-field" :value="pendingChoice?.action === 'select-big' ? pendingChoice.value : activeBigModel" :disabled="selectionUncertain || capabilities.select !== true || status.randomizer || status.jetlinkBig && status.isOnroad !== false" @change="$event.target.value ? runAction('select-big', installedBigModels.find(m => m.value === $event.target.value)) : runAction('select-big')">
                  <option value="">{{ bigNoneLabel }}</option>
                  <option v-for="m in installedBigModels" :key="m.value" :value="m.value" :disabled="!m.selectable">{{ m.label || m.value }}</option>
                </GalaxySelect>
              </div>
            </div>
            <div class="gx-model-driving__foot">
              <div class="gx-row gx-row--borderless gx-model-driving__random" :class="{disabled: !actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')}">
                <span class="gx-row__info gx-row__label"><i aria-hidden="true" class="bi bi-shuffle"></i> Model Randomizer <small class="gx-model-driving__random-hint">tries a different model each drive</small></span>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Model Randomizer" :checked="status.randomizer === true" :disabled="!actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')" @change="toggleRandomizer($event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
              </div>
              <span class="gx-chip gx-model-driving__running">Running now: {{ runningModelLabel }}</span>
            </div>
            <p v-if="status.randomizer" class="gx-model-reason">A different verified model is chosen each start. {{ status.gpuAvailable && activeBigModel ? 'Big GPU' : 'Small on-device' }} models are used. {{ (status.blacklistedModels || []).length }} excluded. Favorites only mark your preferred models.</p>
            <p v-if="gpuNote" class="gx-model-reason">{{ gpuNote }}</p>
            <p class="gx-model-reason">{{ laboratoryEnabled ? 'Small/Big selections are saved as fallback choices. Turn off Laboratory to use them normally at the next start.' : 'Selections take effect the next time the driving model starts.' }}</p>
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
              <div class="gx-actions"><a class="gx-btn gx-btn--tonal" href="https://github.com/StarPilotHQ/StarPilot/blob/SecretGoodStarPilot/openpilot/starpilot/models/JETLINK.md" target="_blank" rel="noopener noreferrer"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open Jetlink setup guide</a></div>
            </div>
          </details>
        </section>

        <section class="gx-card gx-model-library">
          <div class="gx-panel gx-model-toolbar">
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
              </GalaxySelect>
              <button type="button" class="gx-chip" :aria-pressed="userFilter === 'yes'" @click="userFilter = userFilter === 'yes' ? 'all' : 'yes'"><i class="bi bi-star" aria-hidden="true"></i> Favorites</button>
              <button type="button" class="gx-chip" :aria-pressed="communityFilter === 'yes'" @click="communityFilter = communityFilter === 'yes' ? 'all' : 'yes'"><i class="bi bi-people" aria-hidden="true"></i> Community</button>
            </div>
            <div class="gx-actions gx-model-toolbar__actions">
              <button v-if="status.downloading" type="button" class="gx-btn gx-btn--danger" :disabled="!actionAvailable('cancel')" @click="runAction('cancel')"><i aria-hidden="true" class="bi bi-stop-circle"></i> {{ cancelLabel }}</button>
              <button v-else type="button" class="gx-btn" :disabled="!actionAvailable('downloadAll')" @click="runAction('downloadAll')"><i aria-hidden="true" class="bi bi-download"></i> Download all<template v-if="summary.missing"> ({{ summary.missing }})</template></button>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('refresh')" aria-label="Check for new models" title="Check for new models" @click="runAction('refresh')"><i aria-hidden="true" v-if="busy === 'refresh:' || status.refreshing" class="bi bi-arrow-repeat gx-spin"></i><i aria-hidden="true" v-else class="bi bi-arrow-clockwise"></i> <span class="gx-model-toolbar__label">Check for new models</span></button>
            </div>
          </div>
        </section>

        <div v-if="!sorted.length" class="gx-card"><div class="gx-empty">No models match these favorites. <button type="button" class="gx-btn gx-btn--tonal" @click="userFilter = 'all'; communityFilter = 'all'">Show all models</button></div></div>
        <div v-else class="gx-card-grid">
          <section class="gx-card gx-model-card" :class="{'gx-model-card--polaris': isPolaris(m), 'gx-model-card--selected': rowState(m) === 'active'}" v-for="m in sorted" :key="m.value">
            <div class="gx-panel">
              <div class="gx-model-card__heading">
                <strong v-if="isPolaris(m)" :title="m.value"><span class="gx-model-card__title">{{ polarisTitleParts(m)[0] }}</span><span>{{ polarisTitleParts(m)[1] }}</span></strong>
                <strong v-else :title="m.value">{{ m.label || m.value }}</strong>
                <button type="button" class="gx-icon-btn" :disabled="!actionAvailable(m.userFavorite ? 'unfavorite' : 'favorite', m)" :aria-label="(m.userFavorite ? 'Remove from your favorites: ' : 'Add to your favorites: ') + m.label" :title="m.userFavorite ? 'Remove from your favorites' : 'Add to your favorites'" @click="runAction(m.userFavorite ? 'unfavorite' : 'favorite', m)">
                  <i aria-hidden="true" class="bi" :class="m.userFavorite ? 'bi-star-fill' : 'bi-star'"></i>
                </button>
              </div>
              <div v-if="isPolaris(m) || m.communityFavorite" class="gx-actions gx-tags">
                <span v-if="isPolaris(m)" class="gx-chip gx-polaris-badge"><span class="gx-polaris-badge__star" aria-hidden="true">✦</span> StarPilot Custom</span>
                <button v-if="isPolaris(m)" type="button" class="gx-icon-btn" aria-label="About Polaris models" title="About Polaris models" @click="polarisAbout = true"><i class="bi bi-question-circle" aria-hidden="true"></i></button>
                <span v-if="m.communityFavorite" class="gx-chip">Community Favorite</span>
              </div>
              <p v-if="m.unavailableReason && !downloadStage(m) && !(rowState(m) === 'available' && m.downloadAvailable && m.unavailableReason === 'Download required')" class="gx-model-reason gx-model-card__reason">{{ m.unavailableReason }}</p>
            </div>
            <div class="gx-panel-footer gx-model-card__foot">
              <div v-if="status.randomizer || m.blacklisted" class="gx-row gx-row--borderless" :class="{disabled: !actionAvailable(m.blacklisted ? 'include' : 'exclude', m)}"><div class="gx-row__info"><span class="gx-row__label">Include in Randomizer</span></div>
                <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Include ' + m.label + ' in Randomizer'" :checked="!m.blacklisted" :disabled="!actionAvailable(m.blacklisted ? 'include' : 'exclude', m)"
                  @change="$event.target.checked=!m.blacklisted; runAction(m.blacklisted ? 'include' : 'exclude', m)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
              <div v-if="downloadStage(m) === 'active' && progressInfo" class="gx-model-card__progress" role="progressbar" :aria-label="'Downloading ' + m.label" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="progressInfo.indeterminate ? undefined : progressInfo.percent ?? undefined">
                <span class="gx-model-card__progress-fill" :class="{'gx-update-progress__fill--indeterminate': progressInfo.indeterminate}" :style="!progressInfo.indeterminate && progressInfo.percent !== null ? {width: progressInfo.percent + '%'} : {}"></span>
                <span class="gx-model-card__progress-label">{{ cardProgressLabel }}</span>
              </div>
              <button v-else-if="downloadStage(m) === 'queued'" type="button" class="gx-btn gx-btn--tonal" disabled><i aria-hidden="true" class="bi bi-hourglass-split"></i> Waiting in the queue</button>
              <button v-else-if="rowState(m) === 'active'" type="button" class="gx-btn gx-btn--tonal" disabled><i aria-hidden="true" class="bi bi-check-lg"></i> Selected</button>
              <button v-else-if="m.installed" type="button" class="gx-btn" :disabled="!actionAvailable(m.requiresGpu ? 'select-big' : 'select-small', m)" @click="runAction(m.requiresGpu ? 'select-big' : 'select-small', m)"><i aria-hidden="true" class="bi bi-play-fill"></i> Set Active {{ m.requiresGpu ? 'Big' : 'Small' }}</button>
              <button v-else-if="m.downloadAvailable" type="button" class="gx-btn" :disabled="!actionAvailable('download', m)" :aria-label="'Download ' + m.label" @click="runAction('download', m)"><i aria-hidden="true" class="bi bi-download"></i> Download · {{ fileSizeText(m) }}</button>
              <button v-else type="button" class="gx-btn gx-btn--tonal" disabled>Not available yet</button>
            </div>
            <div class="gx-model-card__meta">
              <span class="gx-model-card__facts">{{ modelFacts(m) }}</span>
              <button v-if="m.installed && !m.builtin" type="button" class="gx-icon-btn gx-text-danger" :disabled="!actionAvailable('delete', m)" :aria-label="'Delete files for ' + m.label" :title="'Delete files for ' + m.label" @click="runAction('delete', m)"><i aria-hidden="true" class="bi bi-trash"></i></button>
            </div>
          </section>
        </div>
          </section>
        </div>

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
