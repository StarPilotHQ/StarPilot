import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { displayNumber, processFeature, processRows, processState, vital } from "./system-monitor-data.js"
import { MonitorFeed } from "./monitor-feed.js"
import { GalaxySelect } from "./galaxy-select.js"

export const SystemMonitor = {
  name: "SystemMonitor",
  components: { GxState, GxNotice, GalaxySelect },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ snapshot: null, status: "loading", error: "", query: "", scope: "comma", sort: "cpu", descending: true }),
  mounted() {
    this.feed = new MonitorFeed({ mode: this.mode, publish: (state) => Object.assign(this, state), unauthorized: this.unauthorized })
    this.feed.start()
  },
  beforeUnmount() { this.feed?.stop() },
  computed: {
    rows() { return processRows(this.snapshot, this) },
    captured() { return this.snapshot ? new Date(this.snapshot.sampledAt * 1000).toLocaleString() : "—" },
    uptime() {
      const s = this.snapshot?.uptimeSeconds
      return s === null || s === undefined ? "—" : `${Math.floor(s / 3600)}h ${Math.floor(s / 60) % 60}m`
    },
    vramUsed() { return vital(this.snapshot, "memoryUsedBytes", "memoryMaxAgeMs") },
    vramTotal() { return vital(this.snapshot, "memoryTotalBytes", "memoryMaxAgeMs") },
  },
  methods: {
    number: displayNumber,
    feature: processFeature,
    state: processState,
    sensor(field, budget) { return vital(this.snapshot, field, budget) },
    sortBy(key) { if (this.sort === key) this.descending = !this.descending; else { this.sort = key; this.descending = ["cpu", "memoryMiB"].includes(key) } },
    arrow(key) { return this.sort === key ? (this.descending ? " ↓" : " ↑") : "" },
    ariaSort(key) { return this.sort !== key ? "none" : this.descending ? "descending" : "ascending" },
  },
  template: `
    <div class="gx-monitor">
      <div class="gx-monitor__toolbar gx-page-header"><div><h2>System Monitor</h2><div class="gx-note">{{ mode === 'sample' ? 'Synthetic sample' : 'Local system' }} · Captured {{ captured }}</div></div><span class="gx-chip">{{ mode === 'sample' ? 'Offline preview' : status === 'current' ? 'Live' : 'Unavailable' }}</span></div>
      <p v-if="mode === 'sample'" class="gx-note">Illustrative values only. No device was read.</p>
      <p v-else class="gx-note">Read-only system activity. Updates every few seconds.</p>
      <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
      <GxState v-else-if="!snapshot" loading>Loading system activity…</GxState>
      <template v-else>
        <div class="gx-monitor__summary">
          <section class="gx-card gx-monitor__metric"><span>CPU</span><strong>{{ number(snapshot.cpuPercent, '%') }}</strong><small>{{ snapshot.cores.length }} cores · overall usage</small></section>
          <section class="gx-card gx-monitor__metric"><span>Memory</span><strong>{{ number(snapshot.memory.percent, '%') }}</strong><small>{{ snapshot.memory.usedMiB == null ? '—' : number(snapshot.memory.usedMiB / 1024) }} / {{ snapshot.memory.totalMiB == null ? '—' : number(snapshot.memory.totalMiB / 1024) }} GiB</small><progress v-if="snapshot.memory.percent != null" :value="snapshot.memory.percent" max="100" aria-label="Memory usage"></progress></section>
          <section class="gx-card gx-monitor__metric"><span>Processes</span><strong>{{ snapshot.processCount ?? '—' }}</strong><small>Uptime {{ uptime }}</small></section>
          <section class="gx-card gx-monitor__metric"><span>{{ snapshot.deviceType === 'tici' ? 'Internal storage' : 'Storage' }}</span><strong>{{ number(snapshot.storage.usedGiB) }} GiB</strong><small>{{ number(snapshot.storage.totalGiB) }} GiB total<span v-if="snapshot.deviceType === 'tici'"> · {{ number(snapshot.storage.freeGiB) }} GiB free</span></small></section>
          <section v-if="snapshot.deviceType === 'tici' && snapshot.recordingStorage" class="gx-card gx-monitor__metric"><span>Recording drive</span><strong>{{ number(snapshot.recordingStorage.usedGiB) }} GiB</strong><small>{{ number(snapshot.recordingStorage.totalGiB) }} GiB total · {{ number(snapshot.recordingStorage.freeGiB) }} GiB free</small></section>
          <section class="gx-card gx-monitor__metric"><span>Onboard CPU temperature</span><strong>{{ number(sensor('cpuTempC', 'onboardMaxAgeMs'), ' °C') }}</strong></section>
          <section class="gx-card gx-monitor__metric"><span>Onboard GPU temperature</span><strong>{{ number(sensor('gpuTempC', 'onboardMaxAgeMs'), ' °C') }}</strong></section>
          <section class="gx-card gx-monitor__metric"><span>eGPU hotspot temperature</span><strong>{{ number(sensor('hotspotTempC', 'maxAgeMs'), ' °C') }}</strong></section>
          <section class="gx-card gx-monitor__metric"><span>eGPU temperature</span><strong>{{ number(sensor('gpuEdgeTempC', 'maxAgeMs'), ' °C') }}</strong></section>
          <section class="gx-card gx-monitor__metric"><span>eGPU VRAM</span><strong>{{ vramUsed == null ? '—' : number(vramUsed / 1073741824) + ' GiB' }}</strong><small v-if="vramTotal != null">{{ number(vramTotal / 1073741824) }} GiB total</small><progress v-if="vramTotal> 0 && vramUsed != null" :value="vramUsed" :max="vramTotal" aria-label="eGPU VRAM usage"></progress></section>
        </div>
        <details class="gx-card gx-monitor__cores"><summary>CPU cores</summary><div><span v-for="core in snapshot.cores" :key="core.name">{{ core.name.toUpperCase() }} <b>{{ number(core.percent, '%') }}</b><progress v-if="core.percent != null" :value="core.percent" max="100" :aria-label="core.name + ' usage'"></progress></span></div></details>
        <div class="gx-monitor__filters"><input class="gx-field" type="search" v-model="query" aria-label="Search processes" placeholder="Search feature, process, PID or user…"><GalaxySelect class="gx-field gx-field--full" v-model="scope" aria-label="Process group"><option value="comma">Comma processes</option><option value="users">Apps and services</option><option value="all">All processes</option></GalaxySelect></div>
        <p class="gx-note">{{ rows.length }} processes shown. {{ snapshot.cpuPercent == null ? 'CPU sample unavailable.' : '' }}</p>
        <section class="gx-card gx-monitor__table" tabindex="0" aria-label="Process table; scroll horizontally for more columns">
          <table><thead><tr><th v-for="column in [['name','Process'],['pid','PID'],['cpu','CPU'],['memoryMiB','Memory'],['user','User'],['state','Status']]" :key="column[0]" :aria-sort="ariaSort(column[0])"><button type="button" @click="sortBy(column[0])">{{ column[1] }}{{ arrow(column[0]) }}</button></th></tr></thead>
          <tbody><tr v-for="process in rows" :key="process.pid"><td :title="process.name">{{ process.name }}<span v-if="feature(process)" class="gx-monitor__feature"> ({{ feature(process) }})</span></td><td>{{ process.pid }}</td><td>{{ number(process.cpu, '%') }}</td><td>{{ number(process.memoryMiB) }} MiB</td><td>{{ process.user }}</td><td><span class="gx-chip">{{ state(process.state) }}</span></td></tr><tr v-if="!rows.length"><td colspan="6" class="gx-empty">No matching processes.</td></tr></tbody></table>
        </section>
      </template>
    </div>`,
}
