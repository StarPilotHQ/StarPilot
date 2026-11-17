import { MenuTile } from "./menu-tile.js"
import { GxNotice } from "./notice.js"
import { GalaxySelect } from "./galaxy-select.js"
import { BluetoothNotice, bluetoothConnection } from "./bluetooth-notice.js"
import { balancedBlobUrl, whiteBalancePref, WhiteBalanceSwitch, BalancedImage } from "./white-balance.js"

export class CameraSnapshotFeed {
  constructor({ publish, unauthorized, fetcher = (...args) => fetch(...args),
                createURL = (blob) => URL.createObjectURL(blob), revokeURL = (url) => URL.revokeObjectURL(url),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = id => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, createURL, revokeURL, later, cancelTimer })
    this.request = null
    this.url = ""
    this.timer = null
  }
  stop() {
    this.rejectRequest?.(new Error("Snapshot canceled"))
    this.rejectRequest = null
    this.request?.abort()
    this.request = null
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.timer = null
    if (this.url) this.revokeURL(this.url)
    this.url = ""
    this.publish({ image: "", capturing: false, error: "" })
  }
  async capture(camera) {
    this.stop()
    if (bluetoothConnection()) return
    const request = new AbortController()
    this.request = request
    this.publish({ image: "", capturing: true, error: "" })
    let expire
    const deadline = new Promise((_, reject) => { this.rejectRequest = expire = reject })
    this.timer = this.later(() => {
      if (this.request !== request) return
      expire(new Error("Snapshot timed out. Try taking a new snapshot."))
    }, camera === "cabin" ? 26000 : camera === "wide" ? 20500 : 13500)
    try {
      const response = await Promise.race([this.fetcher("./api/cameras/snapshot", { method: "POST", credentials: "same-origin",
        cache: "no-store", signal: request.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ camera }) }), deadline])
      if (this.request !== request || request.signal.aborted) return
      if (response.status === 401) { this.stop(); this.unauthorized(); return }
      if (!response.ok) throw new Error(response.status === 409 ? "Turn the vehicle off before taking a snapshot." :
        "Camera frame unavailable. Try taking a new snapshot.")
      if (response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "image/jpeg") throw new Error("Camera image is unavailable.")
      const blob = await Promise.race([response.blob(), deadline])
      if (this.request !== request || request.signal.aborted) return
      if (!blob.size || blob.size > 1000000) throw new Error("Camera image is unavailable.")
      this.url = this.createURL(blob)
      this.publish({ image: this.url, capturing: false, error: "" })
    } catch (error) {
      if (this.request === request && !request.signal.aborted)
        this.publish({ image: "", capturing: false, error: error.message || "Camera image is unavailable." })
    } finally {
      if (this.request === request) {
        request.abort()
        this.request = null
        this.rejectRequest = null
        this.cancelTimer(this.timer)
        this.timer = null
      }
    }
  }
}

export const CamerasPage = {
  name: "CamerasPage",
  components: { GxNotice, BluetoothNotice, GalaxySelect, MenuTile, WhiteBalanceSwitch, BalancedImage },
  props: { mode: { type: String, required: true }, go: { type: Function, required: true },
    tools: { type: Array, required: true },
    unauthorized: { type: Function, required: true } },
  data: () => ({ bluetooth: bluetoothConnection(), camera: "cabin", image: "", capturing: false, error: "" }),
  created() { this.snapshots = new CameraSnapshotFeed({ publish: (state) => Object.assign(this.$data, state), unauthorized: this.unauthorized }) },
  methods: {
    chooseCamera(event) { this.camera = event.target.value; this.snapshots.stop() },
  },
  mounted() {
    this.visibility = () => { if (document.hidden) this.snapshots.stop() }
    document.addEventListener("visibilitychange", this.visibility)
  },
  beforeUnmount() { document.removeEventListener("visibilitychange", this.visibility); this.snapshots.stop() },
  watch: { mode() { this.snapshots.stop() } },
  template: `
    <div class="gx-view gx-settings" aria-label="Cameras and Monitoring">
      <h2>Cameras &amp; Monitoring</h2>
      <div class="gx-grid">
        <MenuTile v-for="tool in tools" :key="tool.path" :icon="tool.icon" :title="tool.name" :description="tool.description"
          :disabled="tool.availability === 'local-only' && mode !== 'local'" @select="go(tool.path)" />
      </div>
      <section class="gx-card gx-panel gx-stack"><div class="gx-section__header"><i class="bi bi-camera" aria-hidden="true"></i><span class="gx-section__title">Camera Snapshot</span></div>
        <p>Turn off the vehicle, choose a camera, then take a snapshot.</p>
        <BluetoothNotice v-if="bluetooth" feature="Camera snapshots" />
        <template v-else-if="mode === 'local'">
          <label>Camera
            <GalaxySelect class="gx-field gx-field--full" aria-label="Camera" :value="camera" :disabled="capturing" @change="chooseCamera">
              <option value="cabin">Cabin</option><option value="wide">Wide road</option><option value="narrow">Road</option>
            </GalaxySelect></label>
          <button class="gx-btn" type="button" :disabled="capturing" @click="snapshots.capture(camera)">{{ capturing ? 'Capturing…' : 'Take snapshot' }}</button>
          <button v-if="image || capturing" class="gx-btn gx-btn--tonal" type="button" @click="snapshots.stop()">{{ capturing ? 'Cancel' : 'Clear' }}</button>
          <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
          <WhiteBalanceSwitch v-if="camera === 'cabin'" />
          <BalancedImage v-if="image" :src="image" :balance="camera === 'cabin'" :alt="camera + ' camera snapshot'" class="gx-media gx-media--spaced" />
        </template>
        <p v-else>Camera snapshots are available on the connected device.</p>
        <small>Snapshots are not saved.</small></section>
    </div>`,
}

// Shared owner for live editor frames, including bounded capture and image decode.
export class LiveCameraPreview {
  constructor({ publish, unauthorized, redraw, enabled, imageFactory = () => new Image(), later = (fn, ms) => setTimeout(fn, ms), cancelTimer = id => clearTimeout(id) }) {
    Object.assign(this, { publish, redraw, enabled, imageFactory, later, cancelTimer })
    this.image = null
    this.generation = 0
    this.stopped = true
    this.snapshots = new CameraSnapshotFeed({ unauthorized, publish: update => this.receive(update) })
    this.visibility = () => {
      this.snapshots.stop()
      if (!document.hidden && this.warming) this.poll()
    }
  }
  clear() {
    this.generation++
    this.cancelTimer(this.decodeTimer)
    this.snapshots.stop()
    if (this.image) this.image.src = ""
    this.image = null
    this.publish({ imageName: "" })
    this.redraw()
  }
  receive(update) {
    if (update.error) {
      this.lastError = update.error
      return
    }
    if (!update.image) return
    const generation = ++this.generation
    const image = this.imageFactory()
    const failed = () => {
      if (this.stopped || generation !== this.generation) return
      this.cancelTimer(this.decodeTimer)
      this.generation++
      this.lastError = "Camera frame could not be displayed."
      if (!this.image && !this.warming) this.publish({ cameraError: this.lastError })
      this.redraw()
    }
    image.onload = () => {
      if (this.stopped || generation !== this.generation) return
      this.cancelTimer(this.decodeTimer)
      this.image = image
      this.publish({ imageName: "Live cabin camera", cameraError: "" })
      this.redraw()
    }
    image.onerror = failed
    this.cancelTimer(this.decodeTimer)
    this.decodeTimer = this.later(failed, 4000)
    if (whiteBalancePref.on) balancedBlobUrl(update.image).then(url => { image.src = url }, () => { image.src = update.image })
    else image.src = update.image
  }
  start() {
    if (bluetoothConnection()) return
    this.stopped = false
    document.addEventListener("visibilitychange", this.visibility)
    this.refresh()
  }
  refresh() {
    this.warming = true
    this.lastError = ""
    this.publish({ cameraWarming: true, cameraError: "" })
    this.poll()
  }
  async poll() {
    if (this.stopped || !this.warming) return
    const generation = this.pollGeneration = (this.pollGeneration || 0) + 1
    if (this.enabled() && !document.hidden) await this.snapshots.capture("cabin")
    if (!this.stopped && generation === this.pollGeneration) {
      this.warming = false
      this.publish({ cameraWarming: false, cameraError: this.image ? "" : this.lastError || "No cabin frame was available. Try taking a new snapshot." })
    }
  }
  stop() {
    this.stopped = true
    this.warming = false
    document.removeEventListener("visibilitychange", this.visibility)
    this.clear()
  }
}
