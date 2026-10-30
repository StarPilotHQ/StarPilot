import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { TroubleshootPage, TmuxPage } from "./diagnostic-tools.js"
import { navigate } from "./router.js"
import { SystemMonitor } from "./system-monitor.js"
import { CrashReportsFeed } from "./crash-reports.js"
import { storedSize } from "./record-deletion.js"
import { StarPortLogsFeed, StarPortLogSend, BUNDLE_URL, MESSAGE_LIMIT, fileUrl, outcomeLabel } from "./starport-logs.js"
import { MenuTile } from "./menu-tile.js"
import { BluetoothNotice, bluetoothConnection } from "./bluetooth-notice.js"

export const Logs = {
  name: "Logs",
  components: { GxState, GxNotice, BluetoothNotice, SystemMonitor, TroubleshootPage, TmuxPage, MenuTile },
  props: { path: { type: String, required: true }, mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    tools: { type: Array, required: true } },
  data: () => ({ crashes: { reports: [], scanIncomplete: false, listLimited: false, status: "idle", error: "", selected: null, preview: null, previewStatus: "idle", deleting: false, storageBytes: 0, reportCount: 0 }, search: "",
    starportLogs: { sessions: [], others: [], status: "idle", error: "" }, bluetooth: bluetoothConnection(),
    logSend: { state: "idle", message: "", available: true, error: "" }, sendNote: "", messageLimit: MESSAGE_LIMIT }),
  computed: {
    visibleReports() { return this.crashes.reports.filter((item) => item.name.toLowerCase().includes(this.search.toLowerCase())) },
  },
  created() {
    this.crashFeed = new CrashReportsFeed({ publish: (update) => Object.assign(this.crashes, update), unauthorized: this.unauthorized })
    this.starportFeed = new StarPortLogsFeed({ publish: (update) => Object.assign(this.starportLogs, update), unauthorized: this.unauthorized })
    this.sendFeed = new StarPortLogSend({ publish: (update) => Object.assign(this.logSend, update), unauthorized: this.unauthorized })
  },
  mounted() {
    if (this.path === "/logs/crashes" && this.mode === "local") this.crashFeed.start()
    if (this.path === "/logs/starport" && this.mode === "local") { this.starportFeed.start(); this.sendFeed.start() }
  },
  beforeUnmount() { this.crashFeed.stop(); this.starportFeed.stop(); this.sendFeed.stop() },
  watch: {
    path(next, previous) {
      if (next === "/logs/crashes" && this.mode === "local") this.crashFeed.start()
      else if (previous === "/logs/crashes") { this.crashFeed.stop(); this.search = "" }
      if (next === "/logs/starport" && this.mode === "local") { this.starportFeed.start(); this.sendFeed.start() }
      else if (previous === "/logs/starport") { this.starportFeed.stop(); this.sendFeed.stop() }
    },
  },
  methods: {
    storedSize,
    go: navigate,
    bundleUrl: () => BUNDLE_URL,
    fileUrl,
    outcomeLabel,
    async sendLogs() {
      if (await this.sendFeed.send(this.sendNote)) this.sendNote = ""
    },
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
      <h2 v-if="path === '/logs'">Logs & Diagnostics</h2>
      <p v-if="path === '/logs'" class="gx-note">Logs, diagnostics, drive state, and help.</p>
      <div v-if="path === '/logs'" class="gx-grid">
        <MenuTile v-for="tool in tools" :key="tool.path" :icon="tool.icon" :title="tool.name"
          :description="mode === 'sample' && tool.sampleDescription || tool.description"
          :availability="mode !== 'local' && tool.availability === 'local-only' ? 'Unavailable in preview' : ''"
          :disabled="mode !== 'local' && tool.availability === 'local-only'" @select="go(tool.path)" />
      </div>
      <section v-else-if="path === '/logs/help'" class="gx-settings">
        <header class="gx-settings__header gx-page-header"><div><h2>Help & Community</h2><p>Guides and help from the community.</p></div></header>
        <section class="gx-card"><div class="gx-section__header"><i class="bi bi-book" aria-hidden="true"></i><span class="gx-section__title">Wiki</span></div>
          <div class="gx-panel"><p class="gx-note">Find setup guides, feature explanations, and troubleshooting steps for StarPilot and Android Auto.</p>
            <a class="gx-btn gx-btn--tonal" href="https://wiki.firestar.link/" target="_blank" rel="noopener noreferrer">Open the Wiki</a></div></section>
        <section class="gx-card"><div class="gx-section__header"><i class="bi bi-discord" aria-hidden="true"></i><span class="gx-section__title">Discord</span></div>
          <div class="gx-panel"><p class="gx-note">Ask the community for help, share what you have tried, and follow StarPilot announcements.</p>
            <a class="gx-btn gx-btn--tonal" href="https://firestar.link/discord" target="_blank" rel="noopener noreferrer">Join Discord</a></div></section>
      </section>
      <TroubleshootPage v-else-if="path === '/logs/troubleshoot'" :mode="mode" :unauthorized="unauthorized" @navigate="go" />
      <TmuxPage v-else-if="path === '/logs/tmux'" :mode="mode" :unauthorized="unauthorized" @navigate="go" />
      <template v-else-if="path === '/logs/monitor'"><SystemMonitor :mode="mode" :unauthorized="unauthorized" /></template>
      <template v-else-if="path === '/logs/crashes' && mode === 'local'">

        <h2>Crash Reports</h2>
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
      <section v-else-if="path === '/logs/starport' && mode === 'local'" class="gx-settings gx-starport-logs" aria-labelledby="gx-starport-logs-title">
        <header class="gx-settings__header gx-page-header"><div><h2 id="gx-starport-logs-title">StarPort Logs</h2>
          <p>The last 20 connection sessions, newest first. The archive includes every session, a readable report for each, StarPort settings, the car view's renderer logs, and a snapshot of the device (Wi-Fi, Bluetooth and kernel logs, build, CPU load). Hardware addresses are masked.</p></div></header>
        <BluetoothNotice v-if="bluetooth" feature="Log downloads">Log summaries remain available.</BluetoothNotice>
        <div class="gx-settings__controls gx-actions"><a v-if="!bluetooth" class="gx-btn" :href="bundleUrl()" download><i class="bi bi-file-earmark-zip" aria-hidden="true"></i><span>Download all logs</span><span class="gx-starport-logs__format">ZIP</span></a><button type="button" class="gx-btn gx-btn--tonal" @click="starportFeed.load()"><i class="bi bi-arrow-clockwise" aria-hidden="true"></i><span>Refresh</span></button></div>
        <section v-if="logSend.available" class="gx-card gx-panel gx-starport-send" aria-labelledby="gx-starport-send-title">
          <div class="gx-section__header"><i class="bi bi-send" aria-hidden="true"></i><span id="gx-starport-send-title" class="gx-section__title">Send logs to the developer</span></div>
          <p class="gx-note">Sends the same archive as Download all logs straight from the device. Add a short note about what happened.</p>
          <div class="gx-starport-send__row"><input class="gx-field" type="text" v-model="sendNote" :maxlength="messageLimit" placeholder="What happened? (optional)" aria-label="Note for the developer" :disabled="logSend.state === 'sending'" @keydown.enter="sendLogs"><span class="gx-starport-send__count" aria-live="polite">{{ sendNote.length }}/{{ messageLimit }}</span><button type="button" class="gx-btn" :disabled="logSend.state === 'sending'" @click="sendLogs"><i class="bi bi-send" aria-hidden="true"></i><span>Send logs</span></button></div>
          <GxState v-if="logSend.state === 'sending'" loading>{{ logSend.message || 'Sending logs' }}…</GxState>
          <GxNotice v-else-if="logSend.state === 'sent'" tone="success">{{ logSend.message }}</GxNotice>
          <GxNotice v-else-if="logSend.state === 'failed'" tone="danger">{{ logSend.message }}</GxNotice>
          <GxNotice v-if="logSend.error" tone="danger">{{ logSend.error }}</GxNotice>
        </section>

        <GxState v-if="starportLogs.status === 'loading'" loading>Loading StarPort logs…</GxState>
        <GxNotice v-else-if="starportLogs.status === 'unavailable'" tone="danger">{{ starportLogs.error || 'StarPort logs are unavailable.' }}</GxNotice>
        <GxState v-else-if="starportLogs.status === 'ready' && !starportLogs.sessions.length">No StarPort sessions have been logged yet.</GxState>
        <div v-if="starportLogs.status === 'ready'" class="gx-starport-log-list">
          <article v-for="log in starportLogs.sessions" :key="log.name" class="gx-card gx-starport-log">
            <div class="gx-starport-log__body"><strong>{{ outcomeLabel(log.outcome) }}</strong><span>{{ log.car || 'Car not identified' }}{{ log.transport ? ' · ' + log.transport : '' }}{{ log.trigger ? ' · ' + log.trigger : '' }}</span><span>{{ reportDate(log.modifiedAt) }} · {{ reportSize(log.size) }}</span></div>
            <a v-if="!bluetooth" class="gx-icon-btn gx-starport-log__download" :href="fileUrl(log.name)" :download="log.name" :aria-label="'Download ' + log.name" :title="'Download ' + log.name"><i class="bi bi-download" aria-hidden="true"></i></a>
          </article>
        </div>
        <template v-if="starportLogs.status === 'ready' && starportLogs.others.length">
          <h3 class="gx-starport-logs__section-title">Renderer logs</h3>
          <div class="gx-starport-log-list">
            <article v-for="log in starportLogs.others" :key="log.name" class="gx-card gx-starport-log">
              <div class="gx-starport-log__body"><strong>{{ log.name }}</strong><span>{{ reportDate(log.modifiedAt) }} · {{ reportSize(log.size) }}</span></div>
              <a v-if="!bluetooth" class="gx-icon-btn gx-starport-log__download" :href="fileUrl(log.name)" :download="log.name" :aria-label="'Download ' + log.name" :title="'Download ' + log.name"><i class="bi bi-download" aria-hidden="true"></i></a>
            </article>
          </div>
        </template>
      </section>
      <GxState v-else>This Logs & Diagnostics page is unavailable in the offline preview.</GxState>
    </div>`,
}
