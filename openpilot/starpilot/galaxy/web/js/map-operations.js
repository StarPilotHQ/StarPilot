import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { connectionError } from "./polling.js"
import { requestJson } from "./startup.js"
// One parked map operation belongs to the local owner, not to this browser tab.
const STATES = new Set(["idle", "transferring", "validating", "selecting", "completed", "canceled", "failed", "interrupted", "unavailable"])
const GENERATION = /^(?:|[0-9a-f]{64})$/
const TOKEN = /^(?:nation|us_state)\.[A-Za-z0-9_-]+$/
const ERROR_LABELS = {
  canceled: "Canceled", interrupted: "Interrupted", selection_changed: "The saved selection changed",
  not_parked: "Parked availability was lost", package_unavailable: "The map package is unavailable",
  process_failed: "Map preparation failed", unavailable: "Map manager unavailable",
}
const ACTION_ERRORS = {
  busy: "Another map download is running. Check its progress below.",
  selection_changed: "The selected map changed. Refresh and review the region again.",
  not_parked: "Map downloads need a fresh parked status. Refresh and try again.",
  operation_changed: "This map download changed. Refresh its status before trying again.",
}
// 503 codes that mean "not ready yet" rather than a hard failure.
const SERVICE_PENDING = new Set(["owner_unavailable", "unavailable"])
const integer = (value) => Number.isSafeInteger(value) && value >= 0
const nullableText = (value) => value === null || typeof value === "string" && value.length <= 128
const bounds = (value) => value === null || Array.isArray(value) && value.length === 4 &&
  value.every((n) => typeof n === "number" && Number.isFinite(n)) &&
  value[0] >= -90 && value[2] <= 90 && value[1] >= -180 && value[3] <= 180 &&
  value[0] < value[2] && value[1] < value[3]

export function validCatalog(value) {
  if (!Array.isArray(value?.regions) || value.regions.length > 500 ||
      !integer(value.maxGroups) || value.maxGroups < 1 || !integer(value.maxTransferBytes) ||
      !integer(value.maxNewDiskBytes) || !GENERATION.test(value.selectedGeneration ?? "!")) return false
  const seen = new Set()
  return value.regions.every((region) => {
    if (!TOKEN.test(region?.token) || region.token.length > 73 || seen.has(region.token) || typeof region.name !== "string" ||
        !region.name.trim() || region.name.length > 128 || (region.available && (!bounds(region.bounds) || region.bounds === null)) ||
        !integer(region.groups) || typeof region.available !== "boolean" ||
        !(region.unavailable == null || ["invalid_bounds", "date_line_bounds", "too_large"].includes(region.unavailable)) ||
        (region.available && (region.unavailable != null || region.groups > value.maxGroups)) ||
        (!region.available && !["invalid_bounds", "date_line_bounds", "too_large"].includes(region.unavailable))) return false
    seen.add(region.token)
    return true
  })
}

export function validSetup(value) {
  return value?.schemaVersion === 1 && typeof value.packageReady === "boolean" &&
    ["ready", "missing_binary", "missing_manifest", "invalid_package"].includes(value.packageState) &&
    value.packageReady === (value.packageState === "ready") && typeof value.snapshotReady === "boolean" &&
    GENERATION.test(value.selectedGeneration ?? "!") && value.snapshotReady === Boolean(value.selectedGeneration) &&
    typeof value.parked === "boolean" && integer(value.freeDiskBytes) &&
    integer(value.maxTransferBytes) && integer(value.maxNewDiskBytes)
}

const validSnapshot = (value, generation) => Boolean(value && value.generation === generation &&
  integer(value.storedBytes) && value.storedBytes > 0 && integer(value.tileCount) && value.tileCount > 0 && value.bounds !== null && bounds(value.bounds))

export function validOperation(value) {
  return value?.schemaVersion === 1 && STATES.has(value.state) &&
    typeof value.ownerSession === "string" && value.ownerSession.length <= 128 &&
    nullableText(value.operationId) && nullableText(value.regionToken) && bounds(value.bounds) &&
    integer(value.completedGroups) && integer(value.totalGroups) && value.completedGroups <= value.totalGroups &&
    integer(value.transferredBytes) && integer(value.transferBudgetBytes) &&
    nullableText(value.preparedGeneration) && GENERATION.test(value.selectedGeneration ?? "!") &&
    nullableText(value.errorCode) && typeof value.selectedForNextShadowStart === "boolean" &&
    (value.selectedSnapshot == null || validSnapshot(value.selectedSnapshot, value.selectedGeneration)) &&
    (!value.selectedForNextShadowStart || value.state === "completed" && validSnapshot(value.selectedSnapshot, value.selectedGeneration))
}

export const formatBytes = (value) => !integer(value) ? "Unavailable" : value < 1024 ? `${value} B` :
  value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB` :
  value < 1024 ** 3 ? `${(value / (1024 ** 2)).toFixed(1)} MiB` : `${(value / (1024 ** 3)).toFixed(1)} GiB`
export const formatBounds = (value) => bounds(value) && value !== null ?
  `latitude ${value[0]}°–${value[2]}°, longitude ${value[1]}°–${value[3]}°` : "extent unavailable"

export class MapOperationsClient {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.requests = new Set()
    this.timer = null
    this.setup = null
    this.catalog = null
    this.operation = null
    this.busy = false
    this.error = ""
    this.readErrors = {}
    this.actionError = ""
  }

  emit(update = {}) {
    this.publish({ setup: this.setup, catalog: this.catalog, operation: this.operation, busy: this.busy,
      error: this.actionError || this.error, ...update })
  }

  stop() {
    this.active = false
    this.generation++
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.timer = null
    for (const request of this.requests) request.abort()
    this.requests.clear()
    this.setup = this.catalog = this.operation = null
    this.busy = false
    this.error = this.actionError = ""
    this.readErrors = {}
    this.emit()
  }

  start() {
    this.stop()
    this.active = true
    this.loadCatalog()
    this.loadStatus()
    this.loadSetup()
  }

  async request(url, body = null) {
    const generation = this.generation
    const controller = new AbortController()
    this.requests.add(controller)
    try {
      const { response, data: payload } = await requestJson(url, { fetcher: this.fetcher, later: this.later, cancel: this.cancelTimer, timeout: 10000, withResponse: true, request: { credentials: "same-origin", cache: "no-store",
        signal: controller.signal, ...(body === null ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) } })
      if (!this.active || generation !== this.generation || controller.signal.aborted) return null
      if (response.status === 401) { this.stop(); this.unauthorized(); return null }
      if (response.status === 409) {
        const body = payload
        if (!this.active || generation !== this.generation || controller.signal.aborted) return null
        const message = Object.hasOwn(ACTION_ERRORS, body?.code) ? ACTION_ERRORS[body.code] :
          "Map request could not be completed. Refresh status and try again."
        throw new Error(message)
      }
      if (response.status === 503) {
        const body = payload
        if (!this.active || generation !== this.generation || controller.signal.aborted) return null
        if (["setup_required", "access_unavailable"].includes(body?.code)) {
          this.stop(); this.unauthorized(); return null
        }
        if (body?.code === "package_unavailable")
          throw new Error("Offline map package is unavailable on this device.")
        if (SERVICE_PENDING.has(body?.code))
          throw new Error("Offline maps are starting up. Reconnecting automatically…")
        throw new Error(typeof body?.error === "string" && body.error ? body.error : "Offline maps are unavailable. Try again shortly.")
      }
      if (!response.ok) throw new Error("Map request was rejected.")
      const data = payload
      if (!this.active || generation !== this.generation || controller.signal.aborted) return null
      return data
    } finally {
      this.requests.delete(controller)
    }
  }

  readResult(source, error = null) {
    this.readErrors[source] = error ? connectionError(error) : ""
    this.error = Object.values(this.readErrors).find(Boolean) || ""
    this.emit()
  }

  async loadSetup() {
    if (!this.active) return
    const generation = this.generation
    try {
      const data = await this.request("./api/maps/setup")
      if (data === null) return
      if (!validSetup(data)) throw new Error("Map setup status is unavailable.")
      const wasReady = this.setup?.packageReady
      this.setup = data
      this.readResult("setup")
      if (data.packageReady && wasReady === false) await this.loadCatalog()
    } catch (error) { if (this.active && generation === this.generation) this.readResult("setup", error) }
  }

  async loadCatalog() {
    if (!this.active) return
    const generation = this.generation
    try {
      const data = await this.request("./api/maps/catalog")
      if (data === null) return
      if (!validCatalog(data)) throw new Error("Map catalog is unavailable.")
      this.catalog = data
      this.readResult("catalog")
    } catch (error) { if (this.active && generation === this.generation) this.readResult("catalog", error) }
  }

  async loadStatus() {
    if (!this.active || this.busy) return
    const generation = this.generation
    try {
      const data = await this.request("./api/maps/operation")
      if (data !== null) {
        if (!validOperation(data)) throw new Error("Map operation status is unavailable.")
        this.operation = data
        this.readResult("operation")
      }
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.operation = null; this.readResult("operation", error)
      }
    } finally {
      if (this.active && generation === this.generation && !this.busy)
        this.timer = this.later(() => {
          this.timer = null
          this.loadStatus()
          if (this.readErrors.catalog) this.loadCatalog()
          if (this.readErrors.setup) this.loadSetup()
        }, 2500)
    }
  }

  pausePolling() {
    this.generation++
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.timer = null
    for (const request of this.requests) request.abort()
    this.requests.clear()
  }

  async startRegion(region, expectedGeneration, expectedOwnerSession) {
    if (!this.active || this.busy || !this.catalog || !this.operation || !region?.available ||
        !this.catalog.regions.some((item) => item.token === region.token && item.available) ||
        !["idle", "completed", "canceled", "failed", "interrupted"].includes(this.operation.state)) {
      this.actionError = "Map operation changed. Refresh status and review again."
      this.emit()
      return
    }
    if (this.operation.selectedGeneration !== expectedGeneration || this.operation.ownerSession !== expectedOwnerSession) {
      this.actionError = "Map selection changed. Review this region again."
      this.emit()
      return
    }
    const expected = this.operation.selectedGeneration
    this.pausePolling()
    const generation = this.generation
    this.busy = true
    this.actionError = ""
    this.emit()
    try {
      const data = await this.request("./api/maps/start", { regionToken: region.token,
        maxTransferBytes: this.catalog.maxTransferBytes, maxNewDiskBytes: this.catalog.maxNewDiskBytes,
        expectedCurrentGeneration: expected })
      if (data === null) return
      if (!validOperation(data)) throw new Error("Map start status is unavailable. Refresh status.")
      this.operation = data
      this.emit()
    } catch (error) { if (this.active && generation === this.generation) { this.actionError = error.message; this.emit() } }
    finally { if (this.active && generation === this.generation) { this.busy = false; this.emit(); await this.loadStatus() } }
  }

  async cancel() {
    if (!this.active || this.busy || !this.operation?.operationId ||
        !["transferring", "validating", "selecting"].includes(this.operation.state)) return
    const operationId = this.operation.operationId
    this.pausePolling()
    const generation = this.generation
    this.busy = true
    this.actionError = ""
    this.emit()
    try {
      const data = await this.request("./api/maps/cancel", { operationId })
      if (data === null) return
      if (!validOperation(data)) throw new Error("Cancellation status is unavailable. Refresh status.")
      this.operation = data
      this.emit()
    } catch (error) { if (this.active && generation === this.generation) { this.actionError = error.message; this.emit() } }
    finally { if (this.active && generation === this.generation) { this.busy = false; this.emit(); await this.loadStatus() } }
  }
}

export const MapOperationsPanel = {
  components: { GxDialog, GxNotice },
  name: "MapOperationsPanel",
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ setup: null, catalog: null, operation: null, busy: false, error: "", search: "", review: null }),
  created() { this.client = new MapOperationsClient({ publish: (update) => {
    Object.assign(this.$data, update)
    if (update.operation === null || update.error) this.review = null
  }, unauthorized: () => { this.review = null; this.unauthorized() } }) },
  mounted() {
    this.visibility = () => { if (document.hidden) { this.review = null; this.client.stop() }
      else if (this.mode === "local") this.client.start() }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local" && !document.hidden) this.client.start()
  },
  beforeUnmount() { document.removeEventListener("visibilitychange", this.visibility); this.client.stop() },
  computed: {
    regions() {
      const query = this.search.trim().toLocaleLowerCase()
      return (this.catalog?.regions || []).filter((item) => !query ||
        `${item.name} ${item.token}`.toLocaleLowerCase().includes(query))
    },
    running() { return ["transferring", "validating", "selecting"].includes(this.operation?.state) },
  },
  methods: {
    formatBytes, formatBounds,
    errorLabel(code) { return ERROR_LABELS[code] || "Review this operation in Galaxy" },
    reviewRegion(region) {
      if (!this.busy && region.available && this.operation)
        this.review = { region, selectedGeneration: this.operation.selectedGeneration, ownerSession: this.operation.ownerSession }
    },
    async confirm() {
      const review = this.review
      this.review = null
      if (review) await this.client.startRegion(review.region, review.selectedGeneration, review.ownerSession)
    },
  },
  template: `
    <section class="gx-card gx-map-manager" :inert="busy" :aria-busy="busy || undefined">
      <div class="gx-section__header"><i class="bi bi-map"></i><span class="gx-section__title">Speed Limit Maps</span></div>
      <div class="gx-map-manager__body">
        <p class="gx-note">Offline speed-limit and curve data. Download another region to add coverage. Previously downloaded regions stay available.</p>
        <p v-if="mode !== 'local'">Parked map management is unavailable in the offline preview.</p>
        <template v-else>
          <template v-if="setup"><p role="status">{{ setup.packageReady ? 'Map service ready.' : 'Map service setup required.' }} {{ setup.snapshotReady ? 'An offline map database is saved and selected for the next Maps service start.' : setup.packageReady ? 'Choose a region to download its maps.' : 'Update device software to set up offline maps.' }}</p>
            <p v-if="!setup.packageReady" class="gx-note">The offline map service is unavailable. Update device software, then retry maps. Your saved map selection is retained.</p>
            <p class="gx-note">{{ formatBytes(setup.freeDiskBytes) }} available storage. {{ setup.parked ? 'Parked downloads available.' : 'Park to download maps.' }}</p></template>
          <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>

          <p v-if="!operation && !error" role="status">Loading map operation…</p>
          <template v-if="operation">
            <dl class="gx-details"><dt>Operation</dt><dd>{{ operation.state }}</dd>
              <dt>Selected map</dt><dd>{{ operation.selectedGeneration ? 'Saved selection' : 'None' }}</dd>
              <dt v-if="operation.regionToken">Region</dt><dd v-if="operation.regionToken">{{ operation.regionToken }}</dd>
              <dt v-if="running">Progress</dt><dd v-if="running">{{ operation.completedGroups }} of {{ operation.totalGroups }} areas prepared</dd>
              <dt v-if="running">Transferred</dt><dd v-if="running">{{ formatBytes(operation.transferredBytes) }} of {{ formatBytes(operation.transferBudgetBytes) }} cap</dd></dl>
            <dl v-if="operation.selectedSnapshot" class="gx-details"><dt>Offline map database</dt><dd>{{ formatBytes(operation.selectedSnapshot.storedBytes) }} · {{ operation.selectedSnapshot.tileCount.toLocaleString() }} tiles</dd>
              <dt>Package</dt><dd>{{ operation.selectedGeneration.slice(0, 12) }}</dd>
              <dt>Map extent</dt><dd>{{ formatBounds(operation.selectedSnapshot.bounds) }}</dd></dl>
            <p v-if="operation.selectedForNextShadowStart" class="gx-note">Download complete and saved on this comma. The map database is selected for the next Maps service start.</p>
            <p v-if="operation.selectedGeneration && !operation.selectedForNextShadowStart" class="gx-note">An offline map database is saved and selected for the next Maps service start.</p>
            <GxNotice v-if="operation.errorCode" tone="danger">{{ errorLabel(operation.errorCode) }}.</GxNotice>
            <button v-if="running" type="button" class="gx-btn gx-btn--tonal" @click="client.cancel()">Cancel operation</button>
          </template>
          <div class="gx-map-manager__picker"><label for="map-region-search">Search regions</label>
            <input id="map-region-search" class="gx-field" type="search" v-model="search" placeholder="Country or US state"></div>
          <p v-if="!catalog && setup?.packageReady && !error" role="status">Loading map catalog…</p>
          <template v-if="catalog"><p class="gx-note">{{ regions.length }} regions shown · Up to {{ formatBytes(catalog.maxTransferBytes) }} transfer · {{ formatBytes(catalog.maxNewDiskBytes) }} new storage.</p>
            <div class="gx-map-manager__regions">
              <div v-for="region in regions" :key="region.token" class="gx-map-manager__region">
                <div><strong>{{ region.name }}</strong><small>{{ region.token }} · {{ region.groups }} groups</small>
                  <small v-if="!region.available">Unavailable: {{ region.unavailable.replaceAll('_', ' ') }}</small></div>
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="running || !region.available || !operation" @click="reviewRegion(region)">Review…</button>
              </div>
            </div></template>
        </template>
      </div>
      <GxDialog v-if="review" :inert="busy" labelledby="gx-map-operations-confirm-title" @close="review=null"><h3 id="gx-map-operations-confirm-title">Add Offline Coverage?</h3>
          <p><strong>{{ review.region.name }}</strong> covers an approximate rectangle ({{ formatBounds(review.region.bounds) }}), {{ review.region.groups }} groups. This can transfer up to {{ formatBytes(catalog.maxTransferBytes) }} and use up to {{ formatBytes(catalog.maxNewDiskBytes) }} of new disk space. Existing regions are kept; overlapping areas are refreshed. The combined map is selected for the next map service start.</p>
          <div class="gx-settings__controls gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="review=null">Cancel</button>
            <button type="button" class="gx-btn" :disabled="running || !operation" @click="confirm">Start download</button></div>
        </GxDialog>
    </section>`,
}
