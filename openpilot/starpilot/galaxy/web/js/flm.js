import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { GxDialog } from "./dialog.js"
import { SettingsFeed, settingsBusy } from "./settings.js"
import { GalaxySettingRow, settingControl } from "./galaxy-setting-row.js"
import { PollTimer, connectionError } from "./polling.js"
import { requestJson } from "./startup.js"
import { PlotGraph, PlotsPage, chartGeometry } from "./plots.js"
import { validTrainingResults, readable, detailText, surfaceRows, coordinateText, trackingView, measurementText, validTrackingValues, trackingPlotView, sourceSeconds, validLearningPreference, SURFACE_GROUPS, tunerStatus, learningReason, sourceCaptureCounts, historyTitle, offroadView } from "./flm-training-results.js"

const VIEWS = ["overview", "tune", "activity", "plots"]
const PARAMETER_LABELS = {
  ff_gain_left: "Left steering gain", ff_gain_right: "Right steering gain",
  turn_in_boost_left: "Left turn-in boost", turn_in_boost_right: "Right turn-in boost",
  unwind_taper_left: "Left unwind taper", unwind_taper_right: "Right unwind taper",
  center_taper_max: "Center taper", highway_center_taper_max: "Highway center taper",
  center_deadband_crawl_deg: "Crawl center deadband", center_deadband_low_deg: "Low-speed center deadband",
  center_deadband_mid_deg: "Mid-speed center deadband", center_deadband_fast_deg: "Fast center deadband",
  center_deadband_highway_deg: "Highway center deadband",
  turn_in_threshold_reduction_left: "Left turn-in threshold", turn_in_threshold_reduction_right: "Right turn-in threshold",
  unwind_threshold_increase_left: "Left unwind threshold", unwind_threshold_increase_right: "Right unwind threshold",
  crawl_turn_in_ff_boost_left: "Left crawl turn-in boost", crawl_turn_in_ff_boost_right: "Right crawl turn-in boost",
  low_speed_angle_assist_max_torque: "Low-speed angle assistance",
  curvy_speed_min: "Unwind range · lower speed", curvy_speed_max: "Unwind range · upper speed",
  curvy_turn_in_trim_speed_min: "Turn-in range · lower speed", curvy_turn_in_trim_speed_max: "Turn-in range · upper speed",
  curvy_turn_in_trim_left: "Left turn-in trim", curvy_turn_in_trim_right: "Right turn-in trim",
  curvy_unwind_floor_relief_left: "Left unwind floor relief", curvy_unwind_floor_relief_right: "Right unwind floor relief",
  curvy_unwind_extra_reduction_left: "Left extra unwind relief", curvy_unwind_extra_reduction_right: "Right extra unwind relief",
}

export const FlmPage = {
  name: "FlmPage",
  components: { GxState, GxNotice, GxDialog, PlotGraph, PlotsPage, GalaxySettingRow },
  props: { initialView: { type: String, default: "overview" }, mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    go: { type: Function, default: path => { window.location.hash = path } } },
  data: () => ({ report: null, error: "", loading: false, request: null, receivedAt: 0, now: 0,
    manualState: { status: "idle", data: null, pending: null, error: "" }, manualResetRequested: false,
    view: "overview", group: "balance", showAllHistory: false, groups: SURFACE_GROUPS }),
  watch: { initialView(view) { this.selectView(view) }, mode() { this.syncManualControls() } },
  mounted() {
    this.activePage = true
    this.manualFeed = new SettingsFeed({ publish: update => Object.assign(this.manualState, update), unauthorized: this.unauthorized })
    this.selectView(this.initialView)
    this.poller = new PollTimer({ read: () => this.load(), interval: 1000 })
    this.visibility = () => {
      this.now = Date.now()
      if (document.hidden) this.request?.abort()
      else this.load()
    }
    document.addEventListener("visibilitychange", this.visibility)
    if (this.mode === "local") { this.poller.start(); this.load() }
  },
  beforeUnmount() {
    this.activePage = false; this.manualFeed?.stop(); clearTimeout(this.freshnessTimer); this.poller?.stop(); this.request?.abort(); this.request = null
    document.removeEventListener("visibilitychange", this.visibility)
  },
  computed: {
    manualRows() { return (this.manualState.data?.rows || []).map((row, index) => ({ row, index })).filter(({ row }) => this.selectedGroup.names.includes(row.coordinate)) },
    manualActions() { return (this.manualState.data?.rows || []).map((row, index) => ({ row, index })).filter(({ row }) => row.confirm) },
    manualBusy() { return settingsBusy(this.manualState) },
    current() { return this.report?.current },
    preference() { return validLearningPreference(this.report?.preference) ? this.report.preference : null },
    choiceLive() { return !this.error && !!this.preference && Math.max(0, this.now - this.receivedAt) <= 2000 },
    live() { return !this.error && !!this.current?.fresh && this.current.ageSeconds + Math.max(0, this.now - this.receivedAt) / 1000 <= 2 &&
      !(this.choiceLive && (this.preference.enabled === false || this.preference.parked === true)) },
    state() { return tunerStatus(this.report, this.live, this.choiceLive, this.error) },
    vehicle() { return this.preference?.vehicle ? this.preference.vehicle.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : "Your steering, measured" },
    currentApplied() { return this.live && this.current?.enabled === true && this.current?.application?.applied === true },
    rows() { return surfaceRows(this.report, this.live) },
    tracking() { return trackingView(this.report, this.live) },
    trackingValues() { return validTrackingValues(this.current?.trackingValues) ? this.current.trackingValues : null },
    trackingProgress() { return sourceCaptureCounts(this.report) },
    measurements() { return this.tracking ? this.trackingProgress?.capturedPoses ?? null : this.current?.progress?.samples ?? null },
    trackingPlot() { return trackingPlotView(this.report, this.now - this.receivedAt) },
    offroad() { return offroadView(this.report) },
    pairedTrackingChart() { return chartGeometry(this.trackingPlot?.points || [], ["desiredForceAccel", "measuredForceAccel"]) },
    pairedErrorChart() { return chartGeometry(this.trackingPlot?.points || [], ["errorForceAccel"]) },
    details() { return Object.entries(this.current?.details || {}) },
    reportAge() { return this.current ? this.current.ageSeconds + Math.max(0, this.now - this.receivedAt) / 1000 : null },
    appliedCount() { return this.currentApplied && this.rows.every(row => row.applied !== null) ? this.rows.filter(row => row.applied !== 0).length : null },
    acceptedCount() { return this.trackingValues ? this.rows.filter(row => row.accepted !== null && row.accepted !== 0).length : null },
    groupRows() { return this.rows.filter(row => this.selectedGroup.names.includes(row.name)) },
    selectedGroup() { return this.groups.find(group => group.id === this.group) || this.groups[0] },
    historyRows() { return this.showAllHistory ? this.report?.history || [] : (this.report?.history || []).slice(0, 6) },
    improvement() {
      const before = this.tracking?.baselineErrorRms, after = this.tracking?.futureErrorRms
      return Number.isFinite(before) && before > 0 && Number.isFinite(after) ? (before - after) / before * 100 : null
    },
    quota() {
      const required = this.tracking?.requiredEpisodes, completed = this.tracking?.futureEpisodes
      return Number.isSafeInteger(required) && required > 0 ? { required, completed, percent: Math.min(100, completed / required * 100) } : null
    },
    losses() {
      const d = this.current?.details || {}, baseline = d["Fit baseline loss"], learned = d["Fit candidate loss"]
      return typeof baseline === "number" && Number.isFinite(baseline) && baseline >= 0 &&
        typeof learned === "number" && Number.isFinite(learned) && learned >= 0 ? { baseline, learned } : null
    },
  },
  methods: {
    readable, detailText, coordinateText, measurementText, sourceSeconds, learningReason, historyTitle,
    detailLabel(key) { return ['Fit attempts', 'Fit results', 'Fit in progress', 'Submitted fit jobs (reporter lifetime)'].includes(key) ? 'Legacy fitter · ' + key : readable(key) },
    amount(value) { return value === null || value === undefined ? "—" : value.toLocaleString() },
    parameterLabel(name) { return PARAMETER_LABELS[name] || readable(name) },
    groupChanges(group) {
      if (!this.trackingValues) return "Not reported"
      const count = this.rows.filter(row => group.names.includes(row.name) && row.accepted !== null && row.accepted !== 0).length
      return count ? `${count} accepted` : "Baseline"
    },
    selectView(view) { if (VIEWS.includes(view)) { this.view = view; return this.syncManualControls() } },
    syncManualControls() {
      if (!this.manualFeed) return
      if (this.activePage && this.mode === "local" && this.view === "tune") {
        if (!this.manualFeed.active) return this.manualFeed.start("tuning")
      } else { this.manualResetRequested = false; this.manualFeed.stop() }
    },
    async reviewManualReset(index, direction) {
      const row = this.manualState.data?.rows?.[index]
      if (this.view !== "tune" || this.manualBusy || this.manualState.error || !row?.confirm || !row.available) return
      this.manualResetRequested = true
      await this.manualFeed.preview(index, direction)
      if (!this.manualState.pending) this.manualResetRequested = false
    },
    cancelManualReset() { this.manualResetRequested = false; this.manualFeed.cancel() },
    async confirmManualReset() { await this.manualFeed.confirm(); this.manualResetRequested = false },
    async saveManual(index, value) {
      const row = this.manualState.data?.rows?.[index]
      if (this.view !== "tune" || !this.manualFeed?.active || this.manualBusy || this.manualState.error ||
          !this.selectedGroup.names.includes(row?.coordinate) || !row.available || !row.action || settingControl(row) !== "slider") return false
      await this.manualFeed.previewValue(index, value)
      return !this.manualState.error && this.manualState.status === "ready"
    },
    tabKey(event) {
      const delta = { ArrowRight: 1, ArrowLeft: -1, Home: -VIEWS.length, End: VIEWS.length }[event.key]
      if (delta === undefined) return
      event.preventDefault()
      const index = VIEWS.indexOf(this.view)
      const next = event.key === "Home" ? 0 : event.key === "End" ? VIEWS.length - 1 : (index + delta + VIEWS.length) % VIEWS.length
      this.selectView(VIEWS[next])
      event.currentTarget.parentElement.querySelectorAll('[role="tab"]')[next]?.focus()
    },
    async load() {
      this.now = Date.now()
      if (!this.activePage || this.mode !== "local" || this.request || document.hidden) return
      const controller = new AbortController()
      this.request = controller; this.loading = true
      try {
        const value = await requestJson("./api/flm/training-results", { request: { signal: controller.signal } })
        if (this.request !== controller || !this.activePage) return
        if (!validTrainingResults(value)) throw new Error("The device returned an unsupported training report.")
        this.report = value; this.error = ""; this.receivedAt = this.now = Date.now()
        clearTimeout(this.freshnessTimer)
        const age = value.current?.fresh ? value.current.ageSeconds * 1000 : 0
        this.freshnessTimer = setTimeout(() => { this.now = Date.now() }, Math.max(0, 2000 - age) + 1)
      } catch (error) {
        if (this.request !== controller || !this.activePage) return
        if (error.status === 401 || ["access_unavailable", "setup_required"].includes(error.code)) this.unauthorized()
        else this.error = connectionError(error, "Live update unavailable. Reconnecting automatically…")
      } finally {
        if (this.request === controller) { this.request = null; this.loading = false }
      }
    },
  },
  template: `<div class="gx-view gx-tuner">
    <header class="gx-page-header gx-settings__header gx-tuner__heading"><div><p class="gx-tuner__eyebrow">STEERING · AUTO LEARN</p><h2>Tuning and Analysis</h2><p>{{ vehicle }}</p></div></header>
    <GxState v-if="mode !== 'local'">Connect to your device to see its steering tune.</GxState>
    <template v-else>
      <GxNotice v-if="error && view !== 'plots'" tone="danger">{{ error }}</GxNotice>
      <section v-if="view !== 'plots'" class="gx-card gx-tuner__hero" :data-tone="state.tone" aria-label="Auto Learn status">
        <div class="gx-tuner__hero-icon" aria-hidden="true"><i class="bi bi-sliders2"></i></div>
        <div class="gx-tuner__hero-copy"><div class="gx-tuner__eyebrow"><span class="gx-tuner__status-dot"></span>{{ loading && !report ? 'CONNECTING' : state.key === 'off' ? 'DISABLED' : choiceLive || live ? 'DEVICE CONNECTED' : 'WAITING FOR DATA' }}</div>
          <h3 role="status">{{ loading && !report ? 'Reading your tuner…' : state.title }}</h3><p>{{ state.description }}</p>
          <p v-if="tracking?.changedParameters && live" class="gx-tuner__current-job"><i class="bi bi-crosshair" aria-hidden="true"></i>{{ tracking.names.map(parameterLabel).join(' · ') }}</p>
        </div>
        <div class="gx-tuner__hero-meta"><span>{{ currentApplied ? 'Controller acknowledged' : live ? 'No correction acknowledged' : 'Current application unknown' }}</span><small>{{ reportAge === null ? 'No learner report yet' : 'Learner report ' + reportAge.toFixed(1) + 's old' }}</small></div>
      </section>
      <GxDialog v-if="manualResetRequested && manualState.pending" labelledby="manual-tune-confirm-title" @close="cancelManualReset"><h3 id="manual-tune-confirm-title">Reset manual tune?</h3><p>{{ manualState.pending.question }}</p><div class="gx-dialog__actions"><button type="button" class="gx-btn gx-btn--tonal" @click="cancelManualReset">Cancel</button><button type="button" class="gx-btn" @click="confirmManualReset">Reset</button></div></GxDialog>
      <nav class="gx-tuner__tabs" role="tablist" aria-label="Tuner views">
        <button v-for="tab in ['overview', 'tune', 'activity', 'plots']" :key="tab" type="button" role="tab" :id="'tuner-tab-' + tab" :aria-controls="'tuner-panel-' + tab" :aria-selected="view === tab" :tabindex="view === tab ? 0 : -1" @click="selectView(tab)" @keydown="tabKey">{{ tab === 'overview' ? 'Overview' : tab === 'tune' ? 'Your tune' : tab === 'activity' ? 'Activity' : 'Live plots' }}<span v-if="tab === 'tune'" class="gx-tuner__count">30</span></button>
      </nav>
      <section v-if="view === 'overview'" id="tuner-panel-overview" role="tabpanel" aria-labelledby="tuner-tab-overview" class="gx-tuner__panel">
        <div class="gx-tuner__metrics"><div class="gx-card gx-tuner__metric"><span>Applied parameters</span><strong>{{ amount(appliedCount) }}<small v-if="appliedCount !== null"> / 30</small></strong><p>{{ appliedCount === null ? 'Requires current controller evidence' : 'Nonzero corrections acknowledged now' }}</p></div>
          <div class="gx-card gx-tuner__metric"><span>Accepted corrections</span><strong>{{ amount(acceptedCount) }}</strong><p>{{ acceptedCount === null ? 'No accepted map reported' : 'Reported accepted map · saving is separate' }}</p></div>
          <div class="gx-card gx-tuner__metric"><span>Steering measurements</span><strong>{{ amount(measurements) }}</strong><p>{{ trackingProgress ? 'Retained observations, before quality checks' : tracking ? 'No capture count reported' : 'Samples retained in the learner report' }}</p></div></div>
        <div class="gx-tuner__overview-grid"><section class="gx-card gx-tuner__section gx-tuner__response"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">STEERING RESPONSE</p><h3>Requested meets measured</h3></div><span class="gx-tuner__pill">{{ trackingPlot ? 'Completed window' : offroad ? 'Recorded drive' : 'Waiting for samples' }}</span></div>
          <template v-if="trackingPlot"><PlotGraph :chart="pairedTrackingChart" title="Requested and measured steering acceleration, paired using the learner's measured delay" /><div class="gx-tuner__legend"><span class="gx-tuner__requested">Requested</span><span class="gx-tuner__measured">Measured</span><small>m/s² · delay aligned</small></div><p class="gx-tuner__caption">{{ trackingPlot.totalEndpoints }} measured endpoints · {{ trackingPlot.age.toFixed(1) }}s since the last pose</p></template>
          <div v-else-if="offroad" class="gx-tuner__empty"><i class="bi bi-activity" aria-hidden="true"></i><h4>{{ amount(offroad.samples) }} recorded steering observations</h4><p>{{ learningReason(offroad.reason) }}</p><p class="gx-tuner__caption">The recorded-drive analysis is separate from the live, delay-paired response graph.</p></div>
          <div v-else class="gx-tuner__empty"><i class="bi bi-activity" aria-hidden="true"></i><h4>Real measurements go here</h4><p>A completed steering window will show the requested response beside what the car actually did.</p></div>
        </section>
        <section class="gx-card gx-tuner__section"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">{{ offroad ? 'RECORDED-DRIVE ANALYSIS' : live ? 'THE CURRENT EXPERIMENT' : 'LAST REPORTED EXPERIMENT' }}</p><h3>{{ offroad ? 'Next-drive tuning' : tracking?.acknowledged && tracking.status === 'trial_active' ? 'Trial in progress' : 'Trial results' }}</h3></div></div>
          <p v-if="tracking">{{ learningReason(tracking.reason) }}</p><p v-else-if="offroad">{{ learningReason(offroad.reason) }}</p><p v-else>Waiting for driving samples. Auto Learn compares steering error before and during a trial.</p>
          <template v-if="offroad"><ul v-if="offroad.suggestions.length"><li v-for="(suggestion, index) in offroad.suggestions" :key="index"><strong>{{ parameterLabel(suggestion.parameter) }} {{ coordinateText(suggestion.delta) }}</strong> · {{ amount(suggestion.windows) }} recorded windows · not approved for automatic saving</li></ul><p v-if="offroad.moreSuggestions" class="gx-tuner__caption">Additional candidates are retained in the device's full assessment.</p><p class="gx-tuner__caption">{{ offroad.configurationState === 'ready_for_next_drive' ? 'A separate qualified tune is saved for the next drive. Current application still requires controller evidence.' : 'Your saved tune is unchanged unless the controller and steering-output checks pass.' }}</p></template>
          <div class="gx-tuner__comparison"><div><span>Before</span><strong>{{ measurementText(tracking?.baselineErrorRms ?? null) }}</strong></div><i class="bi bi-arrow-right" aria-hidden="true"></i><div><span>Trial</span><strong>{{ measurementText(tracking?.futureErrorRms ?? null) }}</strong></div></div><p class="gx-tuner__caption">Tracking error RMS · m/s² · lower is better</p>
          <p v-if="improvement !== null" class="gx-tuner__observed">{{ Math.abs(improvement).toFixed(1) }}% {{ improvement >= 0 ? 'lower' : 'higher' }} observed error<small>{{ state.key === 'confirmed' ? 'Comparison passed; controller acknowledged' : 'Observation so far, not a confirmed improvement' }}</small></p>
          <div v-if="quota" class="gx-tuner__quota"><div><span>Comparable trial windows</span><strong>{{ quota.completed }} / {{ quota.required }}</strong></div><div class="gx-tuner__progress" role="progressbar" aria-label="Comparable trial windows" :aria-valuenow="quota.completed" :aria-valuemax="quota.required" aria-valuemin="0"><span :style="{ width: quota.percent + '%' }"></span></div></div>
          <p class="gx-tuner__caption">Changes that make steering worse are rolled back. Accepted and saved values are shown separately.</p>
        </section></div>
      </section>
      <section v-if="view === 'tune'" id="tuner-panel-tune" role="tabpanel" aria-labelledby="tuner-tab-tune" class="gx-tuner__panel">
        <div class="gx-tuner__section-heading"><div><h3>Your tune</h3><p>Thirty steering parameters, grouped by behavior.</p></div><span class="gx-tuner__pill">Manual + Auto Learn</span></div>
        <div class="gx-tuner__tune-layout"><nav class="gx-tuner__groups" aria-label="Steering parameter groups"><button v-for="item in groups" :key="item.id" type="button" :aria-pressed="group === item.id" @click="group = item.id"><i :class="['bi', item.icon]" aria-hidden="true"></i><span>{{ item.label }}<small>{{ item.names.length }} parameters · {{ groupChanges(item) }}</small></span><i class="bi bi-chevron-right" aria-hidden="true"></i></button></nav>
          <section class="gx-card gx-tuner__section gx-tuner__parameters"><div class="gx-tuner__section-heading"><div><h3>{{ selectedGroup.label }}</h3><p>{{ selectedGroup.description }}</p></div></div>
            <section class="gx-tuner__manual" aria-label="Manual tuning"><h4>Manual tune</h4><p class="gx-tuner__caption">Parked edits apply to the next drive. Auto Learn pauses while manual overrides are set. Zero removes an override.</p>
              <GxNotice v-if="manualState.error" tone="danger">{{ manualState.error }}</GxNotice>
              <p v-if="!manualState.data" class="gx-tuner__caption">{{ manualState.status === 'loading' ? 'Loading saved manual tune…' : 'Manual tuning unavailable. Reconnecting…' }}</p>
              <p v-else-if="!manualRows.length" class="gx-tuner__caption">{{ manualState.data.rows[0]?.reason || 'Manual tuning is unavailable for this controller.' }}</p>
              <GalaxySettingRow v-for="{ row, index } in manualRows" :key="row.coordinate" :row="{ ...row, label: parameterLabel(row.coordinate), reason: row.available ? '' : row.reason }" :index="index" :busy="manualBusy" :disabled="!!manualState.error" :save-value="saveManual" @reset-default="index => manualFeed.resetDefault(index)" />
              <GalaxySettingRow v-for="{ row, index } in manualActions" :key="index" :row="row" :index="index" :busy="manualBusy" :disabled="!!manualState.error" :save-value="saveManual" @review="reviewManualReset" />
            </section>
            <h4>Auto Learn evidence</h4><p class="gx-tuner__caption">These reported results are separate from your manual settings.</p>
            <div class="gx-tuner__parameter-head" aria-hidden="true"><span>Parameter</span><span>{{ trackingValues ? 'Accepted' : 'Proposal' }}</span><span>Trial</span><span>Applied now</span></div>
            <div v-for="row in groupRows" :key="row.name" class="gx-tuner__parameter"><div><h4>{{ parameterLabel(row.name) }}</h4><small v-if="!row.pidSupported">Torque only · PID unsupported</small></div><div><span class="gx-tuner__mobile-label">{{ trackingValues ? 'Accepted' : 'Proposal' }}</span><strong>{{ coordinateText(trackingValues ? row.accepted : row.learned) }}</strong></div><div><span class="gx-tuner__mobile-label">Trial</span><strong :class="{ 'gx-tuner__pending': row.pending !== null && row.pending !== row.accepted }">{{ coordinateText(row.pending) }}</strong></div><div><span class="gx-tuner__mobile-label">Applied now</span><strong :class="{ 'gx-tuner__applied': row.applied !== null && row.applied !== 0 }">{{ coordinateText(row.applied) }}</strong></div></div>
            <details class="gx-tuner__disclosure"><summary>How to read these values</summary><p>Zero is the original baseline. These are correction coordinates, not raw degrees or torque. Speed-window coordinates scale the original speed range; deadband and angle assistance use the controller's physical normalizer. A trial is an offer, not proof of application. All 30 stages exist for torque; 26 exist for torque-actuated PID.</p></details>
          </section></div>
      </section>
      <section v-show="view === 'plots'" id="tuner-panel-plots" role="tabpanel" aria-labelledby="tuner-tab-plots" class="gx-tuner__panel"><PlotsPage :mode="mode" :unauthorized="unauthorized" :active="view === 'plots'" embedded /></section>
      <section v-if="view === 'activity'" id="tuner-panel-activity" role="tabpanel" aria-labelledby="tuner-tab-activity" class="gx-tuner__panel"><section class="gx-card gx-tuner__section"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">LEARNING ACTIVITY</p><h3>What the tuner found</h3></div><span class="gx-tuner__pill">Reported observations</span></div>
          <ol v-if="historyRows.length" class="gx-tuner__timeline"><li v-for="(row, index) in historyRows" :key="index"><span class="gx-tuner__timeline-dot" :class="{ 'gx-tuner__timeline-dot--applied': row.applied }"></span><div><strong>{{ historyTitle(row.status) }}</strong><p>{{ learningReason(row.reason) }}</p><small>{{ row.time }}<template v-if="row.applied"> · Applied at that time</template></small></div></li></ol>
          <div v-else class="gx-tuner__empty"><i class="bi bi-clock-history" aria-hidden="true"></i><h4>No findings reported yet</h4><p>This will show real collection, trial and rollback events as the learner reports them.</p></div>
          <button v-if="(report?.history.length || 0) > 6" type="button" class="gx-btn gx-btn--tonal" @click="showAllHistory = !showAllHistory">{{ showAllHistory ? 'Show recent activity' : 'Show all reported activity' }}</button>
        </section>
        <details class="gx-card gx-tuner__section gx-tuner__disclosure"><summary>Diagnostics &amp; source details</summary><dl class="gx-tuner__diagnostics"><template v-if="tracking"><dt>Live learner state</dt><dd>{{ tracking.status }}</dd><dt>Live learner reason</dt><dd>{{ tracking.reason }}</dd></template><template v-if="trackingProgress"><dt>Paired inputs retained</dt><dd>{{ amount(trackingProgress.readyPairs) }}</dd></template><dt>Legacy collector state</dt><dd>{{ readable(current?.status) }}</dd><dt>Legacy collector reason</dt><dd>{{ readable(current?.reason) }}</dd><dt>Legacy retained blocks</dt><dd>{{ amount(current?.progress?.blocks) }}</dd><template v-if="current?.fit"><dt>Latest legacy fit</dt><dd>{{ readable(current.fit.status) }} · {{ readable(current.fit.reason) }}</dd><dt>Training / held-out windows</dt><dd>{{ amount(current.fit.trainWindows) }} / {{ amount(current.fit.heldoutWindows) }}</dd></template><template v-if="losses"><dt>Baseline / candidate loss</dt><dd>{{ losses.baseline.toPrecision(5) }} / {{ losses.learned.toPrecision(5) }}</dd></template><template v-for="[key, value] in details" :key="key"><dt>{{ detailLabel(key) }}</dt><dd>{{ detailText(value) }}</dd></template></dl>
          <template v-if="trackingPlot"><h4>Delay-paired error</h4><PlotGraph :chart="pairedErrorChart" title="Delay-paired steering error" /><p class="gx-tuner__caption">Episode {{ trackingPlot.episodeId }} · {{ trackingPlot.sampled ? 'Selected' : 'All' }} original endpoints. No interpolation.</p><p class="gx-tuner__caption">Control / applied output / pose: {{ sourceSeconds(trackingPlot.points.at(-1).controlMonoTime) }} / {{ sourceSeconds(trackingPlot.points.at(-1).applyMonoTime) }} / {{ sourceSeconds(trackingPlot.points.at(-1).poseMonoTime) }} monotonic seconds.</p></template>
        </details>
      </section>
    </template>
  </div>`,
}
