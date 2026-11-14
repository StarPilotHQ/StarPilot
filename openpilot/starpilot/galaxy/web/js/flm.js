import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { GxDialog } from "./dialog.js"
import { SettingsFeed, settingsBusy } from "./settings.js"
import { GalaxySettingRow, settingControl } from "./galaxy-setting-row.js"
import { PollTimer, connectionError } from "./polling.js"
import { requestJson } from "./startup.js"
import { bluetoothConnection } from "./bluetooth-notice.js"
import { PlotGraph, PlotsPage, chartGeometry } from "./plots.js"
import { validTrainingResults, readable, detailText, surfaceRows, coordinateText, trackingView, measurementText, validTrackingValues, trackingPlotView, sourceSeconds, validLearningPreference, SURFACE_GROUPS, tunerStatus, learningReason, sourceCaptureCounts, historyTitle, offroadView, FRICTION_NAMES, offroadCoordinateEvidence, recommendationText } from "./flm-training-results.js"

import { reviewModel, dialPercent, ideaStatus, ideaExplanation, ideaValue, ideaHeading, curvePointLabel, ideaDetails } from "./flm-review.js"

const VIEWS = ["overview", "tune", "activity", "plots"]
const GROUP_DESCRIPTIONS = {
  balance: 'How steering response differs between left and right curves.',
  center: 'Small steering corrections while travelling straight.',
  low: 'Steering support in tight, slower turns.',
  entry: 'How steering starts a turn.',
  exit: 'How steering settles as a turn ends.',
  speed: 'Choose the speed ranges for changes at the start and end of a curve.',
}
const PARAMETER_LABELS = {
  friction_threshold_crawl: "Crawl friction threshold", friction_threshold_low: "Low-speed friction threshold",
  friction_threshold_mid: "Mid-speed friction threshold", friction_threshold_fast: "Fast friction threshold", friction_threshold_highway: "Highway friction threshold",
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
  data: () => ({ report: null, error: "", loading: false, request: null, receivedAt: 0, now: 0, bluetooth: bluetoothConnection(),
    manualState: { status: "idle", data: null, pending: null, error: "" }, manualResetRequested: false, manualWrite: null,
    headerOffset: 88, view: "overview", group: "balance", showAllHistory: false, groups: SURFACE_GROUPS }),
  watch: {
    initialView(view) { this.selectView(view) }, mode() { this.syncManualControls() },
    'manualState.error'(error, previous) {
      if (error) this.$nextTick(() => {
        const notice = this.$el.querySelector('.gx-tuner__manual-error')
        this.revealSection(notice, true)
      })
      else if (previous && this.manualWrite?.status === 'checked') this.$nextTick(() => this.revealSection(this.$el.querySelector('.gx-tuner__manual-readback'), true))
    },
  },
  mounted() {
    this.activePage = true
    const appbar = this.$el?.closest?.('.gx-app')?.querySelector('.gx-appbar')
    if (appbar) {
      const measure = () => { this.headerOffset = Math.ceil(appbar.getBoundingClientRect().bottom) + 8 }
      measure()
      if (typeof ResizeObserver !== 'undefined') { this.headerObserver = new ResizeObserver(measure); this.headerObserver.observe(appbar) }
    }
    this.manualFeed = new SettingsFeed({ publish: update => this.publishManual(update), unauthorized: this.unauthorized })
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
    this.headerObserver?.disconnect()
    this.activePage = false; this.manualFeed?.stop(); clearTimeout(this.freshnessTimer); this.poller?.stop(); this.request?.abort(); this.request = null
    document.removeEventListener("visibilitychange", this.visibility)
  },
  computed: {
    manualRows() { return (this.manualState.data?.rows || []).map((row, index) => ({ row, index })).filter(({ row }) => this.selectedGroup.names.includes(row.coordinate)) },
    manualActions() { return (this.manualState.data?.rows || []).map((row, index) => ({ row, index })).filter(({ row }) => row.confirm) },
    manualBusy() { return settingsBusy(this.manualState) },
    manualErrorText() { return this.manualWrite?.status === 'unconfirmed' ? 'Could not confirm this change was saved. Reconnecting to check the saved values.' : this.manualState.error },
    manualReadbackText() {
      if (this.manualWrite?.status !== 'checked') return ''
      const { requests, values } = this.manualWrite
      if (requests.length === 1) {
        const [name, target, unit] = requests[0], value = values[0]
        return value === target ? `${this.parameterLabel(name)}: saved value confirmed at ${value}${unit || ''}.` :
          `${this.parameterLabel(name)}: the saved value is ${value}${unit || ''}. The requested ${target}${unit || ''} is not the current saved value.`
      }
      return values.every((value, i) => value === requests[i][1]) ? 'Reset confirmed: your manual overrides are cleared.' :
        'Saved values checked. Some manual overrides are still set; the reset is not confirmed.'
    },
    connectionLabel() {
      if (this.loading && !this.report) return 'Connecting…'
      if (!this.choiceLive && !this.live) return this.state.key === 'offline' ? 'Connection interrupted' : 'Waiting for device'
      return this.preference?.valid ? 'Connected · Auto Learn ' + (this.preference.enabled ? 'on' : 'off') : 'Connected'
    },

    connectionTone() { return this.error || !this.choiceLive ? 'muted' : this.preference?.enabled ? 'ready' : 'muted' },
    current() { return this.report?.current },
    preference() { return validLearningPreference(this.report?.preference) ? this.report.preference : null },
    choiceLive() { return !this.error && !!this.preference && Math.max(0, this.now - this.receivedAt) <= 2000 },
    reportFresh() { return !this.error && !!this.current?.fresh && this.current.ageSeconds + Math.max(0, this.now - this.receivedAt) / 1000 <= 2 },
    live() { return this.reportFresh &&
      !(this.choiceLive && (this.preference.enabled === false || this.preference.parked === true)) },
    state() { return tunerStatus(this.report, this.live, this.choiceLive, this.error, this.reportFresh) },
    heroTitle() { if (!this.current && !this.error) return 'Waiting for a drive report'; if (this.state.key === 'next-drive') return 'Next-drive tune is ready'; return this.state.key === 'analyzed' ? 'Drive review ready' : this.state.title },
    heroDescription() { if (!this.current && !this.error) return 'When the device reports steering measurements, your drive review will appear here.'; if (this.state.key === 'next-drive') return 'Saved for your next drive. New driving is still needed to judge the result.'; return this.state.key === 'analyzed' ? 'Review what the drive found below.' :
      this.state.key === 'fitting' ? 'Checking your completed drive. Earlier findings stay available while this runs.' : this.state.description },
    vehicle() { return this.preference?.vehicle ? this.preference.vehicle.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : "Your steering, measured" },
    currentApplied() { return this.live && this.current?.enabled === true && this.current?.application?.applied === true },
    rows() { return surfaceRows(this.report, this.live) },
    tracking() { return trackingView(this.report, this.live) },
    trackingValues() { return validTrackingValues(this.current?.trackingValues) ? this.current.trackingValues : null },
    trackingProgress() { return sourceCaptureCounts(this.report) },
    measurements() { return this.tracking ? this.trackingProgress?.capturedPoses ?? null : this.current?.progress?.samples ?? null },
    trackingPlot() { return trackingPlotView(this.report, this.now - this.receivedAt) },
    offroad() { return offroadView(this.report) },
    review() {
      const result = reviewModel(this.offroad, this.offroad?.retained, this.offroad?.retained)
      if (!this.offroad && typeof this.current?.details?.['Completed drive analyzed'] === 'string') {
        const otherVehicle = this.preference?.vehicle && this.report?.vehicle && this.preference.vehicle !== this.report.vehicle
        return { ...result, title: otherVehicle ? 'This report is for another vehicle' : 'The drive report is incomplete',
          description: otherVehicle ? 'These findings do not match your current vehicle. Waiting for its own completed-drive report.' :
            'An analysis was reported, but it is missing information needed for a complete drive review. Waiting for the full report.' }
      }
      if (this.current?.status === 'unavailable') return { ...result, title: 'Drive review could not finish', tone: 'neutral',
        description: learningReason(this.current.reason), next: 'This is an analysis problem, not a report that your saved tune was erased. Download FLM logs in Activity for details.' }
      if (!this.offroad && this.current) return { ...result, title: this.state.title, description: this.state.description }
      return result
    },
    savedSummary() {
      if (this.offroad?.saved && !['ready_for_next_drive', 'accepted', 'accepted_after_drive', 'rolled_back'].includes(this.offroad.configurationState)) return 'Saved state not confirmed'
      return this.acceptedCount === null ? 'Saved count not reported' : this.amount(this.acceptedCount) + ' saved'
    },
    savedAdjustments() { return Object.entries(this.offroad?.savedValues || {}).filter(([, value]) => value !== 0).map(([name, value]) => ({ name, value })) },
    frictionRecommendations() { return (this.offroad?.suggestions || []).filter(row => FRICTION_NAMES.includes(row.parameter)) },
    savedFriction() { return this.savedAdjustments.filter(row => FRICTION_NAMES.includes(row.name)) },
    recordedTrackingChart() { return chartGeometry(this.offroad?.observed?.points || [], ['desired', 'actual']) },
    pairedTrackingChart() { return chartGeometry(this.trackingPlot?.points || [], ["desiredForceAccel", "measuredForceAccel"]) },
    pairedErrorChart() { return chartGeometry(this.trackingPlot?.points || [], ["errorForceAccel"]) },
    details() { return Object.entries(this.current?.details || {}).filter(([key]) => !['Recorded coordinate recommendations', 'Recorded route suggestions', 'Recorded suggestion count', 'More recorded suggestions omitted'].includes(key)) },
    reportAge() { return this.current ? this.current.ageSeconds + Math.max(0, this.now - this.receivedAt) / 1000 : null },
    appliedCount() { return this.currentApplied && this.rows.every(row => row.applied !== null) ? this.rows.filter(row => row.applied !== 0).length : null },
    acceptedCount() { return this.offroad ? this.offroad.savedCount : this.trackingValues ? this.rows.filter(row => row.accepted !== null && row.accepted !== 0).length : null },
    groupRows() { return this.rows.filter(row => this.selectedGroup.names.includes(row.name)) },
    navigationGroups() { return [...this.groups.map(group => ({ ...group, description: GROUP_DESCRIPTIONS[group.id] || group.description })), { id: 'friction', label: 'Small corrections by speed', icon: 'bi-bezier2',
      description: 'Five speed settings managed by Auto Learn, separate from the manual sliders.', names: FRICTION_NAMES }] },
    selectedGroup() { return this.navigationGroups.find(group => group.id === this.group) || this.groups[0] },
    frictionRows() { return FRICTION_NAMES.map(name => ({ name, saved: this.offroad?.savedValues?.[name] ?? null,
      ideas: this.review.curve.filter(row => row.parameter === name) })) },
    recordedDuration() { const points = this.offroad?.observed?.points; return points?.length > 1 ? ((points.at(-1).at - points[0].at) / 1000).toFixed(1) : null },
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
    dialPercent, ideaStatus, ideaValue, ideaHeading, curvePointLabel, ideaDetails, readable, detailText, coordinateText, measurementText, sourceSeconds, learningReason, historyTitle, recommendationText,
    ideaExplanation(idea) { return idea.reason?.startsWith('observed_') ? learningReason(idea.reason) : ideaExplanation(idea) },
    manualEvidence(name, unit) { return offroadCoordinateEvidence(this.report, name, this.live, unit) },
    beginManualWrite(requests) {
      const valid = requests.filter(([name, value]) => typeof name === 'string' && Number.isFinite(value))
      this.manualWrite = valid.length ? { requests: valid, status: 'saving', values: null } : null
    },
    publishManual(update) {
      const previous = this.manualState.data
      if (this.manualWrite?.status === 'saving' && update.error) this.manualWrite.status = 'unconfirmed'
      Object.assign(this.manualState, update)
      if (this.manualWrite && ['saving', 'unconfirmed', 'checked'].includes(this.manualWrite.status) && update.status === 'ready' &&
          !update.error && update.data && update.data !== previous) {
        const values = this.manualWrite.requests.map(([name, , unit]) => {
          const row = update.data.rows?.find(row => row.coordinate === name)
          return row && (row.unit || '') === (unit || '') && row.value !== null && row.value !== undefined && row.value !== '' && Number.isFinite(Number(row.value)) ? Number(row.value) : null
        })
        this.manualWrite = { ...this.manualWrite, status: values.every(value => value !== null) ? 'checked' : 'unconfirmed', values }
      }
    },
    resetManualDefault(index) {
      const row = this.manualState.data?.rows?.[index]
      this.beginManualWrite(row?.coordinate ? [[row.coordinate, Number(row.defaultValue), row.unit]] : [])
      return this.manualFeed.resetDefault(index)
    },

    detailLabel(key) { return ['Fit attempts', 'Fit results', 'Fit in progress', 'Submitted fit jobs (reporter lifetime)'].includes(key) ? 'Legacy fitter · ' + key : readable(key) },
    amount(value) { return value === null || value === undefined ? "—" : value.toLocaleString() },
    parameterLabel(name) { return PARAMETER_LABELS[name] || readable(name) },
    groupChanges(group) {
      if (this.offroad) {
        const saved = this.review.saved.filter(row => group.names.includes(row.parameter)).length
        const suggested = this.review.findings.filter(row => group.names.includes(row.parameter)).length
        return [saved ? `${saved} saved` : '', suggested ? `${suggested} ${suggested === 1 ? 'finding' : 'findings'}` : ''].filter(Boolean).join(' · ') || 'No new findings'
      }
      if (!this.trackingValues) return "Not reported"
      const count = this.rows.filter(row => group.names.includes(row.name) && row.accepted !== null && row.accepted !== 0).length
      return count ? `${count} accepted` : "Baseline"
    },
    ideaHelp(name) {
      const help = this.manualState.data?.rows?.find(row => row.coordinate === name)?.reason
      return help ? help.split('. ')[0] + (help.includes('. ') ? '.' : '') : 'Review what this setting changes before making a manual adjustment.'
    },
    groupHasIdeas(group) { return [...this.review.manual, ...this.review.curve].some(row => group.names.includes(row.parameter)) },
    dialIdeas(name) { return this.review.manual.filter(row => row.parameter === name) || [] },
    dialState(name) { return this.rows.find(row => row.name === name) },
    async reviewSetting(name) {
      this.selectView('tune')
      const group = this.navigationGroups.find(group => group.names.includes(name))
      if (group) this.group = group.id
      await this.$nextTick()
      const target = document.getElementById(FRICTION_NAMES.includes(name) ? 'tuner-friction' : 'tuner-dial-' + name)
      this.revealSection(target, true)
    },
    revealSection(target, focus = false) {
      if (!target) return
      target.scrollIntoView({ block: 'start', behavior: 'instant' })
      const tabs = this.$el.querySelector('.gx-tuner__tabs')
      if (tabs) window.scrollBy({ top: target.getBoundingClientRect().top - tabs.getBoundingClientRect().bottom - 16, behavior: 'instant' })
      if (focus) target.focus({ preventScroll: true })
    },
    async reviewGroup(id) {
      if (!this.navigationGroups.some(group => group.id === id)) return
      this.group = id
      await this.$nextTick()
      this.revealSection(this.$el.querySelector('.gx-tuner__parameters'), true)
    },
    selectView(view) {
      if (!VIEWS.includes(view)) return
      const changed = this.view !== view
      this.view = view
      const controls = this.syncManualControls()
      if (changed) this.$nextTick(() => {
        this.revealSection(document.getElementById('tuner-panel-' + view))
      })
      return controls
    },
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
    async confirmManualReset() { this.beginManualWrite((this.manualState.data?.rows || []).filter(row => row.coordinate).map(row => [row.coordinate, 0, row.unit])); await this.manualFeed.confirm(); this.manualResetRequested = false },
    async saveManual(index, value) {
      const row = this.manualState.data?.rows?.[index]
      if (this.view !== "tune" || !this.manualFeed?.active || this.manualBusy || this.manualState.error ||
          !this.selectedGroup.names.includes(row?.coordinate) || !row.available || !row.action || settingControl(row) !== "slider") return false
      this.beginManualWrite([[row.coordinate, value, row.unit]])
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
  template: `<div class="gx-view gx-tuner" :style="{ '--tuner-header-offset': headerOffset + 'px' }">
    <header class="gx-page-header gx-settings__header gx-tuner__heading"><div><h2>Your steering</h2><p>{{ vehicle }}</p></div></header>
    <GxState v-if="mode !== 'local'">Connect to your device to see its steering tune.</GxState>
    <template v-else>
      <GxNotice v-if="error && view !== 'plots'" tone="danger">{{ error }}</GxNotice>
      <div v-if="view !== 'plots'" class="gx-tuner__connection" role="status"><span class="gx-tuner__status-dot" :data-tone="connectionTone" aria-hidden="true"></span><span>{{ connectionLabel }}</span><span class="gx-tuner__connection-state">{{ currentApplied ? 'Learned tune in use' : preference?.parked ? 'Parked' : live ? 'Driving · use not confirmed' : 'State not confirmed' }}</span></div>
      <GxDialog v-if="manualResetRequested && manualState.pending" labelledby="manual-tune-confirm-title" @close="cancelManualReset"><h3 id="manual-tune-confirm-title">Reset manual tune?</h3><p>{{ manualState.pending.question }}</p><div class="gx-dialog__actions"><button type="button" class="gx-btn gx-btn--tonal" @click="cancelManualReset">Cancel</button><button type="button" class="gx-btn" @click="confirmManualReset">Reset</button></div></GxDialog>
      <nav class="gx-tuner__tabs" role="tablist" aria-label="Tuner views">
        <button v-for="tab in ['overview', 'tune', 'activity', 'plots']" :key="tab" type="button" role="tab" :id="'tuner-tab-' + tab" :aria-controls="'tuner-panel-' + tab" :aria-selected="view === tab" :tabindex="view === tab ? 0 : -1" @click="selectView(tab)" @keydown="tabKey">{{ tab === 'overview' ? 'Overview' : tab === 'tune' ? 'Your tune' : tab === 'activity' ? 'Activity' : 'Live plots' }}</button>
      </nav>
      <section v-if="view === 'overview'" id="tuner-panel-overview" role="tabpanel" aria-labelledby="tuner-tab-overview" class="gx-tuner__panel">
        <section class="gx-card gx-tuner__drive" :data-tone="review.tone" aria-label="Drive review">
          <div class="gx-tuner__drive-mark" aria-hidden="true"><i class="bi" :class="review.tone === 'saved' ? 'bi-check2-circle' : review.count ? 'bi-lightbulb' : 'bi-steering-wheel'"></i></div>
          <div class="gx-tuner__drive-copy"><p class="gx-tuner__eyebrow">COMPLETED DRIVE REVIEW</p><h3>{{ review.title }}</h3><p>{{ review.description }}</p><p class="gx-tuner__summary"><template v-if="current?.status === 'unavailable'">Latest review incomplete · </template><template v-else><template v-if="offroad?.evidenceDrives > 1">{{ offroad.evidenceDrives }} drives in your steering history · </template>{{ review.count }} {{ review.count === 1 ? 'finding' : 'findings' }} · </template><template v-if="offroad">{{ savedSummary }}</template><template v-else-if="trackingValues">{{ amount(acceptedCount) }} accepted · saving not confirmed</template><template v-else>Saved tune not reported</template></p><p v-if="offroad?.earlierDrive" class="gx-tuner__caption" data-role="retained-drive-findings">These suggestions come from an earlier drive. Newer driving is reviewed separately.</p></div>
          <div class="gx-tuner__drive-actions"><button v-if="review.saved.length" type="button" class="gx-btn gx-btn--tonal" @click="selectView('tune')">{{ review.historical ? 'View earlier saved values' : 'View saved change' }}<i class="bi bi-arrow-right" aria-hidden="true"></i></button><button v-if="review.count" type="button" class="gx-btn gx-btn--tonal gx-tuner__review-button" data-action="view-findings" @click="selectView('tune')">{{ review.saved.length ? 'View other findings' : 'View ' + review.count + (review.count === 1 ? ' finding' : ' findings') }}<i class="bi bi-arrow-right" aria-hidden="true"></i></button></div>
        </section>
        <div class="gx-tuner__overview-grid"><section class="gx-card gx-tuner__section gx-tuner__response"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">STEERING RESPONSE</p><h3>How your car followed the steering request</h3></div><span class="gx-tuner__pill">{{ trackingPlot ? 'Completed window' : offroad?.observed ? 'One recorded section · ' + recordedDuration + 's' : offroad ? 'Recorded drive' : 'Waiting for samples' }}</span></div>
          <p class="gx-tuner__graph-explainer">When these lines are close, the car is following the steering request closely in this sample.</p>
          <template v-if="trackingPlot"><PlotGraph :chart="pairedTrackingChart" title="Requested and measured steering acceleration, paired using the learner's measured delay" /><div class="gx-tuner__legend"><span class="gx-tuner__requested">What StarPilot asked for</span><span class="gx-tuner__measured">What the car did</span><small>m/s² · delay aligned</small></div><p class="gx-tuner__caption">{{ trackingPlot.totalEndpoints }} measured endpoints · {{ trackingPlot.age.toFixed(1) }}s since the last pose</p></template>
          <template v-else-if="offroad?.observed"><PlotGraph :chart="recordedTrackingChart" title="Recorded requested and measured steering" start-label="Start · 0s" :end-label="'End · ' + recordedDuration + 's'" /><div class="gx-tuner__legend"><span class="gx-tuner__requested">What StarPilot asked for</span><span class="gx-tuner__measured">What the car did</span></div><p class="gx-tuner__caption">One short recorded section, not a full-drive score or a before-and-after comparison.</p><details class="gx-tuner__disclosure"><summary>About this graph</summary><p>{{ offroad.observed.units }} · original source times. Drive {{ offroad.observed.route }} · segment {{ offroad.observed.segment }} · one input-ordered recorded window. No interpolation or delay alignment. Baseline statistics use {{ amount(offroad.observed.windows) }} windows.</p></details></template>
          <div v-else-if="offroad" class="gx-tuner__empty"><i class="bi bi-activity" aria-hidden="true"></i><h4>{{ amount(offroad.samples) }} recorded steering observations</h4><p>{{ learningReason(offroad.reason) }}</p><p class="gx-tuner__caption">No recorded response series was included in this report.</p></div>
          <div v-else class="gx-tuner__empty"><i class="bi bi-activity" aria-hidden="true"></i><h4>No steering recording yet</h4><p>Once the device reports steering measurements, the request and the car response will appear here.</p></div>
        </section>
        <section class="gx-card gx-tuner__section"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">{{ offroad ? 'RECORDED-DRIVE ANALYSIS' : live ? 'THE CURRENT EXPERIMENT' : 'LAST REPORTED EXPERIMENT' }}</p><h3>{{ offroad ? 'What happens next' : tracking?.acknowledged && tracking.status === 'trial_active' ? 'Trial in progress' : 'Trial results' }}</h3></div></div>
          <p v-if="tracking">{{ learningReason(tracking.reason) }}</p><template v-else-if="offroad"><p>{{ review.next }}</p><p v-if="review.count" class="gx-tuner__caption">{{ review.manual.length }} steering {{ review.manual.length === 1 ? 'finding' : 'findings' }} · {{ review.curve.length }} speed-range {{ review.curve.length === 1 ? 'finding' : 'findings' }}<template v-if="review.other.length"> · {{ review.other.length }} earlier grouped findings</template>. Find both in Your tune.</p><button v-if="review.count" type="button" class="gx-btn gx-btn--tonal" @click="selectView('tune')">Review the findings</button></template><p v-else>After active steering has been recorded, your drive review will appear here.</p>
          <details v-if="offroad" class="gx-tuner__disclosure"><summary>Analysis details</summary><p>{{ learningReason(offroad.reason) }}</p><p v-if="offroad.checks">{{ offroad.checks.passed }} / {{ offroad.checks.required }} original controller and encoder windows passed.</p><p v-for="(refusal, index) in offroad.checks?.refusals || []" :key="index">{{ refusal.count }} window(s): {{ learningReason(refusal.reason) }}</p><p>{{ amount(offroad.samples) }} recorded steering observations.</p><p v-if="offroad.moreSuggestions">Additional candidates are retained in the device's full assessment.</p></details>
          <details class="gx-tuner__disclosure" :open="!offroad"><summary>Steering comparison details</summary><div class="gx-tuner__comparison"><div><span>{{ offroad ? 'Recorded baseline' : 'Before' }}</span><strong>{{ measurementText(offroad?.observed?.errorRms ?? tracking?.baselineErrorRms ?? null) }}</strong></div><i class="bi bi-arrow-right" aria-hidden="true"></i><div><span>Trial</span><strong>{{ offroad ? offroad.configurationState === 'ready_for_next_drive' ? 'Awaiting next-drive measurement' : 'No pending trial reported' : measurementText(tracking?.futureErrorRms ?? null) }}</strong></div></div><p v-if="offroad?.observed" class="gx-tuner__caption">Uses {{ amount(offroad.observed.windows) }} recorded windows, not just the section in the graph.</p><p class="gx-tuner__caption">Tracking error RMS · {{ offroad?.observed?.units ?? 'm/s²' }} · lower is better</p>
          <p v-if="improvement !== null" class="gx-tuner__observed">{{ Math.abs(improvement).toFixed(1) }}% {{ improvement >= 0 ? 'lower' : 'higher' }} observed error<small>{{ state.key === 'confirmed' ? 'Comparison passed; controller acknowledged' : 'Observation so far, not a confirmed improvement' }}</small></p>
          <div v-if="quota" class="gx-tuner__quota"><div><span>Comparable trial windows</span><strong>{{ quota.completed }} / {{ quota.required }}</strong></div><div class="gx-tuner__progress" role="progressbar" aria-label="Comparable trial windows" :aria-valuenow="quota.completed" :aria-valuemax="quota.required" aria-valuemin="0"><span :style="{ width: quota.percent + '%' }"></span></div></div>
          <p class="gx-tuner__caption">Changes that make steering worse are rolled back. Accepted and saved values are shown separately.</p></details>
        </section></div>
      </section>
      <section v-if="view === 'tune'" id="tuner-panel-tune" role="tabpanel" aria-labelledby="tuner-tab-tune" class="gx-tuner__panel">
        <div v-if="!offroad" class="gx-tuner__section-heading"><div><h3>Make sense of your tune</h3><p>Review what Auto Learn found, or adjust a setting yourself.</p></div></div>
        <section v-if="offroad" class="gx-card gx-tuner__findings" :class="{ 'gx-tuner__findings--paired': review.manualGroups.length === 1 && review.curve.length > 0 }" aria-label="Drive findings"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">SUGGESTIONS FROM COMPLETED DRIVING</p><h3>{{ review.title }}</h3><p>{{ review.description }}</p></div><span class="gx-tuner__badge" :data-tone="review.tone">{{ review.tone === 'saved' ? 'Saved tune' : review.count ? 'Review only' : 'No new change' }}</span></div>
          <div v-if="review.saved.length" class="gx-tuner__saved-list"><h4>{{ review.historical ? 'Earlier saved Auto Learn values' : offroad.configurationState === 'ready_for_next_drive' ? 'Saved for the next drive' : 'Saved Auto Learn values' }}</h4><div v-for="row in review.saved" :key="row.parameter"><span>{{ parameterLabel(row.parameter) }}</span><strong>{{ dialPercent(row.value) }}</strong></div><p>Saved does not mean applied now. The controller confirms use while driving.</p></div>
          <p v-if="review.count" class="gx-tuner__caption">{{ review.manual.length }} steering {{ review.manual.length === 1 ? 'finding' : 'findings' }} · {{ review.curve.length }} speed-range {{ review.curve.length === 1 ? 'finding' : 'findings' }}<template v-if="review.other.length"> · {{ review.other.length }} earlier grouped findings</template>. Viewing an idea does not change your tune.</p>
          <div v-if="review.manual.length" class="gx-tuner__idea-grid"><template v-for="area in review.manualGroups" :key="area.id"><template v-if="area.findings.length === 1"><article v-for="idea in area.findings" :key="idea.parameter" class="gx-tuner__idea" :data-coordinate="idea.parameter"><h4>{{ ideaHeading(idea.parameter) }}</h4><span class="gx-tuner__badge" data-tone="review">{{ idea.target === null ? 'Conflicting findings' : 'Not saved' }}</span><p>{{ ideaExplanation(idea) }}</p><button type="button" class="gx-btn gx-btn--tonal" @click="reviewSetting(idea.parameter)">View setting<i class="bi bi-arrow-right" aria-hidden="true"></i></button><details class="gx-tuner__disclosure"><summary>Evidence &amp; exact values</summary><p>{{ parameterLabel(idea.parameter) }}. {{ ideaHelp(idea.parameter) }}</p><dl class="gx-tuner__exact-values"><template v-for="value in ideaDetails(idea)" :key="value.label"><dt>{{ value.label }}</dt><dd>{{ value.value }}</dd></template></dl><p>{{ amount(idea.windows) }} recorded windows.</p><p>{{ learningReason(idea.reason) }}</p></details></article></template><article v-else class="gx-tuner__idea"><span class="gx-tuner__badge" data-tone="review">{{ area.findings.length }} steering findings · not saved</span><h4>{{ area.label }}</h4><p>Several settings in this area were flagged for review. No manual value has been changed.</p><details class="gx-tuner__disclosure"><summary>Review {{ area.findings.length }} findings</summary><section v-for="idea in area.findings" :key="idea.parameter" class="gx-tuner__grouped-idea" :data-coordinate="idea.parameter"><h4>{{ parameterLabel(idea.parameter) }}</h4><p>{{ ideaExplanation(idea) }}</p><span class="gx-tuner__badge" data-tone="review">{{ ideaStatus(idea) }}</span><button type="button" class="gx-btn gx-btn--tonal" @click="reviewSetting(idea.parameter)">View setting</button><details><summary>Evidence &amp; exact values</summary><p>{{ ideaHelp(idea.parameter) }}</p><dl class="gx-tuner__exact-values"><template v-for="value in ideaDetails(idea)" :key="value.label"><dt>{{ value.label }}</dt><dd>{{ value.value }}</dd></template></dl><p>{{ amount(idea.windows) }} recorded windows. {{ learningReason(idea.reason) }}</p></details></section></details></article></template></div>
          <article v-if="review.curve.length" class="gx-tuner__idea gx-tuner__curve-summary"><span class="gx-tuner__badge" data-tone="review">{{ review.curve.length }} speed-range findings · suggested changes</span><h4>Small steering corrections at different speeds</h4><p>Auto Learn needs more evidence before changing these speed settings. These suggestions are not saved. Auto Learn manages this curve; your manual sliders are separate.</p><button type="button" class="gx-btn gx-btn--tonal" @click="reviewSetting(review.curve[0].parameter)">Review speed settings<i class="bi bi-arrow-right" aria-hidden="true"></i></button><details class="gx-tuner__disclosure"><summary>See all {{ review.curve.length }} findings</summary><div v-for="idea in review.curve" :key="idea.parameter" class="gx-tuner__curve-point" :data-coordinate="idea.parameter"><span>{{ curvePointLabel(idea.parameter) }}</span><small>{{ ideaStatus(idea) }}</small><dl class="gx-tuner__exact-values"><template v-for="value in ideaDetails(idea)" :key="value.label"><dt>{{ value.label }}</dt><dd>{{ value.value }}</dd></template></dl></div></details></article>
          <details v-if="review.other.length" id="tuner-earlier-findings" class="gx-tuner__disclosure"><summary>{{ review.other.length }} earlier grouped findings</summary><p>This older report does not identify a single dial or absolute target for these findings. They are not changes to apply.</p><p v-for="(idea, index) in review.other" :key="index">{{ readable(idea.parameter) }} · {{ recommendationText(idea) }} · {{ amount(idea.windows) }} recorded windows.</p></details>
        </section>
        <div class="gx-tuner__tune-layout"><div class="gx-tuner__section-heading"><div><h3>Explore your settings</h3><p>Choose an area below. Amber labels show findings from your drive.</p></div></div><nav class="gx-tuner__groups" aria-label="Steering parameter groups"><button v-for="item in navigationGroups" :key="item.id" type="button" :aria-pressed="group === item.id" :class="{ 'has-ideas': groupHasIdeas(item), 'gx-tuner__curve-tile': item.id === 'friction' }" @click="reviewGroup(item.id)"><i :class="['bi', item.icon]" aria-hidden="true"></i><span>{{ item.label }}<small>{{ item.id === 'friction' ? 'Auto Learn only · ' : '' }}{{ groupChanges(item) }}</small></span><i class="bi" :class="groupHasIdeas(item) ? 'bi-lightbulb' : 'bi-chevron-right'" aria-hidden="true"></i></button></nav>
          <section class="gx-card gx-tuner__section gx-tuner__parameters" :id="group === 'friction' ? 'tuner-friction' : undefined" :aria-labelledby="'tuner-group-' + group + '-title'" tabindex="-1"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">{{ group === 'friction' ? 'MANAGED BY AUTO LEARN' : 'YOUR MANUAL CONTROLS' }}</p><h3 :id="'tuner-group-' + group + '-title'">{{ selectedGroup.label }}</h3><p>{{ selectedGroup.description }}</p></div></div>
            <section v-if="group !== 'friction'" class="gx-tuner__manual" aria-label="Manual tuning"><div class="gx-tuner__manual-note"><i class="bi bi-info-circle" aria-hidden="true"></i><p>Moving a slider saves a manual override for your next drive and pauses Auto Learn. Zero removes the override. Reviewing a finding does neither.</p></div>
              <GxNotice v-if="manualState.data?.parked === false">Manual tuning is locked while driving. Park to make changes.</GxNotice>
              <div v-if="manualErrorText" class="gx-tuner__manual-error" tabindex="-1"><GxNotice tone="danger">{{ manualErrorText }}</GxNotice><details v-if="manualWrite?.status === 'unconfirmed' && manualState.error" class="gx-tuner__disclosure"><summary>Connection details</summary><p>{{ manualState.error }}</p></details></div><div v-if="manualReadbackText" class="gx-tuner__manual-readback" tabindex="-1"><GxNotice>{{ manualReadbackText }}</GxNotice></div>
              <p v-if="!manualState.data" class="gx-tuner__caption">{{ manualState.status === 'loading' ? 'Loading saved manual tune…' : 'Manual tuning unavailable. Reconnecting…' }}</p>
              <p v-else-if="!manualRows.length" class="gx-tuner__caption">{{ manualState.data.rows[0]?.reason || 'Manual tuning is unavailable for this controller.' }}</p>
              <article v-for="{ row, index } in manualRows" :key="row.coordinate" :id="'tuner-dial-' + row.coordinate" tabindex="-1" class="gx-tuner__dial" :class="{ 'has-ideas': dialIdeas(row.coordinate).length }"><div class="gx-tuner__dial-status"><span class="gx-tuner__badge" :data-tone="dialIdeas(row.coordinate).length ? 'review' : 'neutral'">{{ dialIdeas(row.coordinate).length ? 'Drive finding' : 'Manual setting' }}</span><span v-if="Number.isFinite(Number(row.value)) && Number(row.value) !== 0" class="gx-tuner__badge" data-tone="manual">Manual override saved</span><span v-if="dialState(row.coordinate)?.applied != null && dialState(row.coordinate)?.applied !== 0" class="gx-tuner__badge" data-tone="saved">Auto Learn applied {{ dialPercent(dialState(row.coordinate).applied) }}</span><span v-else-if="dialState(row.coordinate)?.saved" class="gx-tuner__badge" data-tone="saved">Auto Learn saved {{ dialPercent(dialState(row.coordinate).saved) }}</span></div>
                <div v-for="idea in dialIdeas(row.coordinate)" :key="idea.parameter" class="gx-tuner__dial-idea"><strong>{{ ideaStatus(idea) }}</strong><p>{{ ideaExplanation(idea) }}</p><details class="gx-tuner__disclosure"><summary>Recorded values</summary><dl class="gx-tuner__exact-values"><template v-for="value in ideaDetails(idea)" :key="value.label"><dt>{{ value.label }}</dt><dd>{{ value.value }}</dd></template></dl><p>Your manual value below is separate.</p></details></div>
                <GalaxySettingRow :row="{ ...row, label: parameterLabel(row.coordinate) }" :compact="true" :index="index" :busy="manualBusy" :disabled="!!manualState.error" :save-value="saveManual" @reset-default="resetManualDefault" />
              </article>
              <GalaxySettingRow v-for="{ row, index } in manualActions" :key="index" :row="row" :index="index" :busy="manualBusy" :disabled="!!manualState.error" :save-value="saveManual" @review="reviewManualReset" />
            </section>
            <section v-if="group === 'friction'" class="gx-tuner__curve"><p class="gx-tuner__manual-note">Auto Learn manages this curve automatically. Reviewing these values does not change them, and there are no manual sliders here.</p><article v-for="point in frictionRows" :key="point.name" class="gx-tuner__curve-point" :class="{ 'has-ideas': point.ideas.length }" :data-coordinate="point.name"><h4>{{ curvePointLabel(point.name) }}</h4><span v-if="point.saved !== null" class="gx-tuner__badge" data-tone="saved">Saved {{ dialPercent(point.saved) }}</span><span v-else class="gx-tuner__caption">No saved value reported</span><template v-for="idea in point.ideas" :key="idea.parameter"><span class="gx-tuner__badge" data-tone="review">{{ ideaStatus(idea) }}</span><p>{{ ideaExplanation(idea) }}</p><details class="gx-tuner__disclosure"><summary>Evidence &amp; exact values</summary><p>{{ parameterLabel(idea.parameter) }}. {{ ideaExplanation(idea) }}</p><dl class="gx-tuner__exact-values"><template v-for="value in ideaDetails(idea)" :key="value.label"><dt>{{ value.label }}</dt><dd>{{ value.value }}</dd></template></dl><p>{{ amount(idea.windows) }} recorded windows. {{ learningReason(idea.reason) }}</p></details></template><small v-if="!point.ideas.length">No new finding from this drive</small></article></section>
            <details v-if="group !== 'friction'" class="gx-tuner__disclosure gx-tuner__all-values">            <summary>All saved &amp; applied values</summary><p class="gx-tuner__caption">These reported results are separate from your manual settings.</p>
            <div class="gx-tuner__parameter-head" aria-hidden="true"><span>Parameter</span><span>{{ offroad ? 'Saved' : trackingValues ? 'Accepted' : 'Proposal' }}</span><span>{{ offroad ? 'Recommendation · not saved' : 'Trial' }}</span><span>Applied now</span></div>
            <div v-for="row in groupRows" :key="row.name" class="gx-tuner__parameter"><div><h4>{{ parameterLabel(row.name) }}</h4><small v-if="!row.pidSupported">Torque only · PID unsupported</small></div><div><span class="gx-tuner__mobile-label">{{ offroad ? 'Saved' : trackingValues ? 'Accepted' : 'Proposal' }}</span><strong>{{ coordinateText(offroad ? row.saved : trackingValues ? row.accepted : row.learned) }}</strong></div><div><span class="gx-tuner__mobile-label">{{ offroad ? 'Recommendation · not saved' : 'Trial' }}</span><template v-if="offroad"><strong v-for="(suggestion, index) in row.recommendations" :key="index">{{ recommendationText(suggestion) }}</strong><strong v-if="!row.recommendations.length">None reported</strong></template><strong v-else :class="{ 'gx-tuner__pending': row.pending !== null && row.pending !== row.accepted }">{{ coordinateText(row.pending) }}</strong></div><div><span class="gx-tuner__mobile-label">Applied now</span><strong :class="{ 'gx-tuner__applied': row.applied !== null && row.applied !== 0 }">{{ coordinateText(row.applied) }}</strong></div></div>
            <details class="gx-tuner__disclosure"><summary>How to read these values</summary><p>Zero is the original baseline. These are correction coordinates, not raw degrees or torque. Speed-window coordinates scale the original speed range; deadband and angle assistance use the controller's physical normalizer. A trial is an offer, not proof of application. All 30 stages exist for torque; 26 exist for torque-actuated PID.</p></details>
</details>
          </section></div>
      </section>
      <section v-show="view === 'plots'" id="tuner-panel-plots" role="tabpanel" aria-labelledby="tuner-tab-plots" class="gx-tuner__panel"><PlotsPage :mode="mode" :unauthorized="unauthorized" :active="view === 'plots'" embedded /></section>
      <section v-if="view === 'activity'" id="tuner-panel-activity" role="tabpanel" aria-labelledby="tuner-tab-activity" class="gx-tuner__panel"><section class="gx-card gx-tuner__section"><div class="gx-tuner__section-heading"><div><p class="gx-tuner__eyebrow">LEARNING ACTIVITY</p><h3>What the tuner found</h3></div><a v-if="!bluetooth" class="gx-btn gx-btn--tonal" href="./api/flm/logs/download" download="starpilot-flm-logs.json"><i class="bi bi-download" aria-hidden="true"></i>Download FLM logs</a><span v-else class="gx-tuner__caption">Connect over Wi-Fi to download FLM logs.</span></div>
          <template v-if="offroad"><h4>{{ review.title }}</h4><p>{{ review.description }}</p><div class="gx-tuner__area-tags"><span v-for="area in review.areas" :key="area">{{ area }}</span></div><button type="button" class="gx-btn gx-btn--tonal" @click="selectView('tune')">View drive findings</button><details class="gx-tuner__disclosure"><summary>Analysis details</summary><p>{{ offroad.retained || offroad.earlierDrive ? 'Earlier analyzed drive' : 'Completed drive' }} {{ offroad.route }}</p><p>{{ learningReason(offroad.reason) }}</p><p v-if="offroad.checks">{{ offroad.checks.passed }} / {{ offroad.checks.required }} controller and encoder windows passed.</p></details></template>
          <ol v-if="historyRows.length" class="gx-tuner__timeline"><li v-for="(row, index) in historyRows" :key="index"><span class="gx-tuner__timeline-dot" :class="{ 'gx-tuner__timeline-dot--applied': row.applied }"></span><div><strong>{{ historyTitle(row.status) }}</strong><p>{{ learningReason(row.reason) }}</p><small>{{ row.time }}<template v-if="row.applied"> · Applied at that time</template></small></div></li></ol>
          <div v-else-if="!offroad" class="gx-tuner__empty"><i class="bi bi-clock-history" aria-hidden="true"></i><h4>{{ current?.status === 'unavailable' ? 'Drive review could not finish' : 'No findings reported yet' }}</h4><p>{{ current?.status === 'unavailable' ? learningReason(current.reason) : 'This will show real collection, trial and rollback events as the learner reports them.' }}</p></div>
          <button v-if="(report?.history.length || 0) > 6" type="button" class="gx-btn gx-btn--tonal" @click="showAllHistory = !showAllHistory">{{ showAllHistory ? 'Show recent activity' : 'Show all reported activity' }}</button>
        </section>
        <details class="gx-card gx-tuner__section gx-tuner__disclosure"><summary>Diagnostics &amp; source details</summary><dl class="gx-tuner__diagnostics"><template v-if="tracking"><dt>Live learner state</dt><dd>{{ tracking.status }}</dd><dt>Live learner reason</dt><dd>{{ tracking.reason }}</dd></template><template v-if="trackingProgress"><dt>Paired inputs retained</dt><dd>{{ amount(trackingProgress.readyPairs) }}</dd></template><dt>Legacy collector state</dt><dd>{{ readable(current?.status) }}</dd><dt>Legacy collector reason</dt><dd>{{ readable(current?.reason) }}</dd><dt>Legacy retained blocks</dt><dd>{{ amount(current?.progress?.blocks) }}</dd><template v-if="current?.fit"><dt>Latest legacy fit</dt><dd>{{ readable(current.fit.status) }} · {{ readable(current.fit.reason) }}</dd><dt>Training / held-out windows</dt><dd>{{ amount(current.fit.trainWindows) }} / {{ amount(current.fit.heldoutWindows) }}</dd></template><template v-if="losses"><dt>Baseline / candidate loss</dt><dd>{{ losses.baseline.toPrecision(5) }} / {{ losses.learned.toPrecision(5) }}</dd></template><template v-for="[key, value] in details" :key="key"><dt>{{ detailLabel(key) }}</dt><dd>{{ detailText(value) }}</dd></template></dl>
          <template v-if="trackingPlot"><h4>Delay-paired error</h4><PlotGraph :chart="pairedErrorChart" title="Delay-paired steering error" /><p class="gx-tuner__caption">Episode {{ trackingPlot.episodeId }} · {{ trackingPlot.sampled ? 'Selected' : 'All' }} original endpoints. No interpolation.</p><p class="gx-tuner__caption">Control / applied output / pose: {{ sourceSeconds(trackingPlot.points.at(-1).controlMonoTime) }} / {{ sourceSeconds(trackingPlot.points.at(-1).applyMonoTime) }} / {{ sourceSeconds(trackingPlot.points.at(-1).poseMonoTime) }} monotonic seconds.</p></template>
        </details>
      </section>
    </template>
  </div>`,
}
