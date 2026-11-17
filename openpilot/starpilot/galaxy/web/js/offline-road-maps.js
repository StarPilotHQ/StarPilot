import { markRaw } from "../vendor/vue/vue.esm-browser.js"
import { GxNotice } from "./notice.js"
import { PollTimer, connectionError } from "./polling.js"
import { RasterMap } from "./navigation-map.js"
import { BluetoothNotice, bluetoothConnection } from "./bluetooth-notice.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"

// Road-line maps for the Android Auto overlay: areas saved on the comma, save-as-you-drive, and the downloader's progress.
const object = (value) => !!value && typeof value === "object" && !Array.isArray(value)
const finite = (value) => typeof value === "number" && Number.isFinite(value)
const STATES = new Set(["queued", "downloading", "waiting_wifi", "complete", "incomplete", "no_space"])
const KM_PER_MILE = 1.609344
const START_ZOOM = 12  // a few miles across: streets readable, a typical area still fits after zooming out

// Areas are stored in km; imperial devices pick and show whole miles.
export function radiusLabel(radiusKm, metric) {
  return metric ? `${Math.round(radiusKm)} km` : `${Math.round(radiusKm / KM_PER_MILE)} mi`
}
export function radiusRange(constants, metric) {
  if (metric) return { min: constants.minRadiusKm, max: constants.maxRadiusKm, initial: constants.defaultRadiusKm }
  return { min: Math.ceil(constants.minRadiusKm / KM_PER_MILE), max: Math.floor(constants.maxRadiusKm / KM_PER_MILE), initial: 15 }
}
export const toKm = (value, metric) => metric ? value : Math.round(value * KM_PER_MILE * 10) / 10

export function formatBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0)
  if (value < 1024 * 1024) return `${Math.max(value ? 1 : 0, Math.round(value / 1024))} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(value < 10 * 1024 ** 2 ? 1 : 0)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

// Mirrors offline_roads.estimate: a circle of z14 tiles.
export function estimateArea(latitude, radiusKm, constants) {
  const sideKm = 40075.016686 * Math.cos(latitude * Math.PI / 180) / 16384
  const tiles = Math.max(1, Math.round(Math.PI * (radiusKm / sideKm + 0.5) ** 2))
  return { tiles, downloadBytes: tiles * constants.averageDownloadBytes, storedBytes: tiles * constants.averageStoredBytes }
}

export function validOffline(data) {
  const point = (value) => object(value) && finite(value.latitude) && Math.abs(value.latitude) <= 90 && finite(value.longitude) && Math.abs(value.longitude) <= 180
  const constants = data?.constants
  return object(data) && Array.isArray(data.areas) && data.areas.length <= 64 && object(data.settings) && typeof data.settings.saveDriven === "boolean" &&
    (data.position === null || point(data.position)) && object(data.service) && typeof data.service.running === "boolean" &&
    object(constants) && ["minRadiusKm", "maxRadiusKm", "defaultRadiusKm", "averageDownloadBytes", "averageStoredBytes", "maxAreas"].every((key) => finite(constants[key])) &&
    data.areas.every((area) => point(area) && typeof area.id === "string" && /^[0-9a-f]{32}$/.test(area.id) &&
      typeof area.name === "string" && area.name.length <= 80 && finite(area.radiusKm) &&
      (area.progress === null || object(area.progress) && STATES.has(area.progress.state) && finite(area.progress.total) && finite(area.progress.done)))
}

export function areaLabel(area) {
  const progress = area.progress
  if (!progress) return "Waiting for the downloader"
  const percent = progress.total ? Math.floor(progress.done / progress.total * 100) : 0
  return ({
    queued: "Queued", downloading: `Downloading · ${percent}%`, waiting_wifi: `Waiting for Wi-Fi · ${percent}%`,
    complete: "Saved for offline", incomplete: `${progress.failed} tiles missing · retries later`, no_space: "Storage full"
  })[progress.state]
}

export class OfflineRoadsClient {
  constructor({ publish, unauthorized = () => { }, fetcher = (...args) => fetch(...args),
    later = (fn, ms) => setTimeout(fn, ms), cancel = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancel })
    this.data = null
    this.busy = false
    this.error = ""
    this.active = false
    this.generation = 0
    this.poller = new PollTimer({ read: () => this.load(), interval: 3000, later, cancel })
  }

  emit() { this.publish({ data: this.data, busy: this.busy, error: this.error }) }
  start() { this.stop(); this.active = true; this.poller.start(); return this.load() }
  stop() { this.active = false; this.generation++; this.controller?.abort(); this.busy = false; this.poller.stop() }

  async request(body = null) {
    this.controller?.abort()
    const generation = ++this.generation, controller = this.controller = new AbortController()
    const deadline = this.later(() => controller.abort(), 10000)
    try {
      const response = await this.fetcher("./api/navigation/offline", {
        credentials: "same-origin", cache: "no-store",
        signal: controller.signal,
        ...(body === null ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      })
      const payload = await response.json().catch(() => null)
      if (!this.active || generation !== this.generation) return null
      if (controller.signal.aborted) throw new Error("Offline maps did not respond. Try again shortly.")
      if (response.status === 401 || ["access_unavailable", "setup_required"].includes(payload?.code)) {
        this.stop(); this.unauthorized(); return null
      }
      if (!response.ok) throw new Error(payload?.error || "Offline maps are unavailable. Try again shortly.")
      if (!validOffline(payload)) throw new Error("Offline maps returned an unexpected answer. Reload to try again.")
      return payload
    } catch (error) {
      if (this.active && generation === this.generation) throw error
      return null
    } finally { this.cancel(deadline) }
  }

  async load() {
    if (!this.active || this.busy) return
    try {
      const data = await this.request()
      if (data && this.active && !this.busy) { this.data = data; this.error = ""; this.emit() }
    } catch (error) { if (this.active) { this.error = connectionError(error); this.emit() } }
  }

  async action(body) {
    if (!this.active || this.busy) return null
    this.busy = true
    this.error = ""
    this.emit()
    const generation = this.generation + 1
    try {
      const data = await this.request(body)
      if (data) this.data = data
      return data
    } catch (error) { this.error = connectionError(error); return null }
    finally { if (generation === this.generation) { this.busy = false; if (this.active) this.emit() } }
  }
}

export const OfflineRoadMapsPanel = {
  name: "OfflineRoadMapsPanel",
  components: { GxNotice, BluetoothNotice, GalaxySettingRow },
  props: {
    mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    hasKey: { type: Boolean, default: false }, metric: { type: Boolean, default: true }
  },
  data: () => ({ bluetooth: bluetoothConnection(), data: null, busy: false, error: "", point: null, name: "", radius: null, confirmDelete: null, notice: "" }),
  created() { this.client = new OfflineRoadsClient({ publish: (update) => Object.assign(this.$data, update), unauthorized: this.unauthorized }) },
  mounted() {
    if (this.mode !== "local") return
    this.visibility = () => document.hidden ? this.client.stop() : this.client.start()
    document.addEventListener("visibilitychange", this.visibility)
    if (!document.hidden) this.client.start()
    this.ensureMap()
  },
  beforeUnmount() { document.removeEventListener("visibilitychange", this.visibility); this.client.stop(); this.map?.close() },
  watch: {
    hasKey() { this.$nextTick(() => this.ensureMap()) },
    data(value) {
      if (value && this.radius === null) this.radius = radiusRange(value.constants, this.metric).initial
      if (value?.position && this.map && !this.map.initialized) {
        this.map.center = value.position; this.map.zoom = START_ZOOM; this.map.initialized = true
      }
      this.drawOverlays()
    },
    metric() { if (this.constants) this.radius = radiusRange(this.constants, this.metric).initial },
    point() { this.drawOverlays() },
    radius() { this.drawOverlays() },
  },
  computed: {
    constants() { return this.data?.constants },
    range() { return this.constants ? radiusRange(this.constants, this.metric) : null },
    radiusKm() { return this.radius === null ? null : toKm(this.radius, this.metric) },
    estimate() { return this.point && this.constants ? estimateArea(this.point.latitude, this.radiusKm, this.constants) : null },
    service() { return this.data?.service || {} },
    serviceLabel() {
      const service = this.service, areas = this.data?.areas || []
      if (!this.data) return "Checking…"
      if (!service.running) return "Not running"
      if (service.failure === "key") return "Mapbox key rejected"
      if (service.failure === "budget") return "Paused until the 1st"
      if (service.noSpace) return "Storage full"
      if (areas.some((area) => area.progress?.state === "downloading")) return "Downloading"
      if (areas.some((area) => area.progress?.state === "waiting_wifi")) return "Waiting for Wi-Fi"
      return service.network === "offline" ? "Offline" : "Ready"
    },
    storage() {
      const bytes = this.data?.bytes, limits = this.data?.limits
      if (!bytes) return null
      const used = (bytes.saved || 0) + (bytes.driven || 0) + (bytes.cache || 0)
      const cap = limits ? (limits.saved || 0) + (limits.driven || 0) + (limits.cache || 0) : 0
      return { used, cap, parts: [["Areas", bytes.saved || 0, "#9d72ff"], ["Driven", bytes.driven || 0, "#34c778"], ["Recent", bytes.cache || 0, "#4096ff"]] }
    },
    usage() { const usage = this.data?.usage || {}; return { tiles: usage.tiles || 0, free: usage.freeTiles || 200000 } },
    // This counter covers this comma, not other uses of the same key.
    freeTilesLeft() { return Math.max(0, Math.floor(this.usage.free * 0.99) - this.usage.tiles) },
    saveAvailable() { return this.mode === "local" && !!this.point && !!this.data && this.data.areas.length < this.constants.maxAreas },
    canSave() { return this.saveAvailable && !this.busy },
  },
  methods: {
    formatBytes, areaLabel, radiusLabel, switchRow,
    ensureMap() {
      if (this.bluetooth || this.map || !this.hasKey || !this.$refs.canvas) return
      this.map = markRaw(new RasterMap(this.$refs.canvas, () => { }))
      this.map.onTap = (point) => this.pick({ ...point, name: "" })
      this.map.update({}, false)
      if (this.data?.position) { this.map.center = this.data.position; this.map.zoom = START_ZOOM; this.map.initialized = true }
      this.overlaySignature = null
      this.drawOverlays()
    },
    zoom(delta) { this.map?.changeZoom(delta) },
    pick(point) {
      this.point = { latitude: point.latitude, longitude: point.longitude }
      this.name = point.name || ""
      this.notice = ""
    },
    useLocation() { if (this.data?.position) { this.pick({ ...this.data.position, name: "Around my car" }); this.focus(this.point, this.radiusKm) } },
    focus(point, radiusKm) {
      if (!this.map) return
      this.map.center = { latitude: point.latitude, longitude: point.longitude }
      const pixels = Math.min(this.$refs.canvas.clientWidth, this.$refs.canvas.clientHeight) * 0.42
      const metersPerPixel = radiusKm * 1000 / Math.max(40, pixels)
      const zoom = Math.log2(40075016.686 * Math.cos(point.latitude * Math.PI / 180) / (512 * metersPerPixel))
      this.map.zoom = Math.max(3, Math.min(14, Math.floor(zoom)))
      this.map.requestDraw()
    },
    drawOverlays() {
      if (!this.map) return
      const saved = (this.data?.areas || []).map((area) => ({
        ...area, stroke: area.progress?.state === "complete" ? "#34c778" : "#9d72ff",
        fill: area.progress?.state === "complete" ? "#34c77822" : "#9d72ff1f"
      }))
      const draft = this.point ? [{ ...this.point, radiusKm: this.radiusKm, stroke: "#f5b642", fill: "#f5b64226", dash: [8, 6], width: 3 }] : []
      const overlays = [...saved, ...draft], signature = JSON.stringify(overlays)
      if (signature === this.overlaySignature) return   // polls usually change nothing on the map
      this.overlaySignature = signature
      this.map.overlays = overlays
      this.map.requestDraw()
    },
    async save() {
      if (!this.canSave) return
      const data = await this.client.action({ action: "addArea", area: { ...this.point, radiusKm: Number(this.radiusKm), name: this.name.trim() } })
      if (data) { this.point = null; this.name = ""; this.notice = "Area saved. It downloads on Wi-Fi; you can leave this page." }
    },
    async remove(area) {
      this.confirmDelete = null
      await this.client.action({ action: "deleteArea", id: area.id })
    },
    setSaveDriven(value) { return this.client.action({ action: "settings", patch: { saveDriven: value } }) },
  },
  template: `
    <section class="gx-card gx-offline-roads" aria-label="Offline road maps" :inert="busy" :aria-busy="busy || undefined">
      <div class="gx-offline-roads__head">
        <span class="gx-starport-step__num" aria-hidden="true"><i class="bi bi-cloud-download"></i></span>
        <div>
          <div class="gx-navigation__step-title">
            <h3 class="gx-section__title">Offline Map Management</h3>
            <span class="gx-starport-pill gx-starport-pill--danger">Mapbox only</span>
          </div>
          <p class="gx-note">Saves Mapbox road lines on your comma so the map overlay keeps drawing roads with no cell signal. Google Maps tiles require internet and can't be saved offline, so these road lines always back them up. Downloads happen on Wi-Fi.</p>
        </div>
      </div>
      <p v-if="mode !== 'local'" class="gx-note">Connect to your comma to manage offline road maps.</p>
      <template v-else>
        <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
        <GxNotice tone="warn" v-if="!hasKey">Add your Mapbox key in Setup. Offline road maps download with that key.</GxNotice>
        <GxNotice tone="warn" v-else-if="service.failure === 'key'">Mapbox rejected the saved key for map tiles. Check that it is a public (pk.) key without URL restrictions.</GxNotice>
        <GxNotice tone="warn" v-if="service.failure === 'budget'">This comma's Mapbox road tile allowance is used up, so downloads pause until the 1st.</GxNotice>
        <GxNotice tone="warn" v-if="service.noSpace">The comma's storage is nearly full, so new map areas are paused. Delete an area or free space.</GxNotice>
        <div class="gx-offline-roads__stats">
          <div class="gx-offline-roads__stat"><span>Downloader</span><strong>{{ serviceLabel }}</strong></div>
          <div class="gx-offline-roads__stat"><span>Mapbox this month</span><strong>{{ usage.tiles.toLocaleString() }}</strong><small>of {{ Math.floor(usage.free * 0.99).toLocaleString() }} free tiles used</small></div>
          <div v-if="storage" class="gx-offline-roads__stat gx-offline-roads__stat--wide"><span>Offline overlay road lines</span><strong>{{ formatBytes(storage.used) }}</strong>
            <div class="gx-offline-roads__bar" role="img" :aria-label="formatBytes(storage.used) + ' used for road maps'">
              <i v-for="[label, value, color] in storage.parts" :key="label" :style="{ width: (storage.cap ? Math.max(value ? 1.5 : 0, value / storage.cap * 100) : 0) + '%', background: color }"></i></div>
            <small><span v-for="[label, value, color] in storage.parts" :key="label" class="gx-offline-roads__legend"><b :style="{ background: color }"></b>{{ label }} {{ formatBytes(value) }}</span></small></div>
        </div>
        <GalaxySettingRow :row="switchRow('Save roads as you drive', data?.settings.saveDriven, 'Downloads road lines around and ahead of your car while the map overlay is on and internet is available. Saved lines keep the overlay visible offline; older driven areas are removed when its 600 MB cache fills.')"
          :index="0" :busy="busy" :disabled="!data" :save-value="(index, value) => setSaveDriven(value === 'On')" />
        <div class="gx-offline-roads__picker">
          <BluetoothNotice v-if="bluetooth" feature="Offline area map preview">Saved map settings remain available.</BluetoothNotice>
          <div v-else-if="hasKey" class="gx-offline-roads__map"><canvas ref="canvas" tabindex="0" aria-label="Mapbox map. Tap or press Enter to center a new offline area; drag or use arrow keys to pan."></canvas>
            <span class="gx-offline-roads__provider">Mapbox preview</span>
            <div class="gx-navigation-map__controls"><button class="gx-navigation-map__control" type="button" @click="zoom(1)" aria-label="Zoom in"><i class="bi bi-plus-lg" aria-hidden="true"></i></button>
              <button class="gx-navigation-map__control" type="button" @click="zoom(-1)" aria-label="Zoom out"><i class="bi bi-dash-lg" aria-hidden="true"></i></button></div>
            <span class="gx-offline-roads__hint">{{ point ? 'Adjust the radius, name it, then save.' : 'Tap the map to center a new area.' }}</span></div>
          <div class="gx-offline-roads__form">
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="!data?.position" @click="useLocation"><i class="bi bi-crosshair"></i> Around my car</button>
            <template v-if="point && constants">
              <label for="offline-area-name">Name</label>
              <input id="offline-area-name" class="gx-field" v-model="name" maxlength="80" placeholder="Home, Tahoe trip…">
              <label for="offline-area-radius">Radius · {{ radiusLabel(radiusKm, metric) }}</label>
              <input id="offline-area-radius" class="gx-slider" type="range" :min="range.min" :max="range.max" step="1" v-model.number="radius" @change="focus(point, radiusKm)">
              <p class="gx-note" role="status">≈ {{ estimate.tiles.toLocaleString() }} tiles · {{ formatBytes(estimate.downloadBytes) }} to download · {{ formatBytes(estimate.storedBytes) }} on the comma</p>
              <p v-if="estimate.tiles > freeTilesLeft" class="gx-note">Only {{ freeTilesLeft.toLocaleString() }} tiles are left in this comma's allowance this month. The area resumes after the 1st.</p>
              <div class="gx-settings__controls"><button type="button" class="gx-btn gx-btn--tonal" @click="point = null">Cancel</button>
                <button type="button" class="gx-btn" :disabled="!saveAvailable" @click="save">Save area</button></div>
            </template>
            <p v-if="notice" class="gx-note" role="status">{{ notice }}</p>
          </div>
        </div>
        <ul v-if="data?.areas.length" class="gx-offline-roads__areas" aria-label="Saved offline areas">
          <li v-for="area in data.areas" :key="area.id">
            <div class="gx-offline-roads__area"><strong>{{ area.name }}</strong><small>{{ radiusLabel(area.radiusKm, metric) }} radius · {{ areaLabel(area) }}</small>
              <div v-if="area.progress && area.progress.state !== 'complete'" class="gx-offline-roads__bar"><i :style="{ width: (area.progress.total ? area.progress.done / area.progress.total * 100 : 0) + '%', background: '#9d72ff' }"></i></div></div>
            <div class="gx-navigation__actions">
              <button type="button" class="gx-btn gx-btn--tonal" @click="focus(area, area.radiusKm)">Show</button>
              <button v-if="confirmDelete !== area.id" type="button" class="gx-btn gx-btn--tonal" @click="confirmDelete = area.id">Delete</button>
              <button v-else type="button" class="gx-btn" @click="remove(area)">Delete {{ area.name }}?</button>
            </div>
          </li>
        </ul>
        <p v-else-if="data" class="gx-note">No offline areas yet.{{ data.settings.saveDriven ? ' Road lines are saved while the map overlay is on.' : ' Turn on Save roads as you drive to retain road lines along your drives.' }}</p>
        <p v-if="data?.route" class="gx-note">Active route: {{ data.route.done }} of {{ data.route.total }} corridor tiles checked.</p>
      </template>
    </section>`,
}
