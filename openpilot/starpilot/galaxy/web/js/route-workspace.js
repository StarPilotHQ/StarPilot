import { DrivingHelp } from "./driving-help.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { GxIconButton } from "./icon-button.js"
import { GxDialog } from "./dialog.js"
import { GalaxySelect } from "./galaxy-select.js"
import { RecordingActions } from "./recording-actions.js"
import { CameraViewer } from "./camera-viewer.js"
import { ReplayViewer, replayableSegment } from "./replay.js"
import { requestJson } from "./startup.js"
import { LOCAL, VIDEOS, hasVideo, availableFiles, validRouteDetail, routeDate, cameraLabel, recordingUrl, driveVideoUrl,
  logUrl, logArchiveUrl, connectRouteUrl, formatBytes, detailMetric, segmentSummaryUrl, validSegmentSummary } from "./recording-data.js"

export const RouteWorkspace = {
  components: { GxState, GxNotice, GxIconButton, GxDialog, GalaxySelect, RecordingActions, CameraViewer, ReplayViewer, DrivingHelp },
  props: { routeId: { type: String, required: true }, summary: { type: Object, default: null },
    initialMode: { type: String, default: "footage" }, exports: { type: Object, required: true }, unauthorized: { type: Function, required: true } },
  emits: ["back", "changed"],
  data() { return { route: null, status: "loading", error: "", viewMode: this.initialMode, segmentNumber: null, camera: "fcamera",
    replayMounted: false, replayBusy: false, changingMode: false, infoMode: "files", generation: 0,
    details: null, detailsName: null, detailsStatus: "idle", detailsError: "", detailsGeneration: 0,
    deleting: null, deleteSelection: null, deleteError: "" } },
  computed: {
    segment() { return this.route?.segments.find(segment => segment.number === this.segmentNumber) || this.route?.segments[0] || null },
    segmentIndex() { return this.route?.segments.indexOf(this.segment) ?? -1 },
    cameras() { return VIDEOS.filter(camera => this.route?.segments.some(segment => segment.files[camera])) },
    date() { return routeDate({ ...this.summary, ...this.route, startTime: this.route?.startTime || this.summary?.startTime }) },
    title() { return this.route?.displayName && this.route.displayName !== this.routeId ? this.route.displayName : this.date.label },
    replayable() { return !!this.segment && replayableSegment(this.segment) },
    job() { return this.exports.job?.routeId === this.routeId && this.exports.job.owned ? this.exports.job : null },
    connect() { return connectRouteUrl(this.route?.connectUrl ? this.route : this.summary) },
  },
  mounted() {
    this.visibility = () => { if (document.hidden) { this.closeDetails(); this.infoMode = "files"; this.replayMounted = false; this.replayBusy = false } }
    document.addEventListener("visibilitychange", this.visibility)
    this.load()
  },
  beforeUnmount() { this.generation++; this.inventoryRequest?.abort(); this.deleteRequest?.abort(); this.closeDetails(); document.removeEventListener("visibilitychange", this.visibility) },
  methods: {
    cameraLabel, recordingUrl, driveVideoUrl, availableFiles, logUrl, logArchiveUrl, formatBytes, detailMetric, hasVideo,
    async load() {
      this.inventoryRequest?.abort()
      const generation = ++this.generation, controller = this.inventoryRequest = new AbortController()
      const initial = this.route === null
      if (initial) this.status = "loading"
      this.error = ""
      try {
        if (!LOCAL.test(this.routeId)) throw new Error("This route address is invalid. Return to Routes and select a drive.")
        const result = await requestJson(`./api/recordings/detail?routeId=${encodeURIComponent(this.routeId)}`, { request: { signal: controller.signal } })
        if (controller.signal.aborted || generation !== this.generation) return
        if (!validRouteDetail(result, this.routeId)) throw new Error("This route inventory could not be read. Refresh to try again.")
        this.route = { ...this.summary, ...result.route, startTime: this.summary?.startTime || result.route.startTime }
        if (!this.route.segments.some(segment => segment.number === this.segmentNumber)) {
          const first = this.viewMode === "replay" ? this.route.segments.find(replayableSegment) : this.route.segments[0]
          this.segmentNumber = (first || this.route.segments[0])?.number ?? null
        }
        if (!this.cameras.includes(this.camera)) this.camera = this.cameras[0] || "fcamera"
        this.status = "ready"
        if (initial) this.replayMounted = this.viewMode === "replay" && this.replayable && !this.exports.running && !document.hidden
      } catch (error) {
        if (controller.signal.aborted || generation !== this.generation) return
        if (error.status === 401 || ["setup_required", "access_unavailable"].includes(error.code)) this.unauthorized()
        else { this.status = "unavailable"; this.error = error.message || "This route is unavailable. Return to Routes and refresh." }
      }
    },
    async stopReplay() { if (this.$refs.replay) await this.$refs.replay.stopAndWait(); this.replayMounted = false; this.replayBusy = false },
    async changeMode(mode) {
      if (this.changingMode || mode === this.viewMode) return
      this.changingMode = true; this.error = ""
      try {
        await this.stopReplay()
        this.viewMode = mode
        this.replayMounted = mode === "replay" && this.replayable && !this.exports.running
      } catch (error) { this.error = error.message }
      finally { this.changingMode = false }
    },
    async chooseSegment(number) {
      if (this.changingMode || !this.route.segments.some(segment => segment.number === number)) return
      this.changingMode = true; this.error = ""
      try {
        const next = this.route.segments.find(segment => segment.number === number)
        if (this.viewMode === "replay" && !replayableSegment(next)) await this.stopReplay()
        this.segmentNumber = number
        this.closeDetails()
        if (this.infoMode === "details") this.openDetails(next)
      } catch (error) { this.error = error.message }
      finally { this.changingMode = false }
    },
    nextFootage() {
      const next = this.route.segments.slice(this.segmentIndex + 1).find(segment => segment.files[this.camera])
      if (next) this.chooseSegment(next.number)
    },
    async startExport(segment = null) {
      await this.exports.start(this.routeId, segment, () => this.stopReplay())
    },
    async managed(action) {
      this.$emit("changed")
      if (action === "delete") this.$emit("back")
      else await this.load()
    },
    confirmDeleteVideos(segment) {
      if (this.status !== "ready" || this.deleting !== null || !hasVideo(segment?.files)) return
      this.deleteSelection = segment
      this.deleteError = ""
    },
    async deleteVideos() {
      const segment = this.deleteSelection
      if (!segment || this.status !== "ready" || this.deleting !== null) return
      this.deleting = segment.segmentName
      this.deleteError = ""
      const controller = this.deleteRequest = new AbortController()
      try {
        await this.stopReplay()
        this.$refs.footage?.pause()
        if (controller.signal.aborted) return
        await requestJson("./api/recordings/delete-videos", { timeout: 10000,
          request: { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ segmentName: segment.segmentName }), signal: controller.signal } })
      } catch (error) {
        if (controller.signal.aborted) return
        if (error.status === 401) { this.unauthorized(); return }
        if (error.status !== 404) {
          this.deleteError = error.name === "TimeoutError" ? "Deleting videos timed out. Refresh to check what remains." : error.message
          return
        }
      } finally {
        this.deleteRequest = null
        this.deleting = null
      }
      this.deleteSelection = null
      this.$emit("changed")
      await this.load()
    },
    closeDetails() {
      this.detailsRequest?.abort()
      if (this.detailsTimer !== undefined) clearTimeout(this.detailsTimer)
      this.detailsRequest = null
      this.detailsTimer = undefined
      this.detailsGeneration++
      this.details = null
      this.detailsName = null
      this.detailsStatus = "idle"
      this.detailsError = ""
    },
    async openDetails(segment) {
      if (this.status !== "ready" || !segment?.files?.rlog || document.hidden) return
      const url = segmentSummaryUrl(segment.segmentName)
      if (!url) return
      this.closeDetails()
      const generation = this.detailsGeneration
      const controller = new AbortController()
      this.detailsRequest = controller
      this.detailsName = segment.segmentName
      this.detailsStatus = "loading"
      this.detailsTimer = setTimeout(() => {
        if (generation !== this.detailsGeneration || this.detailsRequest !== controller) return
        controller.abort()
        this.detailsRequest = null
        this.detailsGeneration++
        this.detailsTimer = undefined
        this.detailsStatus = "unavailable"
        this.detailsError = "Reading this segment timed out. Select Details to try again."
      }, 10000)
      try {
        const response = await fetch(url, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
        if (generation !== this.detailsGeneration || controller.signal.aborted) return
        if (response.status === 401) { this.closeDetails(); this.unauthorized(); return }
        if (response.status === 503) {
          const body = await response.json().catch(() => null)
          if (generation !== this.detailsGeneration || controller.signal.aborted) return
          if (["setup_required", "access_unavailable"].includes(body?.code)) { this.closeDetails(); this.unauthorized(); return }
        }
        if (!response.ok) throw new Error("Recorded segment details are unavailable.")
        const result = await response.json()
        if (generation !== this.detailsGeneration || controller.signal.aborted) return
        if (!validSegmentSummary(result, segment.segmentName)) throw new Error("Recorded segment details are unavailable.")
        this.details = result
        this.detailsStatus = "ready"
      } catch (error) {
        if (generation === this.detailsGeneration) {
          this.detailsStatus = "unavailable"
          this.detailsError = error instanceof Error ? error.message : "Recorded segment details are unavailable."
        }
      } finally {
        if (generation === this.detailsGeneration) {
          if (this.detailsTimer !== undefined) clearTimeout(this.detailsTimer)
          this.detailsTimer = undefined
          this.detailsRequest = null
        }
      }
    },
  },
  template: `<div class="gx-route-workspace">
    <header class="gx-route-workspace__header">
      <GxIconButton class="gx-route-workspace__back" label="Back to Routes" icon="bi-arrow-left" @click="$emit('back')" />
      <div class="gx-route-workspace__identity"><h2>{{ title }}</h2><p class="gx-note"><template v-if="title !== date.label">{{ date.label }} · </template><template v-if="date.source">{{ date.source }} · </template><template v-if="route">{{ route.segments.length }} saved segments</template></p></div>
      <RecordingActions v-if="route" :recording="route" collapsed :unauthorized="unauthorized" @changed="managed" />
    </header>
    <GxState v-if="status === 'loading'" loading>Reading saved route…</GxState>
    <GxNotice v-if="error" tone="danger">{{ error }}<template #actions v-if="status === 'unavailable'"><button class="gx-btn gx-btn--tonal" @click="load">Retry route</button></template></GxNotice>
    <template v-if="status === 'ready' && route">
      <details class="gx-card gx-panel">
        <summary>Get help with this drive</summary>
        <DrivingHelp :route-id="routeId" :segment="segment?.number ?? null" :connect="connect || ''" />
      </details>
      <GxNotice v-if="!route.inventoryComplete" tone="warn">{{ route.reason || 'Some route files could not be inspected.' }} Route downloads require a complete inventory.</GxNotice>
      <div class="gx-route-workspace__body">
      <section class="gx-card gx-route-workspace__viewer" aria-label="Route viewer">
        <div class="gx-route-workspace__toolbar">
          <div class="gx-tabs gx-route-workspace__modes" aria-label="Viewing mode">
            <button class="gx-btn gx-btn--tonal" :aria-pressed="viewMode === 'footage'" :disabled="changingMode || exports.busy" @click="changeMode('footage')"><i class="bi bi-camera-video" aria-hidden="true"></i>Footage</button>
            <button class="gx-btn gx-btn--tonal" :aria-pressed="viewMode === 'replay'" :disabled="changingMode || exports.busy" @click="changeMode('replay')"><i class="bi bi-play-circle" aria-hidden="true"></i>Replay with UI</button>
          </div>
          <GalaxySelect v-if="viewMode === 'footage' && cameras.length" class="gx-field gx-route-workspace__camera-select" aria-label="Camera source" :value="camera" @change="camera=$event.target.value"><option v-for="source in cameras" :key="source" :value="source">{{ cameraLabel(source) }}</option></GalaxySelect>
          <div v-if="segment" class="gx-route-workspace__segment"><GxIconButton label="Previous segment" icon="bi-skip-backward-fill" :disabled="segmentIndex <= 0 || replayBusy || changingMode || exports.busy" @click="chooseSegment(route.segments[segmentIndex - 1].number)" />
            <GalaxySelect class="gx-field" aria-label="Route segment" :value="String(segment.number)" :disabled="replayBusy || changingMode || exports.busy" @change="chooseSegment(Number($event.target.value))"><option v-for="(item, index) in route.segments" :key="item.number" :value="String(item.number)" :data-collapsed-label="'Segment ' + item.number">Segment {{ item.number }} · {{ index + 1 }} of {{ route.segments.length }}</option></GalaxySelect>
            <GxIconButton label="Next segment" icon="bi-skip-forward-fill" :disabled="segmentIndex >= route.segments.length - 1 || replayBusy || changingMode || exports.busy" @click="chooseSegment(route.segments[segmentIndex + 1].number)" /></div>
        </div>
        <template v-if="segment">
          <GxState v-if="viewMode === 'footage' && !cameras.length" class="gx-route-workspace__empty">This route contains logs only. Download or inspect them below.</GxState>
          <CameraViewer v-else-if="viewMode === 'footage'" ref="footage" :segment="segment" :camera="camera" :unauthorized="unauthorized" @ended="nextFootage" />
          <ReplayViewer v-else-if="replayMounted && !exports.running" ref="replay" :client-id="exports.clientId" :route-id="routeId" :start-segment="segment.number" :unauthorized="unauthorized" @segment="chooseSegment" @busy="replayBusy=$event" />
          <GxState v-else class="gx-route-workspace__empty">{{ exports.running ? 'Replay is rendering an export. You can watch camera footage while it finishes.' : !replayable ? 'This segment needs saved driving logs and road video for replay. Choose another segment.' : 'Replay is stopped.' }}<button v-if="!exports.running && replayable" class="gx-btn gx-btn--tonal" @click="replayMounted=true">Play replay</button></GxState>
          <div v-if="viewMode === 'replay' || cameras.length" class="gx-route-workspace__downloads">
            <div class="gx-route-workspace__download-row">
              <div class="gx-route-workspace__download-context"><strong>{{ viewMode === 'footage' ? 'Download footage' : 'Export replay' }}</strong><span class="gx-note">{{ viewMode === 'footage' ? cameraLabel(camera) + ' camera · MP4' : 'Camera + driving UI · MP4' }}</span></div>
              <div v-if="viewMode === 'footage'" class="gx-actions gx-route-workspace__download-actions">
                <a v-if="segment.files[camera]" class="gx-btn gx-btn--tonal" :aria-label="'Download segment ' + segment.number + ' footage'" :href="recordingUrl(segment.segmentName, camera)" :download="routeId + '-' + camera + '-' + segment.number + '.mp4'"><i class="bi bi-download" aria-hidden="true"></i>Segment MP4</a>
                <a v-if="route.footageDownloads?.[camera]" class="gx-btn gx-btn--tonal" aria-label="Download saved route footage" :href="driveVideoUrl(routeId, camera)" :download="routeId + '-' + camera + '.mp4'"><i class="bi bi-download" aria-hidden="true"></i>Route MP4</a>
              </div>
              <div v-else-if="!exports.running" class="gx-actions gx-route-workspace__download-actions">
                <button class="gx-btn gx-btn--tonal" :aria-label="'Export replay segment ' + segment.number" :disabled="!replayable || !route.inventoryComplete || exports.busy || changingMode" @click="startExport(segment.number)">Export segment</button>
                <button class="gx-btn gx-btn--tonal" aria-label="Export replay route" :disabled="!route.playable || !route.inventoryComplete || exports.busy || changingMode" @click="startExport()">Export route</button>
              </div>
            </div>
            <template v-if="viewMode === 'replay'">
              <div v-if="job" class="gx-route-workspace__job" role="status">
                <template v-if="exports.running"><label for="replay-export-progress">{{ job.state === 'finalizing' ? 'Preparing MP4' : 'Rendering ' + (job.segment == null ? 'route' : 'segment ' + job.segment) }} · {{ Math.round(job.progress * 100) }}%</label><progress id="replay-export-progress" :value="job.progress" max="1"></progress><button class="gx-btn gx-btn--tonal" :disabled="exports.busy" @click="exports.cancel()">Cancel export</button></template>
                <template v-else-if="job.state === 'ready'"><a class="gx-btn" :href="exports.downloadUrl" :download="job.filename"><i class="bi bi-download" aria-hidden="true"></i>Download replay {{ job.segment == null ? 'route' : 'segment ' + job.segment }} (.mp4)</a><span class="gx-note">{{ formatBytes(job.bytes) }} · Available for 30 minutes, or until the next export.</span></template>
                <span v-else>Replay export {{ job.state }}<template v-if="job.error">: {{ job.error.replaceAll('_', ' ') }}</template>. {{ job.state === 'failed' ? 'Try exporting an individual segment.' : '' }}</span>
              </div>
              <GxNotice v-if="exports.error" tone="danger">{{ exports.error }}</GxNotice><p v-if="exports.running && !job" class="gx-note">Another route is being exported. Open it from Routes to check progress.</p>
            </template>
            <details class="gx-route-workspace__help"><summary>{{ viewMode === 'footage' ? 'About footage downloads' : 'Export details · offroad only' }}</summary>
              <p v-if="viewMode === 'footage'" class="gx-note">Original footage, without the driving interface. Route video joins the saved segments for this camera.<template v-if="cameras.includes(camera) && !route.footageDownloads?.[camera]"> Combined downloads are limited to 512 MB; download individual segments for larger routes.</template></p>
              <p v-else class="gx-note">Camera + driving interface in one MP4. Uses the saved replayable coverage; missing ranges are skipped. Export runs at normal speed and continues when you leave this view. Preview stops while rendering.</p>
            </details>
          </div>
        </template>
      </section>
      <section v-if="segment" class="gx-card gx-route-workspace__files" aria-label="Route files and details">
        <header class="gx-route-workspace__file-head"><div class="gx-tabs" aria-label="Segment information"><button class="gx-btn gx-btn--tonal" :aria-pressed="infoMode === 'files'" @click="infoMode='files'; closeDetails()">Logs &amp; files</button><button class="gx-btn gx-btn--tonal" :aria-pressed="infoMode === 'details'" @click="infoMode='details'; openDetails(segment)">Details</button></div></header>
        <template v-if="infoMode === 'files'">
          <div class="gx-route-workspace__file-group"><h3>Segment {{ segment.number }}</h3><div class="gx-actions"><a v-for="filename in segment.logFiles || []" :key="filename" class="gx-btn gx-btn--tonal" :href="logUrl(segment.segmentName, filename)" :download="segment.segmentName + '-' + filename"><i class="bi bi-download" aria-hidden="true"></i>{{ filename }}<span v-if="segment.logBytes?.[filename]"> · {{ formatBytes(segment.logBytes[filename]) }}</span></a><span v-if="!segment.logFiles?.length" class="gx-note">No logs saved for this segment.</span></div>
            <p class="gx-note">{{ availableFiles(segment.files).join(' · ') }}</p>
            <details v-if="hasVideo(segment.files)" class="gx-route-workspace__help"><summary>Segment actions</summary><button class="gx-btn gx-btn--tonal gx-recordings__danger" :disabled="deleting !== null || exports.running" @click="confirmDeleteVideos(segment)">Delete segment videos</button></details>
          </div>
          <div class="gx-route-workspace__file-group gx-route-workspace__route-files"><h3>Saved route</h3><div class="gx-actions"><a v-if="route.logArchiveAvailable" class="gx-btn gx-btn--tonal" :href="logArchiveUrl(routeId)" :download="routeId + '-logs.tar'">Download all logs (.tar)</a><a v-if="connect" class="gx-btn gx-btn--tonal" :href="connect" target="_blank" rel="noopener noreferrer">Open in connect</a></div><p v-if="!route.logArchiveAvailable && route.segments.some(item => item.logFiles?.length)" class="gx-note">A combined log archive is unavailable. Download individual log files above.</p></div>
        </template>
        <template v-else><dl class="gx-route-workspace__metadata"><dt>Route ID</dt><dd>{{ routeId }}</dd></dl><h3>Segment {{ segment.number }} details</h3><p class="gx-note">For this saved segment only. Distance is estimated from recorded speed.</p><GxState v-if="!segment.files.rlog">Details need a saved full log (rlog).</GxState><GxState v-else-if="detailsStatus === 'loading'" loading>Reading closed full log…</GxState><GxNotice v-if="detailsStatus === 'unavailable'" tone="danger">{{ detailsError }}</GxNotice>
          <div v-if="detailsStatus === 'ready' && details" class="gx-recordings__metrics"><div><strong>{{ detailMetric(details.observedCarSpanSeconds) }}</strong><span>Recorded time span</span></div><div><strong>{{ detailMetric(details.estimatedDistanceMeters, 'm') }}</strong><span>Estimated distance</span></div><div><strong>{{ detailMetric(details.observedLatActiveSeconds) }}</strong><span>Steering active</span></div><div><strong>{{ detailMetric(details.observedLongActiveSeconds) }}</strong><span>Longitudinal active</span></div></div><p v-if="details && !details.sampleCoverageComplete" class="gx-note">Samples have gaps or a missing source; unavailable values were not inferred.</p>
        </template>
      </section>
      </div>
    </template>
    <GxDialog v-if="deleteSelection" :inert="deleting !== null" labelledby="gx-delete-videos-title" describedby="gx-delete-videos-body" alert @close="deleting === null && (deleteSelection=null)"><h3 id="gx-delete-videos-title">Delete segment videos?</h3><p id="gx-delete-videos-body">Delete the videos for segment {{ deleteSelection.number }}? Its logs stay on the device. This cannot be undone.</p><GxNotice v-if="deleteError" tone="danger">{{ deleteError }}</GxNotice><div class="gx-actions"><button class="gx-btn gx-btn--tonal" @click="deleteSelection=null">Cancel</button><button class="gx-btn gx-btn--danger" @click="deleteVideos">{{ deleting ? 'Deleting…' : 'Delete videos' }}</button></div></GxDialog>
  </div>`,
}
