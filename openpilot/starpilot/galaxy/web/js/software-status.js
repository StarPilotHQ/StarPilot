import { DriveStatePanel } from "./drive-state.js"
import { GxDialog } from "./dialog.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { ToggleBackup } from "./toggle-backup.js"
import { GalaxySelect } from "./galaxy-select.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"
import { RouteCleanup } from "./route-cleanup.js"

const ACTIONS = new Set(["check", "download", "select", "install", "preferences", "install-preferences", "update", "fast", "rollback", "versions", "version", "download-version"])
const REQUEST_STATES = new Set(["pending", "complete", "failed"])
const UPDATER_ACTIVE = new Set(["checking...", "downloading...", "finalizing update...", "updating..."])
const PRIMARY_BRANCH_LABELS = { StarPilot: "Stable — StarPilot", Dom: "Development — Dom" }
const PRIMARY_BRANCH_HELP = {
  StarPilot: "Stable releases. Recommended for most users.",
  Dom: "Latest features and fixes under development. Updates regularly and may introduce bugs.",
}
const unavailableOperations = () => ({ parked: false, availableBranches: [], selectedTarget: null,
  canCheck: false, canFastUpdate: false, canRollback: false, canDownload: false, canSelect: false, canInstall: false,
  reason: "Update controls are unavailable", request: null })

export function validSoftwareSnapshot(data) {
  const text = (value) => value === null || typeof value === "string"
  const flag = (value) => value === null || typeof value === "boolean"
  const updater = data?.updater, operations = data?.operations, request = operations?.request
  return data?.schemaVersion === 1 && !!data.installed && !!updater &&
    [data.installed.version, data.installed.branch, data.installed.commit, updater.state,
      updater.targetBranch, updater.lastSuccessAt, updater.lastFetchAt].every(text) &&
    (updater.lastCheckedAt === undefined || text(updater.lastCheckedAt)) &&
    (data.installed.displayVersion === undefined || text(data.installed.displayVersion)) &&
    [updater.targetChangeFound, updater.finalizedUpdateReady].every(flag) &&
    (updater.failedCount === null || Number.isSafeInteger(updater.failedCount) && updater.failedCount >= 0) &&
    (operations === undefined || !!operations && typeof operations.parked === "boolean" && Array.isArray(operations.availableBranches) &&
    operations.availableBranches.length <= 256 && operations.availableBranches.every((branch) =>
      typeof branch === "string" && branch.length > 0 && branch.length <= 128) &&
    text(operations.selectedTarget) && text(operations.reason) &&
    (operations.progress === undefined || operations.progress === null || typeof operations.progress === "object" &&
      (operations.progress.background === undefined || typeof operations.progress.background === 'boolean') &&
      text(operations.progress.stage) && (operations.progress.stage === null || operations.progress.stage.length <= 128) &&
      text(operations.progress.detail) && (operations.progress.detail === null || operations.progress.detail.length <= 512) &&
      (operations.progress.percent === null || Number.isFinite(operations.progress.percent) &&
       operations.progress.percent >= 0 && operations.progress.percent <= 100)) &&
    (operations.canFastUpdate === undefined || typeof operations.canFastUpdate === "boolean") &&
    (request?.selectedCommit === undefined || /^[0-9a-f]{40}$/.test(request.selectedCommit)) &&
    (operations.canRollback === undefined || typeof operations.canRollback === "boolean") &&
    (operations.automaticDownloads === undefined || flag(operations.automaticDownloads)) &&
    (operations.automaticInstalls === undefined || flag(operations.automaticInstalls)) &&
    (operations.preparedCommit === undefined || operations.preparedCommit === null || /^[0-9a-f]{40}$/.test(operations.preparedCommit)) &&
    (operations.nextRestartAt === undefined || text(operations.nextRestartAt)) &&
    (operations.restartSchedule === undefined || typeof operations.restartSchedule === 'string' && operations.restartSchedule.length <= 128) &&
    (operations.installPreferenceRevision === undefined || operations.installPreferenceRevision === null || /^[0-9a-f]{32}$/.test(operations.installPreferenceRevision)) &&
    (operations.restartTimezone === undefined || text(operations.restartTimezone)) &&
    (operations.canConfigure === undefined || typeof operations.canConfigure === "boolean") &&
    (operations.history === undefined || validHistory(operations.history)) &&
    [operations.canCheck, operations.canDownload, operations.canSelect, operations.canInstall].every((value) => typeof value === "boolean") &&
    (request === null || !!request && typeof request.id === "string" && request.id.length <= 100 &&
      ACTIONS.has(request.action) && text(request.target) && REQUEST_STATES.has(request.state) && text(request.error) &&
      (request.outcome === undefined || ["up_to_date", "restarting"].includes(request.outcome))))
}

export function softwareProgress(updater, operations = {}) {
  if (!updater) return null
  const progress = operations.progress
  const fast = updater.state === "updating..." ? updater.fast : null
  const stage = progress?.stage || fast?.stage
  const active = UPDATER_ACTIVE.has(updater.state) && !["unavailable", "timed_out", "error", "complete"].includes(stage)
  const labels = { "checking...": "Checking for updates", "downloading...": "Downloading update",
    "finalizing update...": "Preparing update", "updating...": "Updating software",
    checking: "Checking for updates", downloading: "Downloading update", finalizing: "Preparing update",
    preparing: "Preparing update", fetching: "Downloading update", validating: "Validating update",
    applying: "Applying update", submodules: "Preparing dependencies", rebooting: "Restarting device",
    complete: "Update complete", error: "Update failed", unavailable: "Updater status unavailable", timed_out: "Update is taking longer than expected" }
  if (!active && (!stage || ["idle", "unknown"].includes(stage))) return null
  const percent = Number.isFinite(progress?.percent) && progress.percent >= 0 && progress.percent <= 100 ? progress.percent : null
  return { active, failed: ["error", "unavailable", "timed_out"].includes(stage), percent, label: labels[progress?.stage || fast?.stage] || progress?.stage || fast?.stage || labels[updater.state] || "Update status",
    detail: progress?.detail || fast?.detail || "" }
}

export function validHistory(value) {
  const rows = (items) => Array.isArray(items) && items.length <= 100 && items.every((row) =>
    /^[0-9a-f]{40}$/.test(row?.hash) && typeof row.date === "string" && row.date.length <= 64 &&
    typeof row.subject === "string" && row.subject.length <= 512)
  return !!value && rows(value.installed) && rows(value.downloaded) &&
    (value.previous === undefined || value.previous === null || rows([value.previous])) &&
    (value.downloadedCommits === undefined || Array.isArray(value.downloadedCommits) && value.downloadedCommits.length <= 201 &&
      value.downloadedCommits.every(hash => /^[0-9a-f]{40}$/.test(hash))) &&
    (value.recent === undefined || !!value.recent && (value.recent.branch === null || typeof value.recent.branch === "string") &&
      (value.recent.head === null || /^[0-9a-f]{40}$/.test(value.recent.head)) && rows(value.recent.entries)) &&
    [value.currentReleaseNotes, value.downloadedReleaseNotes].every((notes) => notes === null || typeof notes === "string" && notes.length <= 65536)
}

export class SoftwareStatusFeed {
  constructor({ includeHistory = true, publish, unauthorized = () => {}, restarting = () => {}, preparing = () => {}, cancelPreparation = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { includeHistory, publish, unauthorized, restarting, preparing, cancelPreparation, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.controller = null
    this.timeout = null
    this.pollTimer = null
    this.data = null
    this.attempted = false
    this.busy = false
    this.mutating = false
    this.blocked = false
    this.connectionLost = false
    this.uncertain = false
    this.uncertainAction = null
    this.actionPriorRequestId = null
    this.installBaseline = null
    this.notice = ""
    this.error = ""
    this.startupFailures = 0
    this.waitingForStatus = false
  }

  emit(status) {
    if (this.installBaseline && this.connectionLost) this.restarting(this.installBaseline)
    this.publish({ status, data: this.data, busy: this.busy, uncertain: this.uncertain,
    notice: this.notice, error: this.error, submittingAction: this.mutating ? this.submittingAction : null }) }

  stop() {
    this.active = false
    this.generation++
    this.controller?.abort()
    if (this.timeout !== null) this.cancelTimer(this.timeout)
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.controller = this.timeout = this.pollTimer = null
    this.data = this.installBaseline = this.uncertainAction = this.actionPriorRequestId = null
    this.attempted = false
    this.busy = this.mutating = this.blocked = this.connectionLost = this.uncertain = false
    this.notice = this.error = ""
    this.startupFailures = 0
    this.waitingForStatus = false
    this.emit("idle")
  }

  start() { this.stop(); this.active = true; return this.load() }

  needsRapidPoll() {
    return this.uncertain || !!this.installBaseline || this.data?.operations?.parked === false ||
      this.data?.operations?.request?.state === "pending" || UPDATER_ACTIVE.has(this.data?.updater?.state)
  }

  schedulePoll(delay = null) {
    if (!this.active || this.pollTimer !== null || this.controller) return
    const generation = this.generation
    this.pollTimer = this.later(() => {
      this.pollTimer = null
      if (this.active && generation === this.generation) this.load()
    }, delay ?? (this.needsRapidPoll() ? 1000 : 5000))
  }

  async run(body = null) {
    if (!this.active || this.mutating) return null
    const generation = ++this.generation
    this.controller?.abort()
    if (this.timeout !== null) this.cancelTimer(this.timeout)
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.pollTimer = null
    const controller = new AbortController()
    this.controller = controller
    this.busy = body !== null || this.data === null
    this.mutating = body !== null
    this.submittingAction = body?.action ?? null
    if (body !== null) { this.error = ""; this.notice = "" }
    this.emit(this.data ? "ready" : this.attempted ? "unavailable" : "loading")
    this.timeout = this.later(() => {
      if (!this.active || this.generation !== generation || this.controller !== controller) return
      controller.abort()
      this.generation++
      this.controller = this.timeout = null
      this.busy = this.mutating = false
      this.attempted = true
      this.blocked = true
      this.connectionLost = true
      if (body !== null) { this.uncertain = true; this.uncertainAction = body }
      this.error = body !== null ? "The request timed out. Its result is unknown; wait for a fresh status before trying another action." :
        "Software status timed out. Refresh to try again."
      this.emit(this.data ? "ready" : "unavailable")
      this.schedulePoll(2000)
    }, 5000)
    let reachedServer = false
    try {
      const response = await this.fetcher(body === null ? "./api/software/status" + (this.includeHistory ? "" : "?history=0") : "./api/software/action", {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
        ...(body === null ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      })
      if (!this.active || generation !== this.generation || controller.signal.aborted) return null
      reachedServer = true
      this.connectionLost = false
      const payload = typeof response.json === "function" ? await response.json().catch(() => null) : null
      if (!this.active || generation !== this.generation || controller.signal.aborted) return null
      if (response.status === 401 || response.status === 503 &&
          ["access_unavailable", "setup_required"].includes(payload?.code)) {
        this.stop()
        this.unauthorized()
        return null
      }
      // A tunnel may return its own JSON error page with a successful HTTP status.
      const deviceUnreachable = payload?.code === "connection_unavailable" || !payload?.code && [payload?.error, payload?.message].some(message =>
        typeof message === "string" && /^Your comma cannot be reached(?:\. Check your connection and try again\.?)?$/i.test(message.trim()))
      if (!response.ok || deviceUnreachable) {
        const error = new Error(payload?.error || payload?.message || "Software request failed. Refresh to try again.")
        error.connectionLost = deviceUnreachable || [502, 504].includes(response.status) ||
          response.status === 503 && !payload?.code &&
          !["Software status is unavailable", "Updater status is unavailable"].includes(payload?.error)
        error.rejected = !error.connectionLost && response.status >= 400 && response.status < 500
        error.startupUnavailable = body === null && response.status === 503 &&
          ["Software status is unavailable", "Updater status is unavailable"].includes(payload?.error)
        throw error
      }
      if (!validSoftwareSnapshot(payload)) {
        const error = new Error("Software response is unavailable. Refresh to try again.")
        error.connectionLost = payload === null
        throw error
      }
      const previous = this.data
      this.data = payload.operations === undefined ? { ...payload, operations: unavailableOperations() } : payload
      if (this.data.operations.history === undefined && previous?.operations?.history &&
          previous.installed.commit === this.data.installed.commit && previous.operations.selectedTarget === this.data.operations.selectedTarget)
        this.data.operations = { ...this.data.operations, history: previous.operations.history }
      this.attempted = true
      this.blocked = false
      this.startupFailures = 0
      this.waitingForStatus = false
      if (body !== null) {
        this.uncertain = false
        this.uncertainAction = null
        this.actionPriorRequestId = null
        if (["update", "fast", "rollback", "version"].includes(body.action)) {
          this.notice = `Updating ${body.branch}. Waiting for completion or reconnect.`
        } else if (body.action === "install") {
          this.installBaseline = this.installBaseline || { commit: payload.installed.commit, branch: payload.installed.branch, target: body.branch }
          this.notice = "Restart requested. Waiting to reconnect and verify the installed build."
        } else this.notice = body.action === "select" ? `Target branch set to ${body.branch}. Check and download the update when ready.` :
          body.action === "preferences" ? "Automatic download preference saved." :
          body.action === "install-preferences" ? "Automatic install preferences saved." : ""
      } else {
        const observed = payload.operations.request
        const requestObserved = !!observed && !!this.uncertainAction && observed.action === this.uncertainAction.action &&
          observed.id !== this.actionPriorRequestId &&
          (!this.uncertainAction?.branch || observed.target === this.uncertainAction.branch) &&
          (!this.uncertainAction?.selectedCommit || observed.selectedCommit === this.uncertainAction.selectedCommit)
        const selectionObserved = this.uncertainAction?.action === "select" &&
          payload.operations.selectedTarget === this.uncertainAction.branch
        const preferenceObserved = this.uncertainAction?.action === "preferences" &&
          payload.operations.automaticDownloads === this.uncertainAction.automaticDownloads
        const installPreferenceObserved = this.uncertainAction?.action === 'install-preferences' &&
          payload.operations.automaticInstalls === this.uncertainAction.automaticInstalls &&
          payload.operations.restartSchedule === this.uncertainAction.restartSchedule &&
          payload.operations.restartTimezone === this.uncertainAction.restartTimezone &&
          payload.operations.installPreferenceRevision !== this.uncertainAction.expectedRevision
        if (this.uncertain && (requestObserved || selectionObserved || preferenceObserved || installPreferenceObserved)) {
          this.uncertain = false
          this.uncertainAction = null
          this.actionPriorRequestId = null
        }
        if (["update", "fast", "version"].includes(this.installBaseline?.action) && observed?.action === this.installBaseline.action && observed.target === this.installBaseline.target &&
            (!this.installBaseline.selectedCommit || observed.selectedCommit === this.installBaseline.selectedCommit) &&
            ["complete", "failed"].includes(observed.state)) {
          if (observed.state === "failed" || observed.outcome === "up_to_date") {
            this.installBaseline = null
            this.cancelPreparation()
            this.notice = observed.state === "failed" ? "" : "Installed branch is already up to date. No restart needed."
          } else this.notice = "Update installed. Waiting to reconnect and verify the installed build."
        }
        if (this.installBaseline && payload.installed.branch === this.installBaseline.target &&
            (!this.installBaseline.selectedCommit || payload.installed.commit === this.installBaseline.selectedCommit) &&
            (payload.installed.commit && payload.installed.commit !== this.installBaseline.commit ||
             payload.installed.branch !== this.installBaseline.branch)) {
          this.installBaseline = null
          this.uncertain = false
          this.uncertainAction = null
          this.actionPriorRequestId = null
          this.cancelPreparation()
          this.notice = "Installed build verified after reconnect."
        }
        if (this.installBaseline && payload.operations.updaterAvailable === false && payload.operations.progress?.stage === "unavailable")
          this.restarting(this.installBaseline)
      }
      this.error = ""
      this.emit("ready")
      return payload
    } catch (error) {
      if (this.active && generation === this.generation && !controller.signal.aborted) {
        if (error?.startupUnavailable && !this.data && ++this.startupFailures < 3) {
          this.waitingForStatus = true
          this.error = ""
          return null
        }
        this.attempted = true
        this.blocked = true
        this.waitingForStatus = false
        this.connectionLost = !reachedServer || error?.connectionLost === true
        if (body !== null) {
          this.uncertain = !error?.rejected
          this.uncertainAction = this.uncertain ? body : null
          if (error?.rejected && ["install", "update", "fast", "rollback", "version"].includes(body.action)) {
            this.installBaseline = null
            this.cancelPreparation()
          }
        }
        this.error = error?.message || "Software request failed. Refresh to try again."
        this.emit(this.data ? "ready" : this.attempted ? "unavailable" : "loading")
      }
      return null
    } finally {
      if (this.controller === controller) {
        if (this.timeout !== null) this.cancelTimer(this.timeout)
        this.controller = this.timeout = null
        this.busy = this.mutating = false
        this.emit(this.data ? "ready" : this.waitingForStatus ? "loading" : "unavailable")
        this.schedulePoll(this.error ? 2000 : null)
      }
    }
  }

  load() { return this.run() }

  configureAutomaticDownloads(enabled) {
    if (!this.active || this.mutating || this.blocked || this.uncertain || this.data?.operations?.canConfigure !== true ||
        typeof enabled !== "boolean") return null
    return this.run({ action: "preferences", automaticDownloads: enabled,
      expectedAutomaticDownloads: this.data.operations.automaticDownloads })
  }

  configureInstallPreferences(enabled, schedule, timezone = Intl.DateTimeFormat().resolvedOptions().timeZone) {
    if (!this.active || this.mutating || this.blocked || this.uncertain || this.data?.operations?.canConfigure !== true ||
        typeof enabled !== 'boolean' || typeof schedule !== 'string') return null
    return this.run({action:'install-preferences', automaticInstalls:enabled, restartSchedule:schedule.trim().split(/\s+/).join(' '),
      expectedRevision:this.data.operations.installPreferenceRevision, restartTimezone:timezone})
  }

  action(action, branch = null, selection = null) {
    const operations = this.data?.operations
    const capability = { check: "canCheck", download: "canDownload", select: "canSelect", install: "canInstall", update: "canFastUpdate", fast: "canFastUpdate", rollback: "canRollback", versions: "canCheck", version: "canFastUpdate", "download-version": "canFastUpdate" }[action]
    if (!this.active || !capability || this.mutating || this.blocked || this.uncertain || !operations?.parked ||
        operations[capability] !== true || operations.request?.state === "pending" &&
        !(["check", "versions"].includes(action) && ["download", "download-version"].includes(operations.request.action))) return null
    if (action === "select" && (branch === "other:" || !operations.availableBranches.includes(branch) || branch === operations.selectedTarget)) return null
    if (["download", "install"].includes(action) && (!branch || branch !== operations.selectedTarget)) return null
    if (["update", "fast", "versions", "version", "download-version"].includes(action) && (!branch || branch !== this.data.installed.branch && !operations.availableBranches.includes(branch))) return null
    if (action === "rollback" && branch !== this.data.installed.branch) return null
    if (["version", "download-version"].includes(action)) {
      if (selection?.expectedCommit !== this.data.installed.commit || !/^[0-9a-f]{40}$/.test(selection?.selectedCommit) ||
          operations.history?.recent?.branch !== branch ||
          ![...operations.history.recent.entries, ...(branch === this.data.installed.branch ? operations.history.installed : [])]
            .some((row) => row.hash === selection.selectedCommit)) return null
    }
    if (action === "install" && selection?.selectedCommit &&
        (selection.expectedCommit !== this.data.installed.commit ||
         operations.history?.downloaded?.[0]?.hash !== selection.selectedCommit)) return null
    this.actionPriorRequestId = operations.request?.id ?? null
    if (action === "install") this.installBaseline = { commit: this.data.installed.commit,
      branch: this.data.installed.branch, target: branch, action,
      ...(selection?.selectedCommit ? { selectedCommit: selection.selectedCommit } : {}) }
    if (["update", "fast", "rollback", "version"].includes(action)) this.installBaseline = { commit: this.data.installed.commit, branch: this.data.installed.branch, target: branch, action, ...(action === "version" ? { selectedCommit: selection.selectedCommit } : {}) }
    if (this.installBaseline) this.preparing(this.installBaseline)
    if (["version", "download-version"].includes(action) || action === "install" && selection?.selectedCommit) return this.run({ action, branch, expectedCommit: selection.expectedCommit, selectedCommit: selection.selectedCommit })
    return this.run(action === "check" ? { action } : { action, branch })
  }
}

export const SoftwarePage = {
  components: { DriveStatePanel, GxDialog, GxState, GxNotice, GalaxySelect, GalaxySettingRow, ToggleBackup, RouteCleanup },
  name: "SoftwarePage",
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true }, restarting: { type: Function, default: () => {} },
    preparing: { type: Function, default: () => {} }, cancelPreparation: { type: Function, default: () => {} } },
  data: () => ({ status: "idle", data: null, busy: false, uncertain: false, notice: "", error: "", submittingAction: null,
    initialChecking: true, draftBranch: "", primaryChoice: "", draftTouched: false, dialog: null, newerVersionLimit: 2, olderVersionLimit: 1, collapsedOlderLimit: 0, version: "", restartCronFields: ["0", "0", "*", "*", "*"], scheduleTouched: false, restartTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone }),
  created() {
    this.feed = new SoftwareStatusFeed({ includeHistory: false, publish: (update) => {
      Object.assign(this.$data, update)
      if (update.data?.operations.restartSchedule && !this.scheduleTouched) {
        this.restartCronFields = update.data.operations.restartSchedule.split(' ')
        this.restartTimezone = update.data.operations.restartTimezone || Intl.DateTimeFormat().resolvedOptions().timeZone
      }
      if (update.data && !this.draftTouched) this.syncDraftBranch(update.data.operations.selectedTarget)
    }, unauthorized: this.unauthorized, restarting: this.restarting,
    preparing: this.preparing, cancelPreparation: this.cancelPreparation })
  },
  async mounted() {
    if (this.mode !== "local") return
    try {
      await this.feed.start()
      if (this.feed.active && this.operations?.canCheck && (!this.pending || ['download', 'download-version'].includes(this.operations.request?.action)) &&
          this.data?.updater.state !== 'updating...') await this.feed.action("check")
    } finally { this.initialChecking = false }
  },
  beforeUnmount() { this.feed.stop() },
  computed: {
    operations() { return this.data?.operations },
    restartScheduleChanged() { return this.restartCronFields.join(' ') !== this.operations?.restartSchedule ||
      this.restartTimezone !== this.operations?.restartTimezone },
    restartTimezones() {
      return [...new Set(["UTC", this.restartTimezone, this.operations?.restartTimezone,
        Intl.DateTimeFormat().resolvedOptions().timeZone, ...(Intl.supportedValuesOf?.("timeZone") || [])].filter(Boolean))].sort()
    },
    reportedProgress() { return softwareProgress(this.data?.updater, this.operations) },
    metadataProgress() {
      const check = this.data?.updater.fast?.check
      if (['check', 'versions'].includes(this.submittingAction) || check?.state === 'checking' || this.pending && ['check', 'versions'].includes(this.operations?.request?.action))
        return { active: true, percent: null, label: 'Checking for new versions', detail: check?.state === 'checking' ? check.detail : '' }
      return null
    },
    backgroundProgress() {
      return this.reportedProgress?.active && this.operations?.progress?.background ? this.reportedProgress : null
    },
    updateProgress() {
      if (this.error || this.operations?.request?.state === "failed") return null
      if (this.metadataProgress) return this.metadataProgress
      const progress = this.reportedProgress
      if (this.backgroundProgress) return null
      if (progress?.active) return progress
      if (progress?.failed) return null
      if (this.initialChecking || this.busy || this.pending || this.uncertain) return {
        active: true, percent: null, label: this.initialChecking || this.operations?.request?.action === "check" ?
          "Checking for updates" : this.requestMessage(this.operations?.request) || "Reading update status", detail: "" }
      return null
    },
    updateAvailable() { return this.data?.updater.targetChangeFound === true || this.data?.updater?.finalizedUpdateReady === true },
    osUpdateRequired() { return this.data?.updater.osUpdate?.required === true &&
      this.data.updater.osUpdate.branch === (this.draftBranch || this.operations?.selectedTarget) &&
      this.data.updater.osUpdate.branch === this.data.updater.targetBranch },
    canUpdate() {
      if (this.data?.updater?.finalizedUpdateReady && this.draftBranch === this.operations?.selectedTarget) return this.operations.canInstall
      return this.operations?.canFastUpdate === true && this.branchOptions.some(branch => branch.name === this.draftBranch && branch.available)
    },
    updateBanner() {
      const request = this.operations?.request
      const progress = this.reportedProgress
      const error = this.error || (request?.state === "failed" ? this.requestMessage(request) : "") ||
        (progress?.failed ? progress.detail || progress.label : "")
      if (error) return { tone: "danger", message: error }
      if (this.updateProgress) return null
      if (this.operations?.reason && !(this.backgroundProgress && this.operations.reason === 'Updater is busy')) return { tone: "warn", message: this.operations.reason }
      if (this.updateAvailable && this.osUpdateRequired) return { tone: "warn",
        message: this.data.updater.finalizedUpdateReady ? "Update ready — includes an operating system update. Restart to install." :
          "Update available — operating system update required. This is a large download and may take a while. Keep the device powered and connected to Wi-Fi." }
      if (this.data?.updater.finalizedUpdateReady && this.data.updater.targetBranch === (this.draftBranch || this.operations?.selectedTarget)) return { tone: "success", message: "Downloaded · Ready to install" }
      if (this.notice) return { tone: "info", message: this.notice }
      if (request && !["check", "versions"].includes(request.action)) return { tone: "info", message: this.requestMessage(request) }
      if (this.updateAvailable) return { tone: "success", message: "Update Available" }
      if (this.data?.updater.targetChangeFound === false) return { tone: "success", message: "No Update Available" }
      return { tone: "warn", message: "Update status unavailable. Check for updates to try again." }
    },
    repositoryUrl() {
      const repository = this.operations?.repository
      return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository || "") ? `https://github.com/${repository}` : ""
    },
    commitUrl() { return this.repositoryUrl && /^[0-9a-f]{7,40}$/.test(this.data?.installed.commit || "") ? `${this.repositoryUrl}/commit/${this.data.installed.commit}` : "" },
    pending() { return this.operations?.request?.state === "pending" },
    actionUnavailable() { return this.uncertain || this.pending || !!this.error || !this.operations?.parked },
    actionDisabled() { return this.busy || this.actionUnavailable },
    metadataActionDisabled() { return this.busy || this.uncertain || !!this.error || !this.operations?.parked },
    branchOptions() {
      const available = this.operations?.availableBranches || []
      return [...new Set([this.operations?.selectedTarget, this.data?.installed.branch, this.previousVersion?.branch, ...available].filter(branch => branch && branch !== "other:"))]
        .map(name => ({ name, available: available.includes(name) || name === this.data?.installed.branch }))
    },
    otherBranches() { return this.branchOptions.filter(branch => !['StarPilot', 'Dom'].includes(branch.name)) },
    hasInstalledVersion() {
      const recent = this.operations?.history?.recent
      return this.draftBranch === this.data?.installed.branch ||
        recent?.branch === this.draftBranch && recent.entries.some(entry => entry.hash === this.data?.installed.commit)
    },
    listedPreviousVersion() {
      const previous = this.previousVersion
      if (!previous) return null
      const history = this.operations?.history
      const recent = history?.recent?.branch === this.draftBranch ? history.recent.entries : []
      const known = recent.some(entry => entry.hash === previous.hash) ||
        this.hasInstalledVersion && history.installed.some(entry => entry.hash === previous.hash)
      const locallySelected = this.version === previous.hash && this.draftBranch === (previous.branch || this.data?.installed.branch)
      return known || locallySelected ? previous : null
    },
    versionEntries() {
      const history = this.operations?.history
      if (!history) return []
      const rows = new Map()
      const recent = history.recent?.branch === this.draftBranch ? history.recent.entries : []
      const downloaded = this.data.updater.finalizedUpdateReady && this.data.updater.targetBranch === this.draftBranch && this.operations.selectedTarget === this.draftBranch ? history.downloaded.slice(0, 1) : []
      const installed = this.hasInstalledVersion ? history.installed : []
      for (const entry of [...recent, ...downloaded, ...installed, ...(this.listedPreviousVersion ? [this.listedPreviousVersion] : [])]) rows.set(entry.hash, entry)
      const current = this.data.installed.commit
      if (this.hasInstalledVersion && current && !rows.has(current)) rows.set(current, { hash: current, date: "", subject: "Installed version" })
      return [...rows.values()]
    },
    newerVersions() {
      const index = this.versionEntries.findIndex(entry => entry.hash === this.data?.installed.commit)
      return (index < 0 ? this.versionEntries : this.versionEntries.slice(0, index))
        .filter(entry => entry.hash !== this.listedPreviousVersion?.hash)
    },
    olderVersions() {
      const index = this.versionEntries.findIndex(entry => entry.hash === this.data?.installed.commit)
      return index < 0 ? [] : this.versionEntries.slice(index + 1)
    },
    previousVersion() {
      const previous = this.operations?.history?.previous
      return previous && previous.hash !== this.data?.installed.commit ? previous : null
    },
    beforePrevious() {
      const index = this.olderVersions.findIndex(entry => entry.hash === this.listedPreviousVersion?.hash)
      return this.listedPreviousVersion && index >= 0 ? this.olderVersions.slice(0, index) : []
    },
    afterPrevious() {
      const index = this.olderVersions.findIndex(entry => entry.hash === this.listedPreviousVersion?.hash)
      return this.listedPreviousVersion && index >= 0 ? this.olderVersions.slice(index + 1) : this.olderVersions
    },
    visibleVersions() {
      const installed = this.versionEntries.find(entry => entry.hash === this.data?.installed.commit)
      const previous = this.listedPreviousVersion
      return [...this.newerVersions.slice(0, this.newerVersionLimit), ...(installed ? [installed] : []),
        ...(previous ? [...this.beforePrevious.slice(0, this.collapsedOlderLimit), previous] : []),
        ...this.afterPrevious.slice(0, Math.max(0, this.olderVersionLimit - (previous ? 1 : 0)))]
    },
    hiddenNewerCount() { return Math.max(0, this.newerVersions.length - this.newerVersionLimit) },
    hiddenCollapsedOlderCount() { return Math.max(0, this.beforePrevious.length - this.collapsedOlderLimit) },
    hiddenOlderCount() { return Math.max(0, this.afterPrevious.length - Math.max(0, this.olderVersionLimit - (this.listedPreviousVersion ? 1 : 0))) },
    downloadedVersions() {
      return new Set([...(this.operations?.history?.downloadedCommits || []), this.downloadedCommit].filter(Boolean))
    },
    selectedVersion() { return this.versionEntries.find(entry => entry.hash === (this.version || this.data?.installed.commit)) },
    downloadedCommit() {
      return this.data?.updater.finalizedUpdateReady && this.data.updater.targetBranch === this.draftBranch && this.draftBranch === this.operations?.selectedTarget ?
        this.operations?.history?.downloaded?.[0]?.hash : null
    },
    selectedVersionAction() {
      if (!this.selectedVersion || this.selectedVersion.hash === this.data?.installed.commit) return "No changes selected"
      if (this.selectedVersion.hash === this.previousVersion?.hash) return "Install selected version"
      return this.downloadedVersions.has(this.selectedVersion.hash) ? "Install selected version" : "Download and install selected version"
    },
    canInstallSelection() {
      if (this.actionDisabled || !this.selectedVersion || this.selectedVersion.hash === this.data?.installed.commit) return false
      if (this.selectedVersion.hash === this.previousVersion?.hash) return this.operations?.canRollback === true
      if (this.selectedVersion.hash === this.downloadedCommit) return this.operations?.canInstall === true
      return this.operations?.canFastUpdate === true && this.operations.history?.recent?.branch === this.draftBranch &&
        [...this.operations.history.recent.entries, ...(this.draftBranch === this.data.installed.branch ? this.operations.history.installed : [])]
          .some(entry => entry.hash === this.selectedVersion.hash)
    },
    selectionNeedsInstall() {
      return this.canInstallSelection && this.data?.updater.state !== 'updating...' && !this.feed?.installBaseline
    },
    canDownloadSelection() {
      return !this.actionDisabled && this.operations?.canFastUpdate === true && !!this.selectedVersion &&
        this.selectedVersion.hash !== this.data?.installed.commit && !this.downloadedVersions.has(this.selectedVersion.hash) &&
        this.operations.history?.recent?.branch === this.draftBranch &&
        [...this.operations.history.recent.entries, ...(this.draftBranch === this.data.installed.branch ? this.operations.history.installed : [])]
          .some(entry => entry.hash === this.selectedVersion.hash)
    },
    selectedReleaseNotes() {
      if (this.selectedVersion?.hash === this.data?.installed.commit) return this.operations?.history?.currentReleaseNotes
      if (this.selectedVersion?.hash === this.downloadedCommit) return this.operations?.history?.downloadedReleaseNotes
      return null
    },
    commitsUrl() { return this.data?.installed.branch ? `https://github.com/StarPilotHQ/StarPilot/commits/${encodeURIComponent(this.data.installed.branch)}/` : "" },
  },
  methods: {
    switchRow,
    configureAutomaticInstalls(enabled) {
      return this.feed.configureInstallPreferences(enabled, this.operations.restartSchedule || '0 0 * * *', this.operations.restartTimezone || this.restartTimezone)
    },
    async saveRestartSchedule() {
      const result = await this.feed.configureInstallPreferences(this.operations.automaticInstalls === true, this.restartCronFields.join(' '), this.restartTimezone)
      if (result) {
        this.scheduleTouched = false
        this.restartCronFields = result.operations.restartSchedule.split(' ')
        this.restartTimezone = result.operations.restartTimezone
      }
    },
    syncDraftBranch(branch) {
      this.draftBranch = branch || this.data?.installed.branch || ""
      this.primaryChoice = ['StarPilot', 'Dom'].includes(this.draftBranch) ? this.draftBranch : this.draftBranch ? 'other:' : ''
    },
    onPrimaryBranchChange() {
      this.draftTouched = true
      if (this.primaryChoice === 'other:') {
        if (['StarPilot', 'Dom'].includes(this.draftBranch)) this.draftBranch = ''
        return
      }
      this.draftBranch = this.primaryChoice
      return this.changeBranch()
    },
    async changeBranch() {
      this.primaryChoice = ['StarPilot', 'Dom'].includes(this.draftBranch) ? this.draftBranch : 'other:'
      this.draftTouched = true
      this.version = ""
      this.newerVersionLimit = 2; this.olderVersionLimit = 1; this.collapsedOlderLimit = 0
      return this.loadVersions()
    },
    selectPreviousVersion() {
      if (this.actionDisabled || !this.previousVersion) return
      const previous = this.previousVersion
      this.draftTouched = true
      this.syncDraftBranch(previous.branch || this.data.installed.branch)
      this.version = previous.hash
      this.newerVersionLimit = 2; this.olderVersionLimit = 1; this.collapsedOlderLimit = 0
    },
    downloadSelectedVersion() {
      if (!this.canDownloadSelection) return
      return this.feed.action('download-version', this.draftBranch,
        { expectedCommit: this.data.installed.commit, selectedCommit: this.selectedVersion.hash })
    },
    branchLabel(branch) { return PRIMARY_BRANCH_LABELS[branch] || branch },
    primaryBranchHelpFor(branch) { return PRIMARY_BRANCH_HELP[branch] || "" },
    shown(value) { return value ?? "Unavailable" },
    reported(value) { return value ? new Date(value).toLocaleString() : "Unavailable" },
    shortCommit(value) { return value ? value.slice(0, 8) : "Unavailable" },
    requestMessage(request) {
      if (!request) return ""
      if (request.state === "failed") return request.error || "Update request failed. Refresh and try again."
      if (["update", "fast", "rollback", "version"].includes(request.action)) return request.state === "pending" ? `Updating ${request.target}…` :
        request.outcome === "up_to_date" ? "Installed branch is already up to date. No restart needed." :
        "Update installed. Waiting to reconnect and verify the installed build."
      if (request.action === "download-version") return request.state === "pending" ? "Downloading selected version…" : "Downloaded · Ready to install"
      if (request.action === "versions") return request.state === "pending" ? "Loading recent versions…" : ""
      if (request.action === "install") return "Restart requested. Waiting to reconnect and verify the installed build."
      if (request.state === "pending") return request.action === "check" ? "Checking for updates…" : "Downloading and preparing update…"
      return request.action === "check" ? "Update check finished." : ["download", "download-version"].includes(request.action) ? "Downloaded · Ready to install" : "Target branch saved."
    },
    askUpdate() {
      const branch = this.draftBranch || this.operations?.selectedTarget || this.data?.installed?.branch
      if (this.actionDisabled || !branch) return
      if (this.data?.updater?.finalizedUpdateReady && branch === this.operations?.selectedTarget) return this.askInstall()
      if (this.operations?.canFastUpdate !== true) return
      this.dialog = { action: "update", branch, title: "Update software",
        message: `Install the latest version of ${branch} and restart?`, label: "Update & restart" }
    },
    loadHistory(event) {
      if (!event.target.open || this.operations?.history || this.busy) return
      this.feed.includeHistory = true
      this.feed.load()
    },
    async loadVersions() {
      const branch = this.draftBranch || this.operations?.selectedTarget || this.data?.installed?.branch
      if (this.metadataActionDisabled || !branch) return
      this.feed.includeHistory = true
      return this.feed.action("versions", branch)
    },
    askSelectedVersion() {
      if (!this.canInstallSelection) return
      const entry = this.selectedVersion
      if (entry.hash === this.listedPreviousVersion?.hash) return this.askRollback()
      const downloaded = entry.hash === this.downloadedCommit
      this.dialog = { action: downloaded ? "install" : "version", branch: this.draftBranch,
        selectedCommit: entry.hash, expectedCommit: this.data.installed.commit,
        title: this.selectedVersionAction, label: this.selectedVersionAction,
        message: `${downloaded ? "Install" : "Download and install"} ${entry.subject} (${this.shortCommit(entry.hash)}) and restart the device?` }
    },
    askRollback() {
      const branch = this.data?.installed?.branch
      if (this.actionDisabled || this.operations?.canRollback !== true || !branch) return
      this.dialog = { action: "rollback", branch, title: "Previous version",
        message: "Restore the previous installed version and restart? Automatic downloads will be turned off.", label: "Restore previous version" }
    },
    askInstall() {
      if (this.actionDisabled || !this.operations?.canInstall || !this.operations.selectedTarget) return
      this.dialog = { action: "install", branch: this.operations.selectedTarget, title: "Restart and install update",
        message: `Restart the device to install the finalized update for ${this.operations.selectedTarget}? Keep the vehicle parked and the device accessible.`, label: "Restart & install" }
    },
    closeDialog() { this.dialog = null },
    async confirmDialog() {
      const choice = this.dialog
      if (!choice) return
      this.dialog = null
      const result = await this.feed.action(choice.action, choice.branch, choice)
      if (result && choice.action === "select") {
        this.draftTouched = false
        this.syncDraftBranch(result.operations.selectedTarget)
      }
    },
  },
  template: `
    <div class="gx-view gx-stack" :inert="busy" :aria-busy="busy || undefined">
      <h2>Software &amp; Updates</h2>
      <GxState v-if="mode !== 'local'">Software updates are unavailable in preview.</GxState>
      <template v-else>
        <section class="gx-card gx-panel gx-stack gx-software-card">
          <dl v-if="data" class="gx-software-summary">
            <div><dt>Branch</dt><dd><a v-if="commitsUrl" class="gx-link" :href="commitsUrl" target="_blank" rel="noopener noreferrer"><i class="bi bi-github" aria-hidden="true"></i> {{ data.installed.branch }}</a><span v-else>{{ shown(data.installed.branch) }}</span></dd></div>
            <div><dt>Version</dt><dd>{{ shown(data.installed.displayVersion ?? data.installed.version) }}</dd></div>
            <div><dt>Commit</dt><dd class="gx-actions">
              <a v-if="commitUrl" class="gx-link" :href="commitUrl" target="_blank" rel="noopener noreferrer" title="View commit on GitHub"><i class="bi bi-github" aria-hidden="true"></i> {{ shortCommit(data.installed.commit) }}</a>
              <span v-else>{{ shortCommit(data.installed.commit) }}</span>
            </dd></div>
          </dl>
          <GxNotice v-if="updateBanner" :tone="updateBanner.tone">{{ updateBanner.message }}</GxNotice>
          <div v-if="updateProgress" class="gx-update-progress" role="status">
            <div class="gx-update-progress__heading"><span>{{ updateProgress.label }}</span><span v-if="updateProgress.percent !== null">{{ Math.round(updateProgress.percent) }}%</span></div>
            <div v-if="updateProgress.active || updateProgress.percent !== null" class="gx-update-progress__track" role="progressbar" :aria-label="updateProgress.label"
              aria-valuemin="0" aria-valuemax="100" :aria-valuenow="updateProgress.percent ?? undefined">
              <div class="gx-update-progress__fill" :class="{'gx-update-progress__fill--indeterminate': updateProgress.active && updateProgress.percent === null}"
                :style="updateProgress.percent !== null ? {width: updateProgress.percent + '%'} : {}"></div>
            </div><p v-if="updateProgress.detail" class="gx-note">{{ updateProgress.detail }}</p>
          </div>
          <div v-if="!initialChecking && !updateProgress" class="gx-actions">
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="metadataActionDisabled || !operations?.canCheck" @click="feed.action('check')">Check for updates</button>
            <button v-if="updateAvailable || branchChanging" type="button" class="gx-btn" :disabled="actionDisabled || !canUpdate" @click="askUpdate">Update</button>
          </div>
          <div v-if="backgroundProgress" class="gx-update-progress" role="status" aria-live="polite">
            <div class="gx-update-progress__heading"><span>Background download · {{ backgroundProgress.label }}</span><span v-if="backgroundProgress.percent !== null">{{ Math.round(backgroundProgress.percent) }}%</span></div>
            <div class="gx-update-progress__track" role="progressbar" aria-label="Background update download" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="backgroundProgress.percent ?? undefined">
              <div class="gx-update-progress__fill" :class="{'gx-update-progress__fill--indeterminate': backgroundProgress.percent === null}" :style="backgroundProgress.percent !== null ? {width: backgroundProgress.percent + '%'} : {}"></div>
            </div><p v-if="backgroundProgress.detail" class="gx-note">{{ backgroundProgress.detail }}</p>
          </div>
          <p v-if="data?.updater.lastCheckedAt || data?.updater.lastFetchAt" class="gx-note">
            <span v-if="data.updater.lastCheckedAt">Checked {{ reported(data.updater.lastCheckedAt) }}</span>
            <span v-if="data.updater.lastFetchAt"> · Downloaded {{ reported(data.updater.lastFetchAt) }}</span>
          </p>
          <p v-if="operations?.preparedCommit && operations.preparedCommit !== data?.installed.commit" class="gx-note">
            <a v-if="repositoryUrl" class="gx-link" :href="repositoryUrl + '/commit/' + operations.preparedCommit" target="_blank" rel="noopener noreferrer" title="View commit on GitHub">{{ shortCommit(operations.preparedCommit) }}</a><span v-else>{{ shortCommit(operations.preparedCommit) }}</span> is ready for install. Click Update when ready.
            <template v-if="operations.automaticInstalls && operations.nextRestartAt">Auto install will run at {{ reported(operations.nextRestartAt) }} while offroad.</template>
          </p>
          <GalaxySettingRow v-if="operations?.automaticDownloads !== undefined" class="gx-row--borderless"
            :row="switchRow('Download updates automatically', operations.automaticDownloads, 'Prepare updates while parked; restart when ready.')"
            :index="0" :busy="busy" :disabled="uncertain || !!error || !operations.canConfigure"
            :save-value="(index, value) => feed.configureAutomaticDownloads(value === 'On')" />
          <p v-if="operations?.automaticDownloads === null" class="gx-note">The download preference could not be read. Choose a setting while parked to repair it.</p>
          <GalaxySettingRow v-if="operations?.automaticInstalls !== undefined" class="gx-row--borderless"
            :row="switchRow('Install updates automatically', operations.automaticInstalls, 'Restart on the configured schedule, only while offroad.')"
            :index="1" :busy="busy" :disabled="uncertain || !!error || !operations.canConfigure"
            :save-value="(index, value) => configureAutomaticInstalls(value === 'On')" />
          <p v-if="operations?.automaticInstalls === null" class="gx-note">Install preferences are unreadable. Save the schedule to repair them.</p>
          <details v-if="data" class="gx-software-options" @toggle="loadHistory($event)"><summary>Advanced options</summary><div class="gx-stack">
            <DriveStatePanel offroad-only :lockout="operations?.parked === false" />
            <label class="gx-field-group"><span class="gx-row__label">Branch</span>
              <GalaxySelect show-description v-model="primaryChoice" class="gx-field gx-field--full" aria-label="Version branch" :disabled="metadataActionDisabled || !operations.canCheck" @change="onPrimaryBranchChange">
                <option value="StarPilot" :data-description="primaryBranchHelpFor('StarPilot')">Stable — StarPilot</option>
                <option value="Dom" :data-description="primaryBranchHelpFor('Dom')">Development — Dom</option>
                <option value="other:">Other branches…</option>
              </GalaxySelect>
            </label>
            <label v-if="primaryChoice === 'other:'" class="gx-field-group"><span class="gx-row__label">Other branches</span>
              <GalaxySelect v-model="draftBranch" class="gx-field gx-field--full" aria-label="Other branches" :disabled="metadataActionDisabled || !operations.canCheck" @change="changeBranch">
                <option value="" disabled>{{ otherBranches.length ? 'Select another branch' : 'No other branches available' }}</option>
                <option v-for="branch in otherBranches" :key="branch.name" :value="branch.name">{{ branch.name }}</option>
              </GalaxySelect>
            </label>
            <div class="gx-actions gx-version-actions">
              <button type="button" class="gx-btn" :class="{'gx-version-action--pending': selectionNeedsInstall}" :disabled="!canInstallSelection" @click="askSelectedVersion">{{ selectedVersionAction }}</button>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="metadataActionDisabled || !operations.canCheck" @click="loadVersions">Refresh</button>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="!canDownloadSelection" @click="downloadSelectedVersion">Download only</button>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="actionDisabled || !previousVersion" @click="selectPreviousVersion">Select previous version</button>
            </div>
            <div v-if="visibleVersions.length" class="gx-version-scroll" role="region" aria-label="Versions" tabindex="0">
              <ol class="gx-version-list">
                <template v-for="entry in visibleVersions" :key="entry.hash">
                  <li v-if="hiddenNewerCount && entry.hash === data.installed.commit" class="gx-version-gap">
                    <button type="button" class="gx-btn gx-btn--tonal" @click="newerVersionLimit+=10">Load {{ Math.min(10, hiddenNewerCount) }}{{ hiddenNewerCount > 10 ? ' of ' + hiddenNewerCount : '' }} collapsed {{ hiddenNewerCount === 1 ? 'version' : 'versions' }}</button>
                  </li>
                  <li v-if="hiddenCollapsedOlderCount && entry.hash === listedPreviousVersion?.hash" class="gx-version-gap">
                    <button type="button" class="gx-btn gx-btn--tonal" @click="collapsedOlderLimit+=Math.min(10, hiddenCollapsedOlderCount)">Load {{ Math.min(10, hiddenCollapsedOlderCount) }}{{ hiddenCollapsedOlderCount > 10 ? ' of ' + hiddenCollapsedOlderCount : '' }} collapsed {{ hiddenCollapsedOlderCount === 1 ? 'version' : 'versions' }}</button>
                  </li>
                  <li>
                    <label class="gx-version-row" :class="{ 'gx-version-row--installed': entry.hash === data.installed.commit, 'gx-version-row--selected': entry.hash === (version || data.installed.commit) }">
                      <input type="radio" name="software-version" :value="entry.hash" :checked="entry.hash === (version || data.installed.commit)" @change="version=entry.hash" :disabled="actionDisabled" />
                      <span class="gx-version-content">
                        <strong>{{ entry.subject.trim() || shortCommit(entry.hash) }}</strong>
                        <small class="gx-version-meta">{{ entry.date ? reported(entry.date) + ' · ' : '' }}<a v-if="repositoryUrl" class="gx-link" :href="repositoryUrl + '/commit/' + entry.hash" target="_blank" rel="noopener noreferrer" title="View commit on GitHub" @click.stop>{{ shortCommit(entry.hash) }}</a><span v-else>{{ shortCommit(entry.hash) }}</span><span v-if="entry.hash === data.installed.commit && branchChanging"> · {{ data.installed.branch }}</span><span v-if="entry.hash === data.installed.commit" class="gx-version-badge gx-version-badge--installed">Installed</span><span v-else-if="entry.hash === previousVersion?.hash" class="gx-version-badge gx-version-badge--downloaded">Previous version</span><span v-else class="gx-version-badge" :class="{ 'gx-version-badge--downloaded': downloadedVersions.has(entry.hash) }">{{ downloadedVersions.has(entry.hash) ? 'Downloaded' : 'Not downloaded' }}</span></small>
                      </span>
                    </label>
                  </li>
                </template>
                <li v-if="!hasInstalledVersion && hiddenNewerCount" class="gx-version-gap">
                  <button type="button" class="gx-btn gx-btn--tonal" @click="newerVersionLimit+=10">Load {{ Math.min(10, hiddenNewerCount) }} more older {{ hiddenNewerCount === 1 ? 'version' : 'versions' }}</button>
                </li>
                <li v-if="hiddenOlderCount" class="gx-version-gap">
                  <button type="button" class="gx-btn gx-btn--tonal" @click="olderVersionLimit+=10">Load {{ Math.min(10, hiddenOlderCount) }} more older {{ hiddenOlderCount === 1 ? 'version' : 'versions' }}</button>
                </li>
              </ol>
            </div>
            <p v-else class="gx-note">{{ operations.history ? 'No versions saved.' : 'Reading history…' }}</p>
            <div v-if="selectedReleaseNotes?.trim()"><h4>Release notes</h4><pre class="gx-release-notes">{{ selectedReleaseNotes }}</pre></div>
            <p v-else-if="selectedVersion" class="gx-note">Release notes are not available for this version.</p>
            <form v-if="operations?.restartSchedule !== undefined" class="gx-stack" @submit.prevent="saveRestartSchedule">
              <h4>Automatic restart schedule</h4>
              <div class="gx-restart-cron">
                <label v-for="(label, index) in ['Minute (0–59)', 'Hour (0–23)', 'Day (1–31)', 'Month (1–12)', 'Weekday (0–7)']" :key="index" class="gx-field-group">
                  <span class="gx-row__label">{{ label }}</span>
                  <input v-model="restartCronFields[index]" class="gx-field gx-field--full" type="text" maxlength="24" spellcheck="false"
                    :aria-label="label" :disabled="busy || uncertain || !operations.canConfigure" @input="scheduleTouched=true" />
                </label>
                <label class="gx-field-group">
                  <span class="gx-row__label">Timezone</span>
                  <GalaxySelect v-model="restartTimezone" class="gx-field gx-field--full" aria-label="Schedule timezone"
                    :disabled="busy || uncertain || !operations.canConfigure" @change="scheduleTouched=true">
                    <option v-for="zone in restartTimezones" :key="zone" :value="zone">{{ zone }}</option>
                  </GalaxySelect>
                </label>
              </div>
              <p class="gx-note">Cron: {{ restartCronFields.join(' ') }} · Schedule timezone: {{ restartTimezone }}.<br />Default: daily at local midnight. Uses the selected timezone, including daylight saving time. Use * for every value; ranges, commas and steps are supported. Weekday 0 or 7 is Sunday. Schedule is skipped if onroad.</p>
              <button type="submit" class="gx-btn gx-btn--tonal" :disabled="busy || uncertain || !operations.canConfigure || !restartScheduleChanged">Save restart schedule</button>
            </form>
          </div></details>
        </section>
        <ToggleBackup :unauthorized="unauthorized" />
        <RouteCleanup :unauthorized="unauthorized" />
        <GxDialog v-if="dialog" labelledby="gx-software-status-confirm-title" @close="closeDialog"><h3 id="gx-software-status-confirm-title">{{ dialog.title }}</h3><p>{{ dialog.message }}</p>
          <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="closeDialog">Cancel</button><button type="button" class="gx-btn" @click="confirmDialog">{{ dialog.label }}</button></div>
        </GxDialog>
      </template>
    </div>`,
}
