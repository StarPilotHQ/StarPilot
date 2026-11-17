import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { BluetoothNotice, bluetoothConnection } from "./bluetooth-notice.js"
import { SnapshotFeed } from "./snapshot-feed.js"
import { RecordingActions } from "./recording-actions.js"
import { GalaxySelect } from "./galaxy-select.js"
import { RouteWorkspace } from "./route-workspace.js"
import { ReplayExportClient } from "./replay-export.js"
import { replayableSegment } from "./replay.js"
import { markRaw, shallowReactive } from "../vendor/vue/vue.esm-browser.js"
import { validLocalHistory, hasVideo, routeMatchesSearch, routeDate, formatDuration } from "./recording-data.js"
export * from "./recording-data.js"

export class LocalHistoryFeed extends SnapshotFeed {
  static endpoint = "./api/recordings/local"
  static valid = validLocalHistory
  static subject = "local recordings"
  static unavailable = "Local recordings are unavailable."
  static invalid = "Local recording inventory is unavailable."
  start() { if (bluetoothConnection()) return this.stop(); return super.start() }
}

export const LocalRecordingsPage = {
  name: "LocalRecordingsPage",
  components: { GxState, GxNotice, BluetoothNotice, RecordingActions, GalaxySelect, RouteWorkspace },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    routePath: { type: String, default: "/recordings" }, go: { type: Function, default: () => {} } },
  data: () => ({ bluetooth: bluetoothConnection(), sortOrder: "newest", query: "", preservedOnly: false, status: "idle", data: null, error: "", entryMode: "footage", exports: null }),
  computed: {
    selectedRouteId() { const id = this.routePath.startsWith("/recordings/") ? this.routePath.slice(12) : null; return id },
    selectedSummary() { return this.data?.routes.find(route => route.routeId === this.selectedRouteId) || null },
    stats() { const routes = this.data?.routes || []; return { count: routes.length, minutes: routes.reduce((sum, route) => sum + route.segmentCount, 0), preserved: routes.filter(route => route.preserved).length } },
    routeCards() {
      const ordered = (this.data?.routes || []).slice().sort((a, b) => {
        if (["longest", "shortest"].includes(this.sortOrder)) return (b.segmentCount - a.segmentCount) * (this.sortOrder === "shortest" ? -1 : 1)
        return ((b.startTime || b.fileTime || 0) - (a.startTime || a.fileTime || 0)) * (this.sortOrder === "oldest" ? -1 : 1)
      })
      return ordered.filter(route => (!this.preservedOnly || route.preserved) &&
        routeMatchesSearch(route, this.query)).map((route) => ({ ...route, date: routeDate(route) }))
    },
  },
  created() {
    this.feed = markRaw(new LocalHistoryFeed({ publish: update => Object.assign(this.$data, update), unauthorized: this.unauthorized }))
    this.exports = shallowReactive(new ReplayExportClient({ unauthorized: this.unauthorized }))
  },
  mounted() {
    this.visibility = () => {
      if (document.hidden) { this.feed.stop(); this.exports.close() }
      else if (this.mode === "local") { this.feed.start(); this.openExports() }
    }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local" && !document.hidden) { this.feed.start(); this.openExports() }
  },
  beforeUnmount() { document.removeEventListener("visibilitychange", this.visibility); this.feed.stop(); this.exports.close() },
  watch: {
    selectedRouteId(id) {
      if (!id) this.$nextTick(() => {
        window.scrollTo(0, this.listScroll || 0)
        this.$refs.routeList?.querySelector(`[data-route-id="${this.lastRouteId}"]`)?.focus({ preventScroll: true })
      })
    },
    mode(value) { if (value !== "local") { this.feed.stop(); this.exports.close() } },
  },
  methods: {
    openExports() { if (bluetoothConnection()) this.exports.close(); else this.exports.open() },
    hasVideo, replayableSegment, formatDuration,
    openRoute(route, view = "footage") {
      this.entryMode = view
      this.lastRouteId = route.routeId
      this.listScroll = window.scrollY
      this.go(`/recordings/${route.routeId}`)
    },
    refresh() { return this.feed.load() },
  },
  template: `
    <div class="gx-view gx-recordings">
      <BluetoothNotice v-if="bluetooth" feature="Local recordings and replay" />
      <RouteWorkspace v-else-if="selectedRouteId && mode === 'local'" :key="selectedRouteId" :route-id="selectedRouteId" :summary="selectedSummary" :initial-mode="entryMode" :unauthorized="unauthorized" :exports="exports" @back="go('/recordings')" @changed="refresh" />
      <div v-else ref="routeList" class="gx-recordings__browse">
      <header class="gx-recordings__heading"><h2>Routes</h2><span v-if="mode === 'local' && data" class="gx-note">{{ stats.count }} drives · ~{{ formatDuration(stats.minutes) }}</span></header>
      <GxState v-if="mode !== 'local'">Local recordings are unavailable in the offline preview.</GxState>
      <template v-else>
        <GxNotice v-if="exports?.job?.owned" tone="info">
          <span>{{ exports.running ? 'Exporting replay' : exports.job.state === 'ready' ? 'Replay export ready' : 'Replay export ' + exports.job.state }} · {{ exports.job.segment == null ? 'Saved route' : 'Segment ' + exports.job.segment }}<template v-if="exports.running"> · {{ Math.round(exports.job.progress * 100) }}%</template></span>
          <template #actions><a v-if="exports.job.state === 'ready'" class="gx-btn gx-btn--tonal" :href="exports.downloadUrl" :download="exports.job.filename">Download replay {{ exports.job.segment == null ? 'route' : 'segment' }} (.mp4)</a>
          <button v-if="exports.running" class="gx-btn gx-btn--tonal" :disabled="exports.busy" @click="exports.cancel()">Cancel export</button>
          <button class="gx-btn gx-btn--tonal" @click="openRoute({routeId: exports.job.routeId}, 'replay')">Open route</button>
          </template>
        </GxNotice>
        <section class="gx-card gx-recordings__library">
          <div class="gx-recordings__filters">
            <input class="gx-field" type="search" aria-label="Search routes" placeholder="Search drives, dates or IDs…" v-model="query" />
            <GalaxySelect class="gx-field" aria-label="Sort routes" :value="sortOrder" @change="sortOrder=$event.target.value">
              <option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="longest">Longest duration</option><option value="shortest">Shortest duration</option>
            </GalaxySelect>
            <div class="gx-recordings__tabs gx-tabs" aria-label="Filter routes"><button class="gx-btn gx-btn--tonal" :aria-pressed="!preservedOnly" @click="preservedOnly=false">All</button><button class="gx-btn gx-btn--tonal" :aria-pressed="preservedOnly" @click="preservedOnly=true">Preserved · {{ stats.preserved }}</button></div>
          </div>
        </section>
        <GxState v-if="status === 'loading'" loading>Finding local drives…</GxState><GxNotice tone="danger" v-if="status === 'unavailable'">{{ error }}</GxNotice>
        <template v-if="status === 'ready' && data">
          <GxNotice v-if="data.scanIncomplete" tone="warn">This scan was incomplete. More local segments may exist.</GxNotice>
          <GxNotice v-if="data.legacyRecordingsPresent === true" tone="info">Older StarPilot recordings also use device storage. Eligible recordings from the previous installation are included in automatic cleanup when space is low.</GxNotice>
          <section class="gx-card gx-recordings__list">
            <p v-if="!routeCards.length" class="gx-recordings__empty">{{ data.routes.length ? 'No drives match your filters.' : 'No saved drives found on this device.' }}</p>
            <article v-for="route in routeCards" :key="route.routeId" class="gx-recordings__row" :class="{'gx-recordings__row--preserved': route.preserved}">
              <button class="gx-recordings__drive" :data-route-id="route.routeId" :aria-label="'Open route ' + (route.displayName || route.date.label)" @click="openRoute(route)">
                <span class="gx-recordings__name"><span class="gx-row__label"><span translate="no">{{ route.displayName !== route.routeId ? route.displayName : route.date.label }}</span></span><span v-if="route.preserved" class="gx-chip">Preserved</span></span>
                <span class="gx-row__desc"><template v-if="route.displayName !== route.routeId">{{ route.date.label }} · </template>~{{ formatDuration(route.segmentCount) }}</span>
              </button>
              <span class="gx-note">{{ route.segmentCount }} {{ route.segmentCount === 1 ? "segment" : "segments" }}</span>
              <div class="gx-route-workspace__shortcuts"><button class="gx-btn gx-btn--tonal" :disabled="!route.segments.some(segment => hasVideo(segment.files))" @click="openRoute(route, 'footage')"><i class="bi bi-camera-video" aria-hidden="true"></i>Footage</button>
              <button class="gx-btn gx-btn--tonal" :disabled="!route.segments.some(replayableSegment)" @click="openRoute(route, 'replay')"><i class="bi bi-play-circle" aria-hidden="true"></i>Replay with UI</button></div>
              <RecordingActions :recording="route" compact :unauthorized="unauthorized" @changed="refresh" />
            </article>
          </section>
          <details v-if="data.routes.length" class="gx-card gx-recordings__delete"><summary>Manage local storage</summary><RecordingActions :recording="{displayName: 'local driving recordings'}" all :unauthorized="unauthorized" @changed="refresh" /></details>
          <p class="gx-note">Duration is approximate from saved one-minute segments. Older footage may have been removed to free space.</p>
        </template>
      </template>
      </div>
    </div>`,
}
