import { LogHelp } from "./log-help.js"
import { WikiHelp } from "./wiki-help.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { TroubleshootPage, TmuxPage } from "./diagnostic-tools.js"
import { navigate } from "./router.js"
import { SystemMonitor } from "./system-monitor.js"
import { CrashReportsFeed } from "./crash-reports.js"
import { storedSize } from "./record-deletion.js"
import { MenuTile } from "./menu-tile.js"

export const Logs = {
  name: "Logs",
  components: { LogHelp, WikiHelp, GxState, GxNotice, SystemMonitor, TroubleshootPage, TmuxPage, MenuTile },
  props: {
    path: { type: String, required: true }, mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    tools: { type: Array, required: true }, localRoutes: Boolean
  },
  data: () => ({
    crashes: { reports: [], scanIncomplete: false, listLimited: false, status: "idle", error: "", selected: null, preview: null, previewStatus: "idle", deleting: false, storageBytes: 0, reportCount: 0 }, search: "",
  }),
  computed: {
    helpRoute() {
      return this.path === "/logs/help" || this.path.startsWith("/logs/help/") || this.path === "/logs/starport"
    },
    helpTopic() {
      if (this.path === "/logs/help/connection") return "connection"
      if (this.path === "/logs/help/controller") return "controller"
      return "starport"
    },
    visibleReports() { return this.crashes.reports.filter((item) => item.name.toLowerCase().includes(this.search.toLowerCase())) },
  },
  created() {
    this.crashFeed = new CrashReportsFeed({ publish: (update) => Object.assign(this.crashes, update), unauthorized: this.unauthorized })
  },
  mounted() {
    if (this.path === "/logs/crashes" && this.mode === "local") this.crashFeed.start()
  },
  beforeUnmount() { this.crashFeed.stop() },
  watch: {
    path(next, previous) {
      if (next === "/logs/crashes" && this.mode === "local") this.crashFeed.start()
      else if (previous === "/logs/crashes") { this.crashFeed.stop(); this.search = "" }
    },
  },
  methods: {
    storedSize,
    go: navigate,
    reportDate(seconds) { return new Date(seconds * 1000).toLocaleString() },
    reportSize(bytes) { return `${(bytes / 1024).toFixed(1)} KiB` },
    async copyPreview() {
      if (!this.crashes.preview) return
      try { await navigator.clipboard.writeText(this.crashes.preview.text) }
      catch { this.crashes.error = "Copy is unavailable in this browser." }
    },
  },
  template: `
    <div class="gx-view">
      <header v-if="path === '/logs'" class="gx-settings__header gx-page-header"><div><h2>Logs & Diagnostics</h2></div><WikiHelp /></header>
      <p v-if="path === '/logs'" class="gx-note">Logs, diagnostics, drive state, and help.</p>
      <div v-if="path === '/logs'" class="gx-grid">
        <MenuTile v-for="tool in tools" :key="tool.path" :icon="tool.icon" :title="tool.name"
          :description="mode === 'sample' && tool.sampleDescription || tool.description"
          :availability="mode !== 'local' && tool.availability === 'local-only' ? 'Unavailable in preview' : ''"
          :disabled="mode !== 'local' && tool.availability === 'local-only'" @select="go(tool.path)" />
      </div>
      <LogHelp v-else-if="helpRoute" :mode="mode" :unauthorized="unauthorized" :topic="helpTopic" />
      <TroubleshootPage v-else-if="path === '/logs/troubleshoot'" :mode="mode" :unauthorized="unauthorized" @navigate="go" />
      <TmuxPage v-else-if="path === '/logs/tmux'" :mode="mode" :unauthorized="unauthorized" @navigate="go" />
      <template v-else-if="path === '/logs/monitor'"><SystemMonitor :mode="mode" :unauthorized="unauthorized" /></template>
      <template v-else-if="path === '/logs/crashes' && mode === 'local'">

        <header class="gx-settings__header gx-page-header"><div><h2>Crash Reports</h2></div><WikiHelp /></header>
        <p class="gx-note">Local reports saved when a process stops unexpectedly.</p>
        <GxNotice tone="danger" v-if="crashes.error">{{ crashes.error }}</GxNotice>
        <div class="gx-crash-controls"><button type="button" class="gx-btn" :disabled="crashes.deleting" @click="crashFeed.remove(null)">Delete All</button><span>{{ crashes.scanIncomplete ? 'At least ' : '' }}{{ crashes.reportCount }} reports · {{ crashes.storageIncomplete ? 'At least ' : '' }}{{ storedSize(crashes.storageBytes) }} stored</span><input class="gx-field" type="search" v-model="search" placeholder="Search report names" aria-label="Search crash reports"></div>
        <p v-if="crashes.scanIncomplete" class="gx-note">Directory scan is incomplete; newer reports may be omitted.</p>
        <p v-else-if="crashes.listLimited" class="gx-note">Showing the 200 newest reports; older reports are omitted.</p>
        <GxState v-if="crashes.status === 'loading'" loading>Loading crash reports…</GxState>
        <GxNotice tone="danger" v-else-if="crashes.status === 'unavailable' && !crashes.error">Crash reports are unavailable.</GxNotice>
        <GxState v-else-if="crashes.status === 'ready' && !visibleReports.length">{{ search ? 'No matching reports.' : 'No crash reports.' }}</GxState>
        <div v-if="crashes.status === 'ready'" class="gx-crash-list">
          <article v-for="report in visibleReports" :key="report.id" class="gx-card gx-crash-row gx-panel"><button type="button" class="gx-btn" @click="crashFeed.open(report)"><strong>{{ report.name }}</strong><span>{{ reportDate(report.modifiedAt) }} · {{ reportSize(report.size) }}</span></button><button type="button" class="gx-btn" :disabled="crashes.deleting" @click="crashFeed.remove(report.id)">Delete</button></article>
        </div>
        <GxState v-if="crashes.previewStatus === 'loading'" loading>Loading report…</GxState>
        <section v-else-if="crashes.preview" class="gx-card gx-crash-preview gx-panel"><div class="gx-crash-preview__head"><strong>{{ crashes.preview.name }}</strong><button type="button" class="gx-btn gx-btn--tonal" @click="copyPreview">Copy visible text</button></div><p v-if="crashes.preview.truncated" class="gx-note">Preview truncated to the first 256 KiB.</p><pre>{{ crashes.preview.text }}</pre></section>
      </template>
      <GxState v-else>This Logs & Diagnostics page is unavailable in the offline preview.</GxState>
    </div>`,
}
