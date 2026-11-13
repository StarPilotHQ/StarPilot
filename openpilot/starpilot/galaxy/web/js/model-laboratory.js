import { GxState } from "./state.js"
import { guardUnload } from "./unload-guard.js"
import { GxSummary } from "./summary.js"
import { GxNotice } from "./notice.js"
import { reportSave } from "./save-feedback.js"
import { GalaxySelect } from "./galaxy-select.js"
import { ModelManagerFeed } from "./models.js"

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
  if (!data || data.capabilities?.[action === "enable" || action === "disable" ? "configure" : action] !== true) return false
  if (action === "cancel") return data.download?.downloading === true && typeof data.download.jobId === "string"
  if (data.isOnroad !== false || data.download?.downloading) return false
  if (action === "enable") return !laboratorySelectionError(data, configuration)
  if (action === "disable") return data.configuration.enabled === true
  if (action === "download") return laboratoryModels(data).some(m => m.value === model?.value && m.modelLabEligible === true && m.modelLabArtifactAvailable === true && !m.modelLabArtifactInstalled)
  if (action === "delete") return model?.small === true && model.modelLabChestnut !== false && laboratoryReady(data).some(m => m.value === model?.value) &&
    !(data.configuration.enabled && [data.configuration.lateralModel, data.configuration.longitudinalModel].includes(model.value))
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

function validPayload(data) {
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
      this.data = validPayload(data) ? data : null
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
    const paths = { enable: "laboratory", disable: "laboratory", download: "laboratory/download", delete: "laboratory/delete", cancel: "cancel", refresh: "refresh_manifest" }
    const payload = action === "enable" || action === "disable" ? { ...configuration, enabled: action === "enable" } :
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

export const LaboratoryPage = {
  components: { GxState, GxSummary, GxNotice, GalaxySelect },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ loading: true, status: null, error: "", message: "", busy: false, dirty: false, trackingProgress: false,
    configuration: { enabled: false, lateralModel: "", longitudinalModel: "" } }),
  computed: {
    availableModels() { return laboratoryModels(this.status) },
    readyModels() { return laboratoryReady(this.status) },
    candidates() { return this.readyModels.filter(m => m.value !== this.configuration.lateralModel) },
    selectionError() { return laboratorySelectionError(this.status, this.configuration) },
  },
  created() {
    this.feed = new LaboratoryFeed({ unauthorized: this.unauthorized, publish: update => {
      this.loading = update.loading
      this.error = update.error
      this.status = update.data
      if (update.data) {
        this.configuration = laboratoryDraft(update.data, this.configuration, this.dirty)
        if (this.trackingProgress && update.data.download.progress) {
          this.message = update.data.download.progress
          if (!update.data.download.downloading) this.trackingProgress = false
        }
      }
    } })
  },
  mounted() {
    this._stopUnloadGuard = guardUnload(() => this.dirty || this.busy)
    this.visibility = () => { if (document.hidden) this.feed.stop(); else if (this.mode === "local") this.feed.start() }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local" && !document.hidden) this.feed.start()
  },
  beforeUnmount() { this._stopUnloadGuard(); document.removeEventListener("visibilitychange", this.visibility); this.feed.stop() },
  methods: {
    reportSave,
    statusLabel: laboratoryStatusLabel,
    actionAvailable(action, model = null) { return laboratoryActionAllowed(this.status, action, model, this.configuration) },
    can(action, model = null) { return !this.busy && this.actionAvailable(action, model) },
    changeLateral(value) {
      this.configuration.lateralModel = value
      if (this.configuration.longitudinalModel === value) this.configuration.longitudinalModel = this.candidates[0]?.value || ""
      this.dirty = true
    },
    async act(action, model = null) {
      if (!this.can(action, model)) return
      if (action === "delete" && !window.confirm(`Delete the eGPU variant for "${model.label}"? The normal on-device model will not be removed.`)) return
      this.busy = true
      this.message = ""
      try {
        const result = await this.feed.action(action, model, this.configuration)
        if (result) {
          this.message = result.message || "Updated"
          this.trackingProgress = action === "download" || action === "cancel"
          if (["enable", "disable", "delete"].includes(action)) {
            this.dirty = false
            if (this.status) this.configuration = laboratoryDraft(this.status, this.configuration, false)
          }
        }
        return result
      } finally { this.busy = false }
    },
  },
  template: `
    <div class="gx-settings gx-models" :inert="busy" :aria-busy="busy || undefined">
      <header class="gx-settings__header gx-page-header"><div><h2>Model Laboratory</h2>
        <p>Pair lateral judgment from one model with longitudinal judgment from another. Enabling applies when modeld next starts, when it loads and verifies the model weights, input tensors and GPU state.</p></div>
        <div class="gx-actions"><span class="gx-chip">{{ status ? (status.chestnutReady ? 'Chestnut ready' : 'Chestnut required') : 'Waiting for device status' }}</span><span v-if="status" class="gx-chip">{{ status.isOnroad ? 'Onroad' : 'Parked' }}</span></div></header>
      <GxState v-if="mode !== 'local'">Model Laboratory is available on the device. Preview does not configure a pair.</GxState>
      <GxState v-else-if="loading" loading>Loading Model Laboratory…</GxState>
      <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
      <GxNotice tone="danger" v-if="status?.configurationError">{{ status.configurationError }}</GxNotice>
      <GxNotice v-if="status?.runtime.error" tone="danger">{{ status.runtime.error }}</GxNotice>
      <GxNotice v-if="message" tone="info">{{ message }}</GxNotice>
      <template v-if="mode === 'local' && !loading">
      <section class="gx-card">
        <div class="gx-section__header"><i class="bi bi-collection"></i><span class="gx-section__title">Compose a pair</span><span class="gx-chip">{{ configuration.enabled ? 'Enabled' : 'Disabled' }}</span></div>
        <div class="gx-panel gx-stack">
          <label class="gx-field-group"><strong>Lateral model</strong><small>Path shape, curvature, lane geometry, and driving desire</small><GalaxySelect class="gx-field" aria-label="Lateral model" :disabled="!status || status.isOnroad || status.download.downloading" :value="configuration.lateralModel" @change="changeLateral($event.target.value)"><option value="">Choose a model</option><option v-for="m in readyModels" :key="m.value" :value="m.value">{{ m.label }} · {{ m.version }}</option></GalaxySelect></label>
          <label class="gx-field-group"><strong>Longitudinal model</strong><small>Speed, acceleration, stopping, leads, and scene confidence</small><GalaxySelect class="gx-field" aria-label="Longitudinal model" :disabled="!status || status.isOnroad || status.download.downloading" :value="configuration.longitudinalModel" @change="configuration.longitudinalModel=$event.target.value;dirty=true"><option value="">Choose a model</option><option v-for="m in candidates" :key="m.value" :value="m.value">{{ m.label }} · {{ m.version }}</option></GalaxySelect></label>
          <p class="gx-row__desc">{{ modelLabel(configuration.lateralModel) }} steers · {{ modelLabel(configuration.longitudinalModel) }} paces</p>
          <GxNotice v-if="selectionError" tone="danger">{{ selectionError }}</GxNotice>
          <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Enable model pair</span></div>
            <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Enable model pair" :checked="status?.configuration.enabled === true"
              :disabled="busy || !actionAvailable(status?.configuration.enabled ? 'disable' : 'enable')"
              @change="$event.target.checked=status.configuration.enabled; reportSave(() => act(status.configuration.enabled ? 'disable' : 'enable'))"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
          <div class="gx-actions"><button class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('refresh')" @click="act('refresh')">Check model catalog</button></div>
        </div>
      </section>
      <section class="gx-card">
        <div class="gx-section__header"><i class="bi bi-download"></i><span class="gx-section__title">Available models</span></div>
        <GxSummary :items="[{label: 'Catalog models', value: availableModels.length}, {label: 'Published versions', value: status?.summary?.published || 0}, {label: 'Verified downloads', value: readyModels.length}]" />
        <p class="gx-note gx-inset">Browse Small and Big models here. Combining models requires compatible Chestnut downloads and support for running them together.</p>
        <p class="gx-note gx-inset">Pairing increases inference time. Two Big models cannot run together at the required speed. Choose a Small model for one role. Other combinations can also cause model lag warnings. Small downloads prepare the available variants. When available, the comma GPU runs one Small role while Chestnut runs the other role.</p>
        <p v-if="status && !availableModels.length" class="gx-note gx-inset">The model catalog is unavailable. Refresh to retry.</p>
        <article v-for="m in availableModels" :key="m.value" class="gx-row gx-row--wrap"><div class="gx-row__info gx-row__info--wide"><span class="gx-row__label">{{ m.label }}</span><span class="gx-row__desc">{{ m.value }} · {{ m.small ? 'Small' : 'Big' }} · {{ m.series || 'Unknown series' }} · {{ m.version }}</span><span class="gx-row__desc">{{ m.modelLabReason || 'Pair artifact status unavailable' }}</span></div><div class="gx-actions"><span class="gx-chip">{{ statusLabel(m) }}</span><button v-if="m.modelLabEligible && m.modelLabArtifactAvailable && !m.modelLabArtifactInstalled" class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('download',m)" @click="act('download',m)">{{ m.small ? (m.modelLabChestnut === false ? 'Download Small model' : 'Download pair variants') : 'Download Big model' }}</button><button v-else-if="m.small && m.modelLabChestnut !== false && m.modelLabArtifactInstalled" class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('delete',m)" @click="act('delete',m)">Delete eGPU variant</button></div></article>
        <div v-if="status?.download.downloading" class="gx-row"><div class="gx-row__info"><span class="gx-row__desc">{{ status.download.progress || 'Downloading…' }}</span></div><button class="gx-btn gx-btn--tonal" :disabled="!actionAvailable('cancel')" @click="act('cancel')">Cancel download</button></div>
      </section>
      </template>
    </div>`,
}
