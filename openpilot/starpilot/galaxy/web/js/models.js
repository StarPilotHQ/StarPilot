import { GxDialog } from "./dialog.js"
import { GxState } from "./state.js"
import { GxSummary } from "./summary.js"
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


const FILTER_KEY = "galaxy.modelManager.hardwareFilter";
const FILTERS = new Set(["both", "gpu", "comma", "none"]);

function readHardwareFilter() {
  try {
    const value = localStorage.getItem(FILTER_KEY);
    return FILTERS.has(value) ? value : "both";
  } catch {
    return "both";
  }
}

function saveHardwareFilter(value) {
  const filter = FILTERS.has(value) ? value : "both";
  try { localStorage.setItem(FILTER_KEY, filter); } catch {}
  return filter;
}

function matchesHardware(model, filter) {
  if (filter === "none") return false;
  if (filter === "gpu") return model?.requiresGpu === true;
  if (filter === "comma") return model?.requiresGpu === false;
  return true;
}

function hardwareLabel(model) {
  if (model?.requiresGpu === true) return "eGPU";
  if (model?.requiresGpu === false) return "Comma · On-device";
  return "Hardware unknown";
}

// The coloured hardware tag on each model row.
export function hardwareTag(model) {
  if (model?.requiresGpu === true) return { label: "eGPU", tone: "egpu", title: "Big model: runs on an external GPU" }
  if (model?.requiresGpu === false) return { label: "Small", tone: "small", title: "Small model: runs on the comma itself" }
  return { label: "Hardware unknown", tone: "unknown", title: "" }
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
    this.loaded = false
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
    // only the first read shows the loading screen; a re-read after an action keeps the page in place
    if (!this.data && !this.lastError && !this.loaded) this.publish({ loading: true, data: null, error: "" })
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
        this.loaded = true
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

// Model Laboratory: pairs one model's lateral output with another's longitudinal output, using eGPU (Chestnut) variants.
// It reads the same catalog and download job as the manager, through ./api/models/laboratory.
export const laboratoryModels = data => data?.models || []
export const laboratoryReady = data => laboratoryModels(data).filter(m => m.small === true && m.modelLabEligible === true && m.modelLabArtifactAvailable === true && m.modelLabArtifactInstalled === true)

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
  return ""
}

export function laboratoryActionAllowed(data, action, model, configuration = data?.configuration) {
  if (!data || data.capabilities?.[["enable", "disable", "save"].includes(action) ? "configure" : action] !== true) return false
  if (action === "cancel") return data.download?.downloading === true && typeof data.download.jobId === "string"
  if (data.isOnroad !== false || data.download?.downloading) return false
  if (action === "enable") return !laboratorySelectionError(data, configuration)
  if (action === "disable") return data.configuration.enabled === true
  if (action === "download") return laboratoryModels(data).some(m => m.value === model?.value && m.small === true && m.modelLabEligible === true && m.modelLabArtifactAvailable === true && !m.modelLabArtifactInstalled)
  if (action === "delete") return laboratoryReady(data).some(m => m.value === model?.value) &&
    !(data.configuration.enabled && [data.configuration.lateralModel, data.configuration.longitudinalModel].includes(model.value))
  if (action === "save") {
    // keep the pair's state, change only its models; an enabled pair must stay valid
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

// Selection errors that come from the chosen pair; the others are things the device or build still needs.
const LAB_CHOICE_PROBLEMS = new Set(["Choose two different models", "Both models must have downloaded eGPU variants", "Choose two downloaded eGPU variants"])

export function matchesSearch(model, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return true
  const text = [model?.label, model?.value, model?.series, model?.version, model?.released].filter(Boolean).join(" ").toLowerCase()
  return words.every(word => text.includes(word))
}

// Model list sort orders (ties fall back to the name).
const modelBytes = model => bytes(model?.fileSizeBytes) || bytes(model?.declaredSizeBytes || model?.artifactSize) || 0
export const MODEL_SORTS = Object.freeze({
  release_date: (a, b) => (Date.parse(b.released) || 0) - (Date.parse(a.released) || 0),
  alphabetical: () => 0,
  size: (a, b) => modelBytes(b) - modelBytes(a),
})

// Per-model reasons worth showing on a card; the common states are covered by the chip and the Laboratory card.
const LAB_REASON_STATES = new Set(["unsupported", "unpublished", "checking"])

export const ModelsPage = {
  name: "ModelsPage",
  components: { GxDialog, GxState, GxSummary, GalaxySelect, GxNotice },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    // "laboratory" scrolls to the Model Laboratory card (old #/model_laboratory links)
    focus: { type: String, default: "" } },
  data() {
    return { polarisAbout: false, loading: true, error: "", message: "", trackingProgress: false, busy: "", selectionUncertain: true, disposed: false,
      sortMode: "release_date", userFilter: "all", communityFilter: "all", hardwareFilter: readHardwareFilter(), pairableFilter: false, installedFilter: false, search: "",
      models: [], currentModel: "", activeSmallModel: "", activeBigModel: "", capabilities: {},
      summary: { installed: 0, missing: 0, total: 0 }, status: { isOnroad: true },
      runtime: { status: "idle", data: null, error: "" }, dialog: null, pendingChoice: null,
      lab: null, labError: "", labDirty: false, labConfig: { enabled: false, lateralModel: "", longitudinalModel: "" }, focused: false,
      // Single model / Model pair selector: the mode being shown while a change is saving, or before a pair can switch on.
      pairView: null,
      jetlinkOpen: false, filtersOpen: false, browseOpen: false,
      // Catalog checks report in the Browse models header, never as a banner that pushes the page down.
      refreshNote: "", trackingRefresh: false }
  },
  computed: {
    currentLabel() { return this.models.find(m => m.value === this.currentModel)?.label || this.currentModel || "" },
    runningModelLabel() {
      if (!this.selectionUncertain && this.status.jetlink?.active) return "Jetlink remote model"
      if (this.runtime.status === "unavailable" || this.runtime.status === "stale") return "Status unavailable"
      if (this.runtime.status !== "ready" || !this.runtime.data) return "Checking…"
      const model = this.runtime.data
      if (model.health === "unavailable") return "No running model"
      if (!model.loadedId) return model.health === "loading" ? "Loading…" : "Identity unverified"
      return model.catalog.find(item => item.id === model.loadedId)?.name || model.loadedId
    },
    installedSmallModels() { return this.models.filter(m => (m.value === this.activeSmallModel || m.selectable) && fitsModelProfile(m, "small")) },
    installedBigModels() { return this.models.filter(m => (m.value === this.activeBigModel || m.selectable) && fitsModelProfile(m, "big")) },
    // the manager words it: under Jetlink, the Jetlink app's choice
    bigNoneLabel() { return this.status.bigNoneLabel || "None — always use Active Small" },
    sorted() {
      return this.models.filter(m => matchesHardware(m, this.hardwareFilter))
        .filter(m => this.userFilter === "all" || !!m.userFavorite === (this.userFilter === "yes"))
        .filter(m => this.communityFilter === "all" || !!m.communityFavorite === (this.communityFilter === "yes"))
        .filter(m => !this.pairableFilter || this.isPairable(m))
        .filter(m => !this.installedFilter || m.installed === true)
        .filter(m => matchesSearch(m, this.search))
        .sort((a, b) => (MODEL_SORTS[this.sortMode]?.(a, b) || 0) ||
          (a.label || a.value).localeCompare(b.label || b.value, undefined, { sensitivity: "base", numeric: true }))
    },
    driveMode() { return this.pairView ?? (this.lab?.configuration.enabled ? "pair" : "single") },
    // Filters set in the funnel menu (search not included), shown as a count on the funnel.
    filterCount() {
      return [this.hardwareFilter !== "both", this.installedFilter, this.userFilter === "yes", this.communityFilter === "yes", this.pairableFilter].filter(Boolean).length
    },
    filtersActive() { return !!(this.search || this.installedFilter || this.pairableFilter || this.userFilter !== "all" || this.communityFilter !== "all" || this.hardwareFilter !== "both") },
    // eGPU variants only matter for model pairs.
    showLabVariants() { return this.driveMode === "pair" || this.pairableFilter },
    downloadTargetLabel() {
      if (this.status.refreshing) return "model catalog"
      if (this.status.downloadAll) return "all missing models"
      const label = this.models.find(m => m.value === this.status.modelToDownload)?.label || "a model"
      return this.status.downloadVariant === "amd" ? `${label} · eGPU variant` : label
    },
    labRows() { return new Map(laboratoryModels(this.lab).map(m => [m.value, m])) },
    labReady() { return laboratoryReady(this.lab) },
    labCandidates() { return this.labReady.filter(m => m.value !== this.labConfig.lateralModel) },
    labSelectionError() { return laboratorySelectionError(this.lab, this.labConfig) },
    labRuntimeState() { return !this.lab ? "Status unavailable" : !this.lab.runtimeSupported ? "Unavailable" : this.lab.runtime.active ? "Pair active" : this.lab.runtime.requested ? "Pair requested" : "Inactive" },
    labChoiceProblem() { return LAB_CHOICE_PROBLEMS.has(this.labSelectionError) },
    missingDownloads() { return this.models.filter(m => !m.installed && m.downloadAvailable === true) },
    labLocked() { return !this.lab || this.lab.isOnroad || this.lab.download.downloading },
  },
  watch: {
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
        if (this.trackingRefresh && !p.downloading) { this.trackingRefresh = false; this.noteRefresh(p.progress || "Model list checked") }
        if (this.trackingProgress) {
          if (p.downloading) this.message = ""
          else {
            if (p.progress) this.message = p.progress
            this.trackingProgress = false
          }
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
      if (document.hidden) { this.finishDialog(false); this.feed.stop(); this.manager.stop(); this.laboratory.stop() }
      else if (this.mode === "local") { this.feed.start(); this.manager.start(); this.laboratory.start() }
    }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local" && !document.hidden) { this.feed.start(); this.manager.start(); this.laboratory.start() }
  },
  beforeUnmount() {
    this.disposed = true
    clearTimeout(this.refreshTimer)
    if (this.filterAway) { document.removeEventListener("pointerdown", this.filterAway); document.removeEventListener("keydown", this.filterAway) }
    this.finishDialog(false)
    document.removeEventListener("visibilitychange", this.visibility)
    this.feed.stop()
    this.manager.stop()
    this.laboratory.stop()
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
    hardwareTag,
    // The funnel menu closes on a click elsewhere or Escape.
    toggleFilters() { this.filtersOpen ? this.closeFilters() : this.openFilters() },
    openFilters() {
      this.filtersOpen = true
      this.filterAway = event => {
        if (event.type === "keydown" ? event.key === "Escape" : !this.$refs.filterMenu?.contains(event.target)) this.closeFilters(event.type === "keydown")
      }
      setTimeout(() => {
        if (!this.filtersOpen) return
        document.addEventListener("pointerdown", this.filterAway)
        document.addEventListener("keydown", this.filterAway)
      })
    },
    closeFilters(refocus = false) {
      this.filtersOpen = false
      if (this.filterAway) { document.removeEventListener("pointerdown", this.filterAway); document.removeEventListener("keydown", this.filterAway); this.filterAway = null }
      if (refocus) this.$refs.filterButton?.focus()
    },
    labStatusLabel: laboratoryStatusLabel,
    labReason(model) { const row = this.labRow(model); return row && LAB_REASON_STATES.has(row.modelLabStatus) ? row.modelLabReason : "" },
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
    // Saves the pair's models; a choice the device refuses goes back to the saved pair.
    // With Model pair picked, the pair switches on as soon as both models can run.
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
    // The Single model / Model pair selector. Single turns a pair off; Model pair turns it on when it can run,
    // otherwise it shows the pair settings and what is still missing.
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
      if (!roles.length && model.value === this.currentModel) roles.push("Selected")
      return roles
    },
    pairRoles(model) {
      const pair = this.lab?.configuration
      if (!pair) return []
      return [[pair.lateralModel, "Pair · lateral"], [pair.longitudinalModel, "Pair · longitudinal"]]
        .filter(([id]) => id && id === model.value).map(([, role]) => role)
    },
    noteRefresh(note) {
      clearTimeout(this.refreshTimer)
      this.refreshNote = note || ""
      this.refreshTimer = setTimeout(() => { this.refreshNote = "" }, 6000)
    },
    showPairable() {
      this.pairableFilter = true
      this.browseOpen = true
      this.$nextTick(() => this.$refs.browse?.scrollIntoView({ block: "start" }))
    },
    clearFilters() {
      Object.assign(this, { search: "", installedFilter: false, pairableFilter: false, userFilter: "all", communityFilter: "all" })
      this.setHardwareFilter("both")
    },
    toggleLabPair(event) {
      const enabled = this.lab?.configuration.enabled === true
      event.target.checked = enabled
      return reportSave(() => this.runLabAction(enabled ? "disable" : "enable"))
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
          // the download job is shared, so show its progress through the manager straight away
          this.manager.load()
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
    hardwareLabel, fileSizeText,
    setHardwareFilter(value) { this.hardwareFilter = saveHardwareFilter(value) },
    toggleHardware(kind, checked) {
      const gpu = kind === "gpu" ? checked : ["both", "gpu"].includes(this.hardwareFilter)
      const comma = kind === "comma" ? checked : ["both", "comma"].includes(this.hardwareFilter)
      this.setHardwareFilter(gpu && comma ? "both" : gpu ? "gpu" : comma ? "comma" : "none")
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
        if (action === "downloadAll") {
          // one confirmation: how much will download, and the GPU caveat when it applies
          const missing = this.missingDownloads
          const size = missing.reduce((total, m) => total + (bytes(m.declaredSizeBytes || m.artifactSize) || 0), 0)
          if (!await this.confirmAction({ title: "Download Missing Models", confirmLabel: needsGpu ? "Download Anyway" : "Download",
            message: `Download ${missing.length} model${missing.length === 1 ? "" : "s"}${size ? ` (about ${formatBytes(size)})` : ""}? Keep the device connected until the download finishes. Downloading does not change the active model.` +
              (needsGpu ? " Some of these need an external GPU, which is not detected; they cannot run until a compatible GPU is connected." : "") })) return
          allowGpuWithoutGpu = needsGpu
        } else if (needsGpu) {
          allowGpuWithoutGpu = await this.confirmAction({ title: "No External GPU Detected", confirmLabel: "Download Anyway",
            message: "These model files require an external GPU. You can download them now, but they cannot run until a compatible GPU is connected and detected. GPU model files can be large. Downloading does not activate a model." })
          if (!allowGpuWithoutGpu) return
        }
        if (this.disposed) return
        const perform = () => this.manager.action(action, model, { allowGpuWithoutGpu })
        const result = await (["enable-randomizer", "disable-randomizer", "include", "exclude"].includes(action) ? reportSave(perform) : perform())
        if (!this.disposed && result) {
          this.trackingProgress = ["download", "downloadAll", "refresh"].includes(action)
          this.message = this.trackingProgress && this.status.downloading ? "" : (this.trackingProgress && this.status.progress) || result.message ||
            (action.startsWith("select-") ? "Selection saved for the next onroad cycle." : "Saved.")
          if (action === "refresh") {
            this.trackingRefresh = this.status.downloading === true
            if (!this.trackingRefresh) this.noteRefresh(this.message)
            this.message = ""
            this.trackingProgress = false
          }
          if (["download", "downloadAll", "cancel", "delete", "refresh"].includes(action)) this.laboratory.load()
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
          <p>Manage models, downloads, Jetlink connections, and Model Laboratory pairs.</p></div></header>
        <GxNotice v-if="message" tone="info">{{ message }}</GxNotice>
        <GxNotice v-if="error" tone="danger" title="Model Manager">{{ error }}</GxNotice>
        <GxNotice v-if="status.isOnroad" tone="warn">Selections apply next onroad cycle. Park before downloading models or changing Jetlink.</GxNotice>
        <GxNotice v-if="status.downloading && !status.refreshing" :title="'Downloading ' + downloadTargetLabel">{{ status.progress || 'Keep the device connected until the download finishes.' }}</GxNotice>
        <section class="gx-card gx-model-choose">
          <div class="gx-section__header">
            <i aria-hidden="true" class="bi bi-sliders"></i>
            <span class="gx-section__title">Choose models</span>
            <span class="gx-section__count">Applies at the next start</span>
          </div>
          <div class="gx-panel gx-stack">
            <!-- One mode at a time. Single model turns a pair off; Model pair turns it on once it can run. -->
            <div class="gx-tabs gx-model-mode" role="group" aria-label="Driving models">
              <button type="button" :aria-pressed="driveMode === 'single'" :disabled="!!busy || labLocked && driveMode !== 'single'" @click="setDriveMode('single')"><i aria-hidden="true" class="bi bi-cpu"></i> Single model</button>
              <button type="button" :aria-pressed="driveMode === 'pair'" :disabled="!!busy || labLocked" @click="setDriveMode('pair')"><i aria-hidden="true" class="bi bi-bezier2"></i> Model pair</button>
            </div>
            <template v-if="driveMode === 'single'">
              <small class="gx-model-mode__hint">One model steers and sets the speed.</small>
              <div class="gx-model-control">
                <label class="gx-row__label gx-model-control__label" for="gx-model-small">Active Small <span class="gx-model-tag gx-model-tag--small" title="Small model: runs on the comma itself">Small</span></label>
                <GalaxySelect id="gx-model-small" class="gx-field" :value="pendingChoice?.action === 'select-small' ? pendingChoice.value : activeSmallModel" :disabled="selectionUncertain || capabilities.select !== true || status.randomizer" @change="runAction('select-small', installedSmallModels.find(m => m.value === $event.target.value))">
                  <option v-for="m in installedSmallModels" :key="m.value" :value="m.value" :disabled="!m.selectable">{{ m.label || m.value }}</option>
                </GalaxySelect>
              </div>
              <div class="gx-model-control">
                <label class="gx-row__label gx-model-control__label" for="gx-model-big">Active Big <span class="gx-model-tag gx-model-tag--egpu" title="Big model: runs on an external GPU">eGPU</span></label>
                <GalaxySelect id="gx-model-big" class="gx-field" :value="pendingChoice?.action === 'select-big' ? pendingChoice.value : activeBigModel" :disabled="selectionUncertain || capabilities.select !== true || status.randomizer || status.jetlinkBig && status.isOnroad !== false" @change="$event.target.value ? runAction('select-big', installedBigModels.find(m => m.value === $event.target.value)) : runAction('select-big')">
                  <option value="">{{ bigNoneLabel }}</option>
                  <option v-for="m in installedBigModels" :key="m.value" :value="m.value" :disabled="!m.selectable">{{ m.label || m.value }}</option>
                </GalaxySelect>
              </div>
              <p v-if="status.jetlinkBig === true" class="gx-model-reason">Jetlink runs Active Big on its host, which downloads the model itself. A model chosen in the Jetlink app shows here once the comma connects.</p>
              <p v-else-if="!status.gpuAvailable" class="gx-model-reason">An external GPU is not detected. Active Small is used; your Big selection is saved for later.</p>
              <div class="gx-row gx-row--borderless" :class="{disabled: !actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')}">
                <span class="gx-row__info gx-row__label">Model Randomizer</span>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Model Randomizer" :checked="status.randomizer === true" :disabled="!actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')" @change="toggleRandomizer($event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
              </div>
              <p v-if="status.randomizer" class="gx-model-reason">A different verified model is chosen each start. {{ status.gpuAvailable && activeBigModel ? 'Big GPU' : 'Small on-device' }} models are used. {{ (status.blacklistedModels || []).length }} excluded. Favorites only mark your preferred models.</p>
            </template>
            <div v-else id="gx-model-laboratory" ref="laboratory" class="gx-stack gx-model-laboratory">
              <small class="gx-model-mode__hint">Pair lateral judgment from one model with longitudinal judgment from another. Enabling applies when modeld next starts, when it loads and verifies the model weights, input tensors and GPU state.</small>
                <div class="gx-actions gx-tags">
                  <span class="gx-chip">{{ lab ? (lab.chestnutReady ? 'Chestnut ready' : 'Chestnut required') : 'Waiting for device status' }}</span>
                  <span v-if="lab" class="gx-chip" :class="{'gx-chip--selected': labRuntimeState === 'Pair active'}">{{ labRuntimeState }}</span>
                </div>
              <GxNotice v-if="labError" tone="danger" title="Model Laboratory">{{ labError }}</GxNotice>
              <GxNotice v-if="lab?.configurationError" tone="danger">{{ lab.configurationError }}</GxNotice>
              <p v-if="!lab && !labError" class="gx-note">Loading Model Laboratory…</p>
              <template v-if="lab">
                <div class="gx-model-control">
                  <label class="gx-row__label" for="gx-lab-lateral">Lateral model<small>Path shape, curvature, lane geometry, and driving desire</small></label>
                  <GalaxySelect id="gx-lab-lateral" class="gx-field" aria-label="Lateral model" :disabled="labLocked" :value="labConfig.lateralModel" @change="changeLateral($event.target.value)">
                    <option value="">Choose a model</option>
                    <option v-for="m in labReady" :key="m.value" :value="m.value">{{ m.label }} · {{ m.version }}</option>
                  </GalaxySelect>
                </div>
                <div class="gx-model-control">
                  <label class="gx-row__label" for="gx-lab-longitudinal">Longitudinal model<small>Speed, acceleration, stopping, leads, and scene confidence</small></label>
                  <GalaxySelect id="gx-lab-longitudinal" class="gx-field" aria-label="Longitudinal model" :disabled="labLocked" :value="labConfig.longitudinalModel" @change="changeLongitudinal($event.target.value)">
                    <option value="">Choose a model</option>
                    <option v-for="m in labCandidates" :key="m.value" :value="m.value">{{ m.label }} · {{ m.version }}</option>
                  </GalaxySelect>
                </div>
                <p class="gx-row__desc">{{ labModelLabel(labConfig.lateralModel) }} steers · {{ labModelLabel(labConfig.longitudinalModel) }} paces</p>
                <GxNotice v-if="labSelectionError" :tone="labChoiceProblem ? 'danger' : 'info'">{{ labSelectionError }}</GxNotice>
                <div class="gx-row gx-row--borderless">
                  <div class="gx-row__info">
                    <span class="gx-row__label">Running pair</span>
                    <span class="gx-row__desc">Lateral: {{ labModelLabel(lab.runtime.lateralModel) }} · Longitudinal: {{ labModelLabel(lab.runtime.longitudinalModel) }}</span>
                    <span v-if="!lab.runtimeSupported && labSelectionError !== lab.runtimeUnavailableReason" class="gx-row__desc">{{ lab.runtimeUnavailableReason || 'Pair runtime is unavailable.' }}</span>
                  </div>
                </div>
                <GxNotice v-if="lab.runtime.error" tone="danger">{{ lab.runtime.error }}</GxNotice>
                <div class="gx-row gx-row--borderless gx-row--wrap">
                  <div class="gx-row__info"><span class="gx-row__desc">Verified downloads: {{ labReady.length }} of {{ lab.summary?.published || 0 }} published eGPU variants. Download them from the Small model cards.</span></div>
                  <button type="button" class="gx-btn gx-btn--tonal" @click="showPairable"><i aria-hidden="true" class="bi bi-funnel"></i> Show pairable models</button>
                </div>
              </template>
            </div>
          </div>
        </section>

        <section v-if="status.jetlink" class="gx-card gx-model-jetlink">
          <button type="button" class="gx-section__header gx-model-collapse" :aria-expanded="String(jetlinkOpen)" aria-controls="gx-model-jetlink-panel" @click="jetlinkOpen = !jetlinkOpen">
            <i aria-hidden="true" class="bi bi-usb-symbol"></i><span class="gx-section__title">Jetlink</span>
            <span class="gx-chip">{{ status.jetlink.state }}</span>
            <i aria-hidden="true" class="bi bi-chevron-down gx-model-collapse__chevron"></i>
          </button>
          <div v-show="jetlinkOpen" id="gx-model-jetlink-panel" class="gx-panel gx-stack">
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
        </section>

        <section ref="browse" class="gx-card gx-model-browse">
          <button type="button" class="gx-section__header gx-model-collapse" :aria-expanded="String(browseOpen)" aria-controls="gx-model-browse-panel" @click="browseOpen = !browseOpen">
            <i aria-hidden="true" class="bi bi-list-ul"></i>
            <span class="gx-section__title">Browse models</span>
            <span class="gx-section__count" role="status"><template v-if="status.refreshing">Checking for new models…</template><template v-else-if="refreshNote">{{ refreshNote }}</template><template v-else><span class="gx-model-count-word">Showing </span>{{ sorted.length }} of {{ models.length }}</template></span>
            <i aria-hidden="true" class="bi bi-chevron-down gx-model-collapse__chevron"></i>
          </button>
          <div v-if="browseOpen" id="gx-model-browse-panel">
          <div class="gx-panel gx-stack gx-model-tools">
            <div class="gx-model-searchbar">
              <input class="gx-field gx-model-search" type="search" v-model.trim="search" placeholder="Search models" aria-label="Search models">
              <button v-if="status.downloading" type="button" class="gx-btn gx-btn--danger gx-model-btn" :disabled="!actionAvailable('cancel')" @click="runAction('cancel')"><i aria-hidden="true" class="bi bi-stop-circle"></i> {{ status.refreshing ? 'Stop check' : 'Cancel download' }}</button>
              <!-- The comma does not check by itself while this page is open; this checks for new models now. -->
              <button type="button" class="gx-icon-btn gx-model-refresh" :disabled="!actionAvailable('refresh')" aria-label="Check for new models" title="Check for new models" @click="runAction('refresh')"><i aria-hidden="true" class="bi" :class="busy === 'refresh:' || status.refreshing ? 'bi-arrow-repeat gx-model-spin' : 'bi-arrow-clockwise'"></i></button>
              <div ref="filterMenu" class="gx-model-filter">
                <button ref="filterButton" type="button" class="gx-icon-btn gx-model-filter__button" :class="{'gx-model-filter__button--on': filterCount}" :aria-expanded="String(filtersOpen)" aria-controls="gx-model-filter-menu" aria-haspopup="true"
                  :aria-label="filterCount ? 'Filters, ' + filterCount + ' on' : 'Filters'" title="Filters" @click="toggleFilters"><i aria-hidden="true" class="bi" :class="filterCount ? 'bi-funnel-fill' : 'bi-funnel'"></i><span v-if="filterCount" class="gx-model-filter__count" aria-hidden="true">{{ filterCount }}</span></button>
                <div v-if="filtersOpen" id="gx-model-filter-menu" class="gx-model-filter__menu" role="group" aria-label="Filter models">
                  <span class="gx-model-filter__heading">Hardware</span>
                  <label class="gx-model-filter__option"><input type="checkbox" :checked="['both', 'comma'].includes(hardwareFilter)" @change="toggleHardware('comma', $event.target.checked)"><span class="gx-model-tag gx-model-tag--small">Small</span> On the comma</label>
                  <label class="gx-model-filter__option"><input type="checkbox" :checked="['both', 'gpu'].includes(hardwareFilter)" @change="toggleHardware('gpu', $event.target.checked)"><span class="gx-model-tag gx-model-tag--egpu">eGPU</span> External GPU</label>
                  <span class="gx-model-filter__heading">Show only</span>
                  <label class="gx-model-filter__option"><input type="checkbox" v-model="installedFilter"> Installed</label>
                  <label class="gx-model-filter__option"><input type="checkbox" :checked="userFilter === 'yes'" @change="userFilter = $event.target.checked ? 'yes' : 'all'"> Your favorites</label>
                  <label class="gx-model-filter__option"><input type="checkbox" :checked="communityFilter === 'yes'" @change="communityFilter = $event.target.checked ? 'yes' : 'all'"> Community favorites</label>
                  <label class="gx-model-filter__option"><input type="checkbox" v-model="pairableFilter"> <span class="gx-model-tag gx-model-tag--pair">Pairable</span></label>
                  <button type="button" class="gx-model-clear" :disabled="!filterCount" @click="clearFilters">Clear filters</button>
                </div>
              </div>
            </div>
            <div class="gx-model-filters" role="group" aria-label="Sort models">
              <span class="gx-model-filters__label">Sort by</span>
              <div class="gx-model-chips">
                <button type="button" class="gx-chip gx-model-chip" :aria-pressed="sortMode === 'release_date'" @click="sortMode = 'release_date'">Newest</button>
                <button type="button" class="gx-chip gx-model-chip" :aria-pressed="sortMode === 'alphabetical'" @click="sortMode = 'alphabetical'">Name</button>
                <button type="button" class="gx-chip gx-model-chip" :aria-pressed="sortMode === 'size'" title="Largest first" @click="sortMode = 'size'">Size</button>
              </div>
            </div>
          </div>
          <div v-if="!sorted.length" class="gx-empty gx-actions">No models match these filters. <button type="button" class="gx-btn gx-btn--tonal" @click="clearFilters">Clear search and filters</button></div>
          <ul v-else class="gx-model-list" aria-label="Models">
            <li v-for="m in sorted" :key="m.value" class="gx-model-item" :class="{'gx-model-card--polaris': isPolaris(m), 'gx-model-item--active': activeRoles(m).length > 0}">
              <div class="gx-model-item__main">
                <div class="gx-model-item__title">
                  <strong v-if="isPolaris(m)"><span class="gx-model-card__title">{{ polarisTitleParts(m)[0] }}</span><span>{{ polarisTitleParts(m)[1] }}</span></strong>
                  <strong v-else>{{ m.label || m.value }}</strong>
                  <span v-if="isPolaris(m)" class="gx-chip gx-polaris-badge"><span class="gx-polaris-badge__star" aria-hidden="true">✦</span> StarPilot Custom</span>
                  <button v-if="isPolaris(m)" type="button" class="gx-icon-btn gx-model-about" aria-label="About Polaris models" title="About Polaris models" @click="polarisAbout = true"><i class="bi bi-question-circle" aria-hidden="true"></i></button>
                  <span v-for="role in pairRoles(m)" :key="role" class="gx-chip">{{ role }}</span>
                  <span v-if="m.communityFavorite" class="gx-chip">Community Favorite</span>
                  <span v-if="m.builtin" class="gx-chip">Built-in</span>
                  <span v-if="m.partial" class="gx-chip">Partial Files</span>
                </div>
                <div class="gx-model-item__meta">
                  <span class="gx-model-tag" :class="'gx-model-tag--' + hardwareTag(m).tone" :title="hardwareTag(m).title || null">{{ hardwareTag(m).label }}</span>
                  <span v-for="role in activeRoles(m)" :key="role" class="gx-model-tag gx-model-tag--active">{{ role }}</span>
                  <span v-if="isPairable(m)" class="gx-model-tag gx-model-tag--pair" title="An eGPU variant is published, so this model can be part of a model pair"><i aria-hidden="true" class="bi bi-bezier2"></i> Pairable</span>
                  <span class="gx-model-item__facts"><span v-if="m.released" :aria-label="'Released '+ m.released">{{ m.released }}</span><span>{{ fileSizeText(m) }}</span></span>
                </div>
                <p v-if="m.unavailableReason && !(rowState(m) === 'available' && m.downloadAvailable && m.unavailableReason === 'Download required')" class="gx-model-reason">{{ m.unavailableReason }}</p>
                <!-- eGPU variants matter only for model pairs, so they show with Model pair or Pairable only. -->
                <template v-if="showLabVariants">
                  <div v-if="labRow(m)?.small" class="gx-model-lab">
                    <span class="gx-model-lab__label"><i aria-hidden="true" class="bi bi-bezier2"></i> eGPU variant</span>
                    <span class="gx-chip">{{ labStatusLabel(labRow(m)) }}</span>
                    <button v-if="labRow(m).modelLabEligible && labRow(m).modelLabArtifactAvailable && !labRow(m).modelLabArtifactInstalled" type="button" class="gx-icon-btn" :disabled="!labActionAvailable('download', labRow(m))" :aria-label="'Download eGPU variant: ' + m.label" title="Download eGPU variant" @click="runLabAction('download', labRow(m))"><i class="bi bi-download" aria-hidden="true"></i></button>
                    <button v-else-if="labRow(m).modelLabArtifactInstalled" type="button" class="gx-icon-btn gx-text-danger" :disabled="!labActionAvailable('delete', labRow(m))" :aria-label="'Delete eGPU variant: ' + m.label" title="Delete eGPU variant" @click="runLabAction('delete', labRow(m))"><i class="bi bi-trash" aria-hidden="true"></i></button>
                  </div>
                  <p v-if="labRow(m)?.small && labReason(m)" class="gx-model-reason gx-model-lab__reason">{{ labReason(m) }}</p>
                </template>
              </div>
              <div class="gx-model-item__actions">
                <label v-if="status.randomizer || m.blacklisted" class="gx-model-random" :class="{disabled: !actionAvailable(m.blacklisted ? 'include' : 'exclude', m)}"><span class="gx-model-random__label">Include in Randomizer</span>
                  <span class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Include ' + m.label + ' in Randomizer'" :checked="!m.blacklisted" :disabled="!actionAvailable(m.blacklisted ? 'include' : 'exclude', m)"
                    @change="$event.target.checked=!m.blacklisted; runAction(m.blacklisted ? 'include' : 'exclude', m)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span></label>
                <span v-if="rowState(m) === 'busy'" class="gx-chip"><i aria-hidden="true" class="bi bi-hourglass-split"></i> Busy</span>
                <button v-else-if="rowState(m) === 'cancellable'" type="button" class="gx-btn gx-btn--danger gx-model-btn" :disabled="!actionAvailable('cancel', m)" @click="runAction('cancel', m)"><i aria-hidden="true" class="bi bi-x-circle"></i> Cancel</button>
                <template v-else-if="rowState(m) === 'installed'">
                  <button type="button" class="gx-btn gx-btn--tonal gx-model-btn" :disabled="!actionAvailable(m.requiresGpu ? 'select-big' : 'select-small', m)" @click="runAction(m.requiresGpu ? 'select-big' : 'select-small', m)"><i aria-hidden="true" class="bi bi-play-fill"></i> Set Active {{ m.requiresGpu ? 'Big' : 'Small' }}</button>
                  <button v-if="!m.builtin" type="button" class="gx-icon-btn gx-model-delete" :disabled="!actionAvailable('delete', m)" :aria-label="'Delete ' + m.label" title="Delete" @click="runAction('delete', m)"><i aria-hidden="true" class="bi bi-trash"></i></button>
                </template>
                <button v-else-if="rowState(m) === 'available' && m.downloadAvailable" type="button" class="gx-icon-btn" :disabled="!actionAvailable('download', m)" :aria-label="'Download ' + m.label" :title="'Download ' + m.label" @click="runAction('download', m)"><i class="bi bi-download" aria-hidden="true"></i></button>
                <button type="button" class="gx-icon-btn" :disabled="!actionAvailable(m.userFavorite ? 'unfavorite' : 'favorite', m)" :aria-label="(m.userFavorite ? 'Remove from your favorites: ' : 'Add to your favorites: ') + m.label" :title="m.userFavorite ? 'Remove from your favorites' : 'Add to your favorites'" @click="runAction(m.userFavorite ? 'unfavorite' : 'favorite', m)">
                  <i aria-hidden="true" class="bi" :class="m.userFavorite ? 'bi-star-fill' : 'bi-star'"></i>
                </button>
              </div>
            </li>
          </ul>
          </div>
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

