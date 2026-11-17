import { displayLocale } from "./localization.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { LocalAccess } from "./local-access.js"
import { MonitorFeed } from "./monitor-feed.js"
import { SoftwareStatusFeed } from "./software-status.js"
import { ModelStatusFeed } from "./models.js"
import { DriveStatsFeed } from "./drive-stats.js"
import { WeatherVitals } from "./weather.js"

// The six lightweight motifs follow the original Galaxy Personal Records row.
const RECORD_ART = {
  longestDrive: ["M4 12h16", "m14 6 6 6-6 6"],
  mostEngagedDay: ["M20 11a8 8 0 1 1-5-7", "m8 11 4 4 8-9"],
  bestWeek: ["M4 4v16h16", "m7 15 5-5 4 2 5-7", "M17 5h4v4"],
  highestStreak: ["m13 2-9 12h7l-1 8 10-13h-7z"],
  longestUndistractedDrive: ["m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z", "m8 12 3 3 5-6"],
  cleanDriveStreak: ["m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5z", "M20 2v4M18 4h4"],
}
export const recordArt = (id) => RECORD_ART[id] || ["m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z"]

const shown = (value) => typeof value === "string" && value.trim() ? value : "Unavailable"
const measure = (value, unit, digits = 0) =>
  typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}${unit}` : "Unavailable"
const counted = (value, noun) => `${value} ${noun}${value === 1 ? "" : "s"}`
const distance = (meters, isMetric) => measure(meters === null ? null : meters / (isMetric ? 1000 : 1609.344),
  isMetric ? " km" : " mi", 1)
const hours = (seconds) => measure(seconds === null ? null : seconds / 3600, " h", 1)
const percent = (value) => measure(value, "%")
const duration = (seconds) => {
  if (seconds == null) return "Unavailable"
  const minutes = Math.round(seconds / 60)
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes}m`
}
const driveDate = (start, end = null) => {
  if (start === null) return "Date unavailable"
  const date = new Date(start * 1000)
  if (!Number.isFinite(date.getTime())) return "Date unavailable"
  const calendar = new Intl.DateTimeFormat(displayLocale(), { month: "short", day: "numeric", year: "numeric" })
  const clock = (value) => new Intl.DateTimeFormat(displayLocale(), { hour: "2-digit", minute: "2-digit" }).format(new Date(value * 1000))
  const day = calendar.format(date)
  const endDay = end !== null ? calendar.format(new Date(end * 1000)) : null
  return `${day} · ${clock(start)}${end !== null ? `–${endDay === day ? "" : endDay + " · "}${clock(end)}` : ""}`
}
const dayLabel = (date) => new Intl.DateTimeFormat(displayLocale(), { timeZone: "UTC", weekday: "short" }).format(new Date(`${date}T00:00:00Z`))
const recordDisplay = (record, isMetric) => record.value === null ? "Unavailable" :
  record.unit === "m" ? distance(record.value, isMetric) : record.unit === "s" ? duration(record.value) :
    record.unit === "days" || record.unit === "drives" ? counted(record.value, record.unit.slice(0, -1)) :
      `${record.value}${record.unit === "%" ? "%" : record.unit ? ` ${record.unit}` : ""}`


export function modelArc(start, share) {
  if (!(share > 0)) return ""
  const point = (radius, percent) => {
    const angle = percent * Math.PI / 50 - Math.PI / 2
    return `${50 + radius * Math.cos(angle)},${50 + radius * Math.sin(angle)}`
  }
  const middle = start + share / 2, end = start + share
  return `M${point(48, start)} A48,48 0 0 1 ${point(48, middle)} A48,48 0 0 1 ${point(48, end)} ` +
    `L${point(32, end)} A32,32 0 0 0 ${point(32, middle)} A32,32 0 0 0 ${point(32, start)} Z`
}
const modelLabel = (value) => shown(value).replaceAll("Chestnut big", "Big")

export function driveSummary(feed) {
  const data = feed.status === "ready" ? feed.data : null
  if (!data) return null
  const isMetric = data.isMetric
  const last = data.lastDrive
  const analysisStatus = () => ({ analysisLabel: "Incomplete recording" })
  const maximum = Math.max(1, ...data.week.days.map((day) => day.distanceMeters ?? 0))
  const models = data.models
  const modelTotal = models.reduce((sum, model) => sum + (model.distanceMeters ?? 0), 0)
  const colors = ["#5ec8c8", "#8b6cc5", "#d4a060"]
  const modelColor = (index) => colors[index] || `hsl(${(index * 137.508) % 360} 55% 65%)`
  let offset = 0
  const recordedModels = models.map((model, index) => {
    const share = modelTotal > 0 ? (model.distanceMeters ?? 0) / modelTotal * 100 : 0
    const arc = modelArc(offset, share)
    offset += share
    return { index, name: modelLabel(model.name), distance: distance(model.distanceMeters, isMetric),
      drives: model.drives, color: modelColor(index), share, shareLabel: `${share.toFixed(1)}%`, arc }
  })
  return {
    analysis: data.analysis,
    last: last && { ...analysisStatus(last), routeId: last.routeId, date: driveDate(last.startTime, last.endTime), complete: last.complete,
      distance: distance(last.distanceMeters, isMetric), duration: duration(last.durationSeconds), engaged: percent(last.engagedPercent),
      aol: percent(last.aolPercent), aolDuration: duration(last.aolSeconds),
      average: measure(last.distanceMeters === null || !(last.durationSeconds > 0) ? null : last.distanceMeters / last.durationSeconds * (isMetric ? 3.6 : 2.236936), isMetric ? " km/h" : " mph"),
      model: modelLabel(last.model), distracted: last.distractedMoments === null ? "Unavailable" : String(last.distractedMoments),
      unresponsive: last.unresponsiveMoments === null ? "Unavailable" : String(last.unresponsiveMoments) },
    totals: { distance: distance(data.totals.distanceMeters, isMetric), duration: hours(data.totals.durationSeconds),
      engaged: duration(data.totals.engagedSeconds), drives: data.totals.drives,
      aol: duration(data.totals.aolSeconds), aolDrives: data.totals.aolDrives ?? 0 },
    week: { timezone: data.week.timezone || "UTC", distance: distance(data.week.distanceMeters, isMetric), duration: hours(data.week.durationSeconds),
      engaged: percent(data.week.engagedPercent), progress: data.week.engagedPercent ?? 0, drives: data.week.drives,
      aol: percent(data.week.aolPercent), aolDrives: data.week.aolDrives ?? 0,
      days: data.week.days.map((day) => ({ date: day.date, label: dayLabel(day.date),
        distance: distance(day.distanceMeters, isMetric), height: day.distanceMeters === null ? 0 : day.distanceMeters / maximum * 100 })) },
    recent: data.recentDrives.slice(0, 5).map((drive) => ({ ...analysisStatus(drive), routeId: drive.routeId, date: driveDate(drive.startTime, drive.endTime),
      complete: drive.complete, ignored: drive.ignored, distance: distance(drive.distanceMeters, isMetric),
      duration: duration(drive.durationSeconds), engaged: percent(drive.engagedPercent), aol: percent(drive.aolPercent), model: modelLabel(drive.model) })),
    records: data.records.map((record) => ({ id: record.id, label: record.label, display: recordDisplay(record, isMetric) })),
    models: recordedModels,
  }
}

// Each card retains its own source status. Missing/expired data is never a zero or a healthy state.
export function homeSummary(monitor, model, software) {
  const live = monitor.status === "current" ? monitor.snapshot : null
  const loaded = model.status === "ready" ? model.data : null
  const installed = software.status === "ready" ? software.data : null
  return {
    system: {
      available: !!live,
      cpu: measure(live?.cpuPercent, "%"),
      memory: measure(live?.memory?.percent, "%"),
      battery: measure(live?.batteryVoltage, " V", 1),
      storageLabel: live?.deviceType === "tici" ? "Internal storage" : "Storage",
      storageFree: live?.deviceType === "tici" ? measure(live?.storage?.freeGiB, " GiB", 1) : null,
      storage: live?.storage?.usedGiB != null && live?.storage?.totalGiB != null
        ? `${measure(live.storage.usedGiB, " GiB", 1)} / ${measure(live.storage.totalGiB, " GiB", 1)}` : "Unavailable",
      recordingStorage: live?.deviceType === "tici" && live?.recordingStorage ? {
        usage: live.recordingStorage.usedGiB != null && live.recordingStorage.totalGiB != null
          ? `${measure(live.recordingStorage.usedGiB, " GiB", 1)} / ${measure(live.recordingStorage.totalGiB, " GiB", 1)}` : "Unavailable",
        free: measure(live.recordingStorage.freeGiB, " GiB", 1),
      } : null,
      observed: live ? new Date(live.sampledAt * 1000).toLocaleString(displayLocale()) : "Unavailable",
    },
    model: {
      available: !!loaded,
      health: loaded ? shown(loaded.health).replaceAll("-", " ") : "Unavailable",
      variant: loaded?.loadedId ? loaded.variant === "chestnut" ? "Big" : "Small" : "Identity unavailable",
      fallback: loaded?.fallbackReason === "chestnut-run-stalled" ? "Chestnut output stopped; Small restarted for this drive" :
        loaded?.fallbackReason === "chestnut-load-failed" ? "Small model after Chestnut load failure" : null,
    },
    software: {
      available: !!installed,
      version: shown(installed?.installed?.displayVersion ?? installed?.installed?.version),
      branch: shown(installed?.installed?.branch),
      update: installed?.updater?.finalizedUpdateReady === true ? "Finalized update ready" :
        installed?.updater?.finalizedUpdateReady === false ? "None reported" : "Unavailable",
    },
  }
}

export const Home = {
  name: "Home",
  components: { GxState, GxNotice, LocalAccess, WeatherVitals },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    go: { type: Function, required: true } },
  data: () => ({
    showAllModels: false, activeModel: 0,
    monitor: { snapshot: null, status: "idle", error: "" },
    model: { data: null, status: "idle", error: "" },
    software: { data: null, status: "idle", error: "" },
    drives: { data: null, status: "idle", error: "", busy: false },
  }),
  computed: {
    summary() { return homeSummary(this.monitor, this.model, this.software) },
    driving() { return driveSummary(this.drives) },
    visibleModels() { return this.showAllModels ? this.driving?.models || [] : (this.driving?.models || []).slice(0, 6) },
    selectedModel() { return this.driving?.models[this.activeModel] || this.driving?.models[0] || null },
  },
  created() {
    const revoked = () => { this.stopFeeds(); this.unauthorized() }
    this.monitorFeed = new MonitorFeed({ mode: this.mode, publish: (update) => Object.assign(this.monitor, update), unauthorized: revoked })
    this.modelFeed = new ModelStatusFeed({ publish: (update) => Object.assign(this.model, update), unauthorized: revoked })
    this.softwareFeed = new SoftwareStatusFeed({ includeHistory: false, publish: (update) => Object.assign(this.software, update), unauthorized: revoked })
    this.driveFeed = new DriveStatsFeed({ publish: (update) => Object.assign(this.drives, update), unauthorized: revoked })
  },
  mounted() {
    this.visibility = () => { if (document.hidden) this.stopFeeds(); else this.startFeeds() }
    document.addEventListener("visibilitychange", this.visibility)
    if (!document.hidden) this.startFeeds()
  },
  beforeUnmount() { document.removeEventListener("visibilitychange", this.visibility); this.stopFeeds() },
  methods: {
    recordArt,
    counted,
    startFeeds() {
      if (this.mode !== "local") return
      this.monitorFeed.start()
      this.modelFeed.start()
      this.softwareFeed.start()
      this.driveFeed.start()
    },
    stopFeeds() {
      this.monitorFeed.stop()
      this.modelFeed.stop()
      this.softwareFeed.stop()
      this.driveFeed.stop()
      this.monitor.snapshot = null
      this.monitor.status = "idle"
    },
    ignoreDrive(drive) {
      if (this.mode !== "local" || document.hidden || this.drives.busy) return
      return this.driveFeed.ignore(drive.routeId, !drive.ignored)
    },
  },
  template: `
    <div class="gx-view gx-home">
      <div class="gx-home__hero">
        <div><h1>Dashboard</h1><p class="gx-note">Your saved drives and current device</p></div>

      </div>
      <GxState v-if="mode !== 'local'">Dashboard information is available on your device.</GxState>
      <template v-else>
        <section class="gx-card gx-home__last">
          <div class="gx-home__kicker"><span></span> Last drive</div>
          <p v-if="drives.status !== 'ready'" role="status">{{ drives.status === 'loading' ? 'Loading your drives…' : drives.error || 'Drive history is unavailable.' }}</p>
          <p v-else-if="!driving.last">No saved drives yet.</p>
          <template v-else>
            <p class="gx-home__date">{{ driving.last.date }}</p>
            <p v-if="!driving.last.complete" class="gx-home__subtle">{{ driving.last.analysisLabel }}</p>
            <div class="gx-home__four"><div><strong>{{ driving.last.distance }}</strong><span>distance</span></div>
              <div><strong>{{ driving.last.duration }}</strong><span>duration</span></div>
              <div><strong>{{ driving.last.engaged }}</strong><span>engaged</span></div>
              <div><strong>{{ driving.last.average }}</strong><span>average speed</span></div></div>
            <p class="gx-home__subtle">{{ driving.last.aol }} AOL steering only · {{ driving.last.aolDuration }}</p>
            <div class="gx-home__attention"><span class="gx-home__model-tag"><i class="bi bi-cpu"></i> {{ driving.last.model }}</span><span><i class="bi bi-eye"></i> {{ driving.last.distracted === "Unavailable" ? "Attention data unavailable" : driving.last.distracted + " distracted " + (driving.last.distracted === "1" ? "moment" : "moments") }}</span>
              <span><i class="bi bi-exclamation-triangle"></i> {{ driving.last.unresponsive }} unresponsive {{ driving.last.unresponsive === "1" ? "moment" : "moments" }}</span></div>
          </template>
        </section>
        <template v-if="driving">
          <p v-if="driving.analysis.pending" class="gx-home__analysis" role="status">
            <i class="bi bi-hourglass-split"></i> {{ driving.analysis.running ? 'Updating drive history' : driving.analysis.state === 'waitingParked' ? 'Drive history will update when parked' : 'Drive history queued' }}{{ driving.analysis.pending ? ' · ' + driving.analysis.pending + ' remaining' : '' }}
          </p>
          <div class="gx-home__section-title"><span></span>Your driving</div>
          <div class="gx-home__totals">
            <section class="gx-card"><h2>Saved History</h2><strong>{{ driving.totals.distance }}</strong><span>{{ counted(driving.totals.drives, "drive") }}</span></section>
            <section class="gx-card"><h2>Time on the Road</h2><strong>{{ driving.totals.duration }}</strong><span>{{ driving.totals.engaged }} engaged</span><span>{{ driving.totals.aol }} AOL steering only · {{ driving.totals.aolDrives }}/{{ driving.totals.drives }} drives measured</span></section>
            <section class="gx-card"><h2>This Week</h2><strong>{{ driving.week.distance }}</strong><span>{{ counted(driving.week.drives, "drive") }} · {{ driving.week.duration }}</span></section>
          </div>
          <div class="gx-home__pair">
            <section class="gx-card gx-home__week"><div class="gx-section__header"><i class="bi bi-calendar-week"></i><h2 class="gx-section__title">This Week</h2></div>
              <div class="gx-home__week-top"><div class="gx-home__donut" :style="{ '--gx-home-progress': driving.week.progress + '%' }">
                <strong>{{ driving.week.engaged }}</strong><span>engaged</span></div>
                <div><strong>{{ driving.week.distance }}</strong><span>across {{ counted(driving.week.drives, "drive") }}</span></div></div>
              <p class="gx-home__subtle">{{ driving.week.aol }} AOL steering only · {{ driving.week.aolDrives }}/{{ driving.week.drives }} drives measured</p>
              <h3>Distance per day · {{ driving.week.timezone }}</h3>
              <div class="gx-home__bars"><div v-for="day in driving.week.days" :key="day.date" :aria-label="day.date + ': ' + day.distance" :title="day.date + ': ' + day.distance">
                <div class="gx-home__bar-track"><span :style="{ height: day.height + '%' }"></span></div><small>{{ day.label }}</small><small>{{ day.distance }}</small></div></div>
            </section>
            <section class="gx-card gx-home__records"><div class="gx-section__header"><i class="bi bi-trophy"></i><h2 class="gx-section__title">Personal Records</h2></div>
              <p v-if="!driving.records.length" class="gx-home__subtle">Records will appear as drives are saved.</p>
              <div v-for="record in driving.records" :key="record.id" class="gx-home__record"><span class="gx-home__record-art" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path v-for="(path, index) in recordArt(record.id)" :key="index" :d="path" /></svg></span><span class="gx-home__record-label">{{ record.label }}</span><strong>{{ record.display }}</strong></div>
            </section>
          </div>
          <section class="gx-card gx-home__recent"><div class="gx-section__header"><i class="bi bi-clock-history"></i><h2 class="gx-section__title">Recent Drives</h2></div>
            <p v-if="!driving.recent.length" class="gx-home__subtle">No saved drives yet.</p>
            <div v-for="drive in driving.recent" :key="drive.routeId" class="gx-home__drive" :class="{ 'is-ignored': drive.ignored }">
              <div><strong>{{ drive.date }}</strong><small>{{ drive.model }}</small></div>
              <div><strong>{{ drive.complete ? drive.distance : drive.analysisLabel }}</strong><small>{{ drive.duration }} · {{ drive.engaged }} engaged</small><small>{{ drive.aol }} AOL steering only</small></div>
              <div><small>{{ drive.ignored ? 'Excluded from totals' : drive.complete ? 'Included in totals' : drive.analysisLabel }}</small>
                <button type="button" class="gx-home__link" :inert="drives.busy" :aria-busy="drives.busy || undefined" @click="ignoreDrive(drive)">
                  {{ drive.ignored ? 'Include in stats' : 'Ignore drive stats' }}</button></div>
            </div>
            <GxNotice tone="danger" v-if="drives.error">{{ drives.error }}</GxNotice>
          </section>
          <section class="gx-card gx-home__models"><div class="gx-section__header"><i class="bi bi-cpu"></i><h2 class="gx-section__title">Recorded Models</h2></div>
            <p v-if="!driving.models.length" class="gx-home__subtle">Recorded models will appear as drives are saved.</p>
            <div v-else class="gx-home__model-layout">
              <div class="gx-home__model-chart">
                <div class="gx-home__model-ring">
                  <svg viewBox="0 0 100 100" role="group" aria-label="Recorded model distance. Hover, tap or focus a segment for details.">
                    <circle cx="50" cy="50" r="40" fill="none" stroke="var(--outline)" stroke-width="16" />
                    <template v-for="model in driving.models" :key="model.index">
                      <path v-if="model.share > 0" :d="model.arc" :fill="model.color" tabindex="0" role="button"
                        :aria-label="model.name + ': ' + model.shareLabel + ', ' + model.distance + ', ' + counted(model.drives, 'drive')"
                        :aria-pressed="selectedModel?.index === model.index" :class="{ 'is-selected': selectedModel?.index === model.index }"
                        @pointerenter="activeModel = model.index" @focus="activeModel = model.index" @click="activeModel = model.index"
                        @keydown.enter.prevent="activeModel = model.index" @keydown.space.prevent="activeModel = model.index">
                        <title>{{ model.name }} · {{ model.shareLabel }} · {{ model.distance }}</title>
                      </path>
                    </template>
                  </svg>
                  <div class="gx-home__model-center" aria-hidden="true"><strong>{{ selectedModel?.shareLabel }}</strong><small>of distance</small></div>
                </div>
                <div class="gx-home__model-detail" aria-live="polite"><strong>{{ selectedModel?.name }}</strong>
                  <small>{{ counted(selectedModel?.drives, "drive") }} · {{ selectedModel?.distance }}</small></div>
                <small class="gx-home__subtle">Hover or tap a segment</small>
              </div>
              <div class="gx-home__model-legend">
                <div class="gx-home__model-list" :class="{ 'is-expanded': showAllModels }">
                  <button v-for="model in visibleModels" :key="model.index" type="button" class="gx-home__model-row"
                    :aria-pressed="selectedModel?.index === model.index" @pointerenter="activeModel = model.index"
                    @focus="activeModel = model.index" @click="activeModel = model.index">
                    <span class="gx-home__swatch" :style="{ background: model.color }"></span>
                    <span><strong>{{ model.name }}</strong><small>{{ counted(model.drives, "drive") }} · {{ model.distance }}</small></span>
                    <small class="gx-home__model-share">{{ model.shareLabel }}</small>
                  </button>
                </div>
                <button v-if="driving.models.length > 6" type="button" class="gx-home__link" :aria-expanded="showAllModels"
                  @click="showAllModels = !showAllModels">{{ showAllModels ? 'Show top 6' : 'Show all ' + driving.models.length + ' models' }}</button>
              </div>
            </div>
          </section>
        </template>
        <div class="gx-home__section-title"><span></span>Your device</div>
        <LocalAccess ref="localAccess" :mode="mode" :on-unauthorized="unauthorized" />
        <div class="gx-home__grid">
          <section class="gx-card gx-home__card">
            <div class="gx-section__header"><i class="bi bi-activity"></i><h2 class="gx-section__title">System</h2></div>
            <p v-if="!summary.system.available" role="status">Live system status unavailable.</p>
            <template v-else><div class="gx-home__metrics"><div><strong>{{ summary.system.cpu }}</strong><span>CPU</span></div>
              <div><strong>{{ summary.system.memory }}</strong><span>Memory</span></div></div>
              <p>12 V battery: <strong>{{ summary.system.battery }}</strong></p>
              <p>{{ summary.system.storageLabel }}: {{ summary.system.storage }}<span v-if="summary.system.storageFree !== null"> · {{ summary.system.storageFree }} free</span></p>
              <p v-if="summary.system.recordingStorage">Recording drive: {{ summary.system.recordingStorage.usage }} · {{ summary.system.recordingStorage.free }} free</p><small>Sampled {{ summary.system.observed }}</small></template>
            <WeatherVitals :metric="drives.data?.isMetric === true" :mode="mode" :unauthorized="unauthorized" />
            <button type="button" class="gx-home__link" @click="go('/logs/monitor')">Open System Monitor <i class="bi bi-arrow-right"></i></button>
          </section>
          <section class="gx-card gx-home__card">
            <div class="gx-section__header"><i class="bi bi-cpu"></i><h2 class="gx-section__title">Driving model</h2></div>
            <p v-if="!summary.model.available" role="status">Model identity unavailable.</p>
            <template v-else><p>Reported health: <strong>{{ summary.model.health }}</strong></p>
              <p>Loaded variant: <strong>{{ summary.model.variant }}</strong></p>
              <small v-if="summary.model.fallback">{{ summary.model.fallback }}</small></template>
            <button type="button" class="gx-home__link" @click="go('/manage_models')">Open Driving Model <i class="bi bi-arrow-right"></i></button>
          </section>
          <section class="gx-card gx-home__card">
            <div class="gx-section__header"><i class="bi bi-arrow-repeat"></i><h2 class="gx-section__title">Software</h2></div>
            <p v-if="!summary.software.available" role="status">Installed software report unavailable.</p>
            <template v-else><p>Installed: <strong>{{ summary.software.version }}</strong></p>
              <p>Branch: <strong>{{ summary.software.branch }}</strong></p>
              <p>Last updater report: <strong>{{ summary.software.update }}</strong></p></template>
            <button type="button" class="gx-home__link" @click="go('/system')">Open Software &amp; Updates <i class="bi bi-arrow-right"></i></button>
          </section>
        </div>
      </template>
    </div>`,
}
