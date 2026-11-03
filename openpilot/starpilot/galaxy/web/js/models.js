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
  if (data.isOnroad !== false && !["favorite", "unfavorite", "cancel"].includes(action)) return false
  if (action === "jetlink") return data.jetlink !== undefined
  if (["select-small", "select-big"].includes(action)) {
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

export const ModelsPage = {
  name: "ModelsPage",
  components: { GxDialog, GxState, GxSummary, GalaxySelect, GxNotice },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data() {
    return { polarisAbout: false, loading: true, error: "", message: "", trackingProgress: false, busy: "", selectionUncertain: true, disposed: false,
      sortMode: "release_date", userFilter: "all", communityFilter: "all", hardwareFilter: readHardwareFilter(),
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
        .sort((a, b) => (this.sortMode === "release_date" ? (Date.parse(b.released) || 0) - (Date.parse(a.released) || 0) : 0) ||
          (a.label || a.value).localeCompare(b.label || b.value, undefined, { sensitivity: "base", numeric: true }))
    },
    downloadTargetLabel() { return this.status.downloadAll ? "all missing models" : this.models.find(m => m.value === this.status.modelToDownload)?.label || "a model" },
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
            (action.startsWith("select-") ? "Selection saved for the next start." : "Saved.")
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
          <p>Manage models, downloads, and Jetlink connections.</p></div></header>
        <GxNotice v-if="message" tone="info">{{ message }}</GxNotice>
        <GxNotice v-if="error" tone="danger" title="Model Manager">{{ error }}</GxNotice>
        <GxNotice v-if="status.isOnroad" tone="warn">Park the vehicle before changing or downloading models.</GxNotice>
        <GxNotice v-if="status.downloading" :title="'Downloading ' + downloadTargetLabel">{{ status.progress || 'Keep the device connected until the download finishes.' }}</GxNotice>
        <section class="gx-card gx-model-overview">
          <div class="gx-section__header">
            <i aria-hidden="true" class="bi bi-cpu"></i>
            <span class="gx-section__title">Models</span>
            <span v-if="currentLabel" class="gx-section__count">Selected: {{ currentLabel }}</span>
          </div>
          <GxSummary :items="[{label: 'Installed', value: summary.installed}, {label: 'Missing', value: summary.missing}, {label: 'Total', value: summary.total}, {label: 'Running model', value: runningModelLabel}]" />
        </section>

        <section v-if="status.jetlink" class="gx-card">
          <div class="gx-section__header"><i aria-hidden="true" class="bi bi-usb-symbol"></i><span class="gx-section__title">Jetlink</span></div>
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
        </section>

        <section class="gx-card">
          <div class="gx-section__header">
            <i aria-hidden="true" class="bi bi-sliders"></i>
            <span class="gx-section__title">Controls</span>
          </div>
          <div class="gx-panel gx-stack">
            <p class="gx-note">Selections take effect the next time the driving model starts.</p>
            <div class="gx-actions">
              <button v-if="status.downloading" type="button" class="gx-btn gx-btn--danger" :disabled="!actionAvailable('cancel')" @click="runAction('cancel')"><i aria-hidden="true" class="bi bi-stop-circle"></i> Cancel Download</button>
              <button v-else type="button" class="gx-btn" :disabled="!actionAvailable('downloadAll')" @click="runAction('downloadAll')"><i aria-hidden="true" class="bi bi-download"></i> Download Missing Models</button>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('refresh')" @click="runAction('refresh')"><i aria-hidden="true" v-if="busy === 'refresh:'" class="bi bi-arrow-repeat gx-spin"></i><i aria-hidden="true" v-else class="bi bi-arrow-clockwise"></i> Check model catalog</button>
            </div>

            <div class="gx-row gx-row--borderless" :class="{disabled: !actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')}">
              <span class="gx-row__info gx-row__label">Model Randomizer</span>
              <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Model Randomizer" :checked="status.randomizer === true" :disabled="!actionAvailable(status.randomizer ? 'disable-randomizer' : 'enable-randomizer')" @change="toggleRandomizer($event)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label>
            </div>
            <p v-if="status.randomizer" class="gx-model-reason">A different verified model is chosen each start. {{ status.gpuAvailable && activeBigModel ? 'Big GPU' : 'Small on-device' }} models are used. {{ (status.blacklistedModels || []).length }} excluded. Favorites only mark your preferred models.</p>
            <p v-if="status.jetlinkBig === true" class="gx-model-reason">Jetlink runs Active Big on its host, which downloads the model itself. A model chosen in the Jetlink app shows here once the comma connects.</p>
            <p v-else-if="!status.gpuAvailable" class="gx-model-reason">An external GPU is not detected. Active Small is used; your Big selection is saved for later.</p>

            <div class="gx-model-control">
              <label class="gx-row__label" for="gx-model-small">Active Small · On-device</label>
              <GalaxySelect id="gx-model-small" class="gx-field" :value="pendingChoice?.action === 'select-small' ? pendingChoice.value : activeSmallModel" :disabled="selectionUncertain || capabilities.select !== true || status.isOnroad || status.randomizer" @change="runAction('select-small', installedSmallModels.find(m => m.value === $event.target.value))">
                <option v-for="m in installedSmallModels" :key="m.value" :value="m.value" :disabled="!m.selectable">{{ m.label || m.value }}</option>
              </GalaxySelect>
            </div>

            <div class="gx-model-control">
              <label class="gx-row__label" for="gx-model-big">Active Big · GPU</label>
              <GalaxySelect id="gx-model-big" class="gx-field" :value="pendingChoice?.action === 'select-big' ? pendingChoice.value : activeBigModel" :disabled="selectionUncertain || capabilities.select !== true || status.isOnroad || status.randomizer" @change="$event.target.value ? runAction('select-big', installedBigModels.find(m => m.value === $event.target.value)) : runAction('select-big')">
                <option value="">{{ bigNoneLabel }}</option>
                <option v-for="m in installedBigModels" :key="m.value" :value="m.value" :disabled="!m.selectable">{{ m.label || m.value }}</option>
              </GalaxySelect>
            </div>

            <div class="gx-model-filter-bar" role="group" aria-label="Sort models">
              <span class="gx-row__label">Sort</span>
              <div class="gx-actions">
                <button type="button" class="gx-btn gx-btn--tonal" :aria-pressed="sortMode === 'release_date'" @click="sortMode = 'release_date'">Newest</button>
                <button type="button" class="gx-btn gx-btn--tonal" :aria-pressed="sortMode === 'alphabetical'" @click="sortMode = 'alphabetical'">Name</button>
              </div>
            </div>

            <div class="gx-model-filter-bar" role="group" aria-label="Show models">
              <span class="gx-row__label">Show models</span>
              <div class="gx-actions">
                <button type="button" class="gx-btn gx-btn--tonal" :aria-pressed="['both', 'comma'].includes(hardwareFilter)" @click="toggleHardware('comma', !['both', 'comma'].includes(hardwareFilter))">Small · On-device</button>
                <button type="button" class="gx-btn gx-btn--tonal" :aria-pressed="['both', 'gpu'].includes(hardwareFilter)" @click="toggleHardware('gpu', !['both', 'gpu'].includes(hardwareFilter))">Big · GPU</button>
                <button type="button" class="gx-btn gx-btn--tonal" :aria-pressed="userFilter === 'yes'" @click="userFilter = userFilter === 'yes' ? 'all' : 'yes'">Your favorites only</button>
                <button type="button" class="gx-btn gx-btn--tonal" :aria-pressed="communityFilter === 'yes'" @click="communityFilter = communityFilter === 'yes' ? 'all' : 'yes'">Community favorites only</button>
              </div>
            </div>
          </div>
        </section>

        <template v-if="!sorted.length">
          <div class="gx-card"><div class="gx-empty">No models match these filters. Select a model type or clear the favorite filters.</div></div>
        </template>
        <template v-else>
          <div class="gx-card-grid">
            <section class="gx-card gx-model-card" :class="{'gx-model-card--polaris': isPolaris(m)}" v-for="m in sorted" :key="m.value">
              <div class="gx-panel">
                <div class="gx-model-card__heading">
                  <strong v-if="isPolaris(m)"><span class="gx-model-card__title">{{ polarisTitleParts(m)[0] }}</span><span>{{ polarisTitleParts(m)[1] }}</span></strong>
                  <strong v-else>{{ m.label || m.value }}</strong>
                  <div class="gx-model-card__actions">
                    <button v-if="rowState(m) === 'available' && m.downloadAvailable" type="button" class="gx-icon-btn" :disabled="!actionAvailable('download', m)" :aria-label="'Download ' + m.label" :title="'Download ' + m.label" @click="runAction('download', m)"><i class="bi bi-download" aria-hidden="true"></i></button>
                    <button type="button" class="gx-icon-btn" :disabled="!actionAvailable(m.userFavorite ? 'unfavorite' : 'favorite', m)" :aria-label="(m.userFavorite ? 'Remove from your favorites: ' : 'Add to your favorites: ') + m.label" :title="m.userFavorite ? 'Remove from your favorites' : 'Add to your favorites'" @click="runAction(m.userFavorite ? 'unfavorite' : 'favorite', m)">
                      <i aria-hidden="true" class="bi" :class="m.userFavorite ? 'bi-star-fill' : 'bi-star'"></i>
                    </button>
                  </div>
                </div>
                <div class="gx-actions gx-tags">
                  <span v-if="isPolaris(m)" class="gx-chip gx-polaris-badge"><span class="gx-polaris-badge__star" aria-hidden="true">✦</span> StarPilot Custom</span>
                  <button v-if="isPolaris(m)" type="button" class="gx-icon-btn" aria-label="About Polaris models" title="About Polaris models" @click="polarisAbout = true"><i class="bi bi-question-circle" aria-hidden="true"></i></button>
                  <span v-if="m.communityFavorite" class="gx-chip">Community Favorite</span>
                  <span v-if="rowState(m) === 'active'" class="gx-chip gx-chip--selected">Selected</span>
                  <span class="gx-chip">{{ m.value }}</span>
                  <span v-if="m.builtin" class="gx-chip">Built-in</span>
                  <span class="gx-chip">{{ hardwareLabel(m) }}</span>
                  <span v-if="m.version" class="gx-chip" :aria-label="'Version '+ m.version">{{ m.version }}</span>
                  <span v-if="m.released" class="gx-chip" :aria-label="'Released '+ m.released">{{ m.released }}</span>
                  <span v-if="m.partial" class="gx-chip">Partial Files</span>
                  <span class="gx-chip">{{ fileSizeText(m) }}</span>
                </div>
              </div>
              <p v-if="m.unavailableReason && !(rowState(m) === 'available' && m.downloadAvailable && m.unavailableReason === 'Download required')" class="gx-model-reason gx-model-card__reason">{{ m.unavailableReason }}</p>
              <div v-if="status.randomizer || m.blacklisted || ['busy', 'cancellable', 'installed'].includes(rowState(m))" class="gx-panel-footer gx-actions">
                <div v-if="status.randomizer || m.blacklisted" class="gx-row gx-row--borderless" :class="{disabled: !actionAvailable(m.blacklisted ? 'include' : 'exclude', m)}"><div class="gx-row__info"><span class="gx-row__label">Include in Randomizer</span></div>
                  <label class="gx-switch"><input type="checkbox" role="switch" :aria-label="'Include ' + m.label + ' in Randomizer'" :checked="!m.blacklisted" :disabled="!actionAvailable(m.blacklisted ? 'include' : 'exclude', m)"
                    @change="$event.target.checked=!m.blacklisted; runAction(m.blacklisted ? 'include' : 'exclude', m)"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
                <template v-if="rowState(m) === 'busy'">
                  <span class="gx-chip"><i aria-hidden="true" class="bi bi-hourglass-split"></i> Busy</span>
                </template>
                <template v-else-if="rowState(m) === 'cancellable'">
                  <button type="button" class="gx-btn gx-btn--danger" :disabled="!actionAvailable('cancel', m)" @click="runAction('cancel', m)"><i aria-hidden="true" class="bi bi-x-circle"></i> Cancel</button>
                </template>
                <template v-else-if="rowState(m) === 'installed'">
                  <button type="button" class="gx-btn" :disabled="!actionAvailable(m.requiresGpu ? 'select-big' : 'select-small', m)" @click="runAction(m.requiresGpu ? 'select-big' : 'select-small', m)"><i aria-hidden="true" class="bi bi-play-fill"></i> Set Active {{ m.requiresGpu ? 'Big' : 'Small' }}</button>
                  <button v-if="!m.builtin" type="button" class="gx-btn gx-btn--tonal gx-text-danger" :disabled="!actionAvailable('delete', m)" @click="runAction('delete', m)"><i aria-hidden="true" class="bi bi-trash"></i> Delete</button>
                </template>
              </div>
            </section>
          </div>
        </template>
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

