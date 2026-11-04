import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { GalaxyConfirm } from "../components/GalaxyModal.js"
import { GalaxySection } from "../components/GalaxySection.js"
import { VersionHistoryPicker, versionTitle, releaseVersions } from "../components/VersionHistoryPicker.js"
import { GxNotice } from "../components/GxNotice.js"

function shortCommit(commit) {
  return String(commit || "").slice(0, 10) || "—"
}

function toPercent(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, n))
}
function githubRemoteUrl(remote, commitsUrl = "") {
  let value = String(remote || "").trim()
  if (!value) {
    const commits = String(commitsUrl || "").trim()
    const markerIndex = commits.indexOf("/commits/")
    if (commits.startsWith("https://github.com/") && markerIndex > 0) value = commits.slice(0, markerIndex)
    else return ""
  }
  if (value.startsWith("git@github.com:")) value = `https://github.com/${value.split(":", 2)[1] || ""}`
  else if (value.startsWith("ssh://git@github.com/")) value = `https://github.com/${value.split("ssh://git@github.com/", 2)[1] || ""}`
  else if (value.startsWith("http://github.com/")) value = `https://github.com/${value.split("http://github.com/", 2)[1] || ""}`
  if (!value.startsWith("https://github.com/")) return ""
  value = value.replace(/\/+$/, "")
  return value.endsWith(".git") ? value.slice(0, -4) : value
}

function githubCommitUrl(remote, commitsUrl, commit) {
  const sha = String(commit || "").trim()
  if (!/^[a-f0-9]{7,40}$/i.test(sha)) return ""
  const base = githubRemoteUrl(remote, commitsUrl)
  return base ? `${base}/commit/${sha}` : ""
}

function githubCommitsUrl(remote, commitsUrl, branch) {
  const suppliedUrl = String(commitsUrl || "").trim()
  if (suppliedUrl) {
    try {
      const parsed = new URL(suppliedUrl)
      if (parsed.protocol === "https:" && parsed.hostname === "github.com" && parsed.pathname.includes("/commits/")) {
        return parsed.href
      }
    } catch (e) {
    }
  }

  const base = githubRemoteUrl(remote, commitsUrl)
  const normalizedBranch = String(branch || "").trim()
  if (!base || !normalizedBranch) return ""
  try {
    if (new URL(base).hostname !== "github.com") return ""
  } catch (e) {
    return ""
  }
  return `${base}/commits/${encodeURIComponent(normalizedBranch)}/`
}

const CORE_UPDATE_BRANCHES = ["StarPilot", "Dom"]
const REBOOT_PENDING_STORAGE_KEY = "galaxy-update-reboot-pending"
const LOCAL_DEVICE_SCOPE = "local"
const DEVICE_SLUG_RE = /^[A-Za-z0-9]{16}$/

function rebootStorageKey(scope = LOCAL_DEVICE_SCOPE) {
  const normalizedScope = String(scope || "").trim() || LOCAL_DEVICE_SCOPE
  return REBOOT_PENDING_STORAGE_KEY + ":" + normalizedScope
}

function readRebootMarker(scope = LOCAL_DEVICE_SCOPE) {
  try {
    const raw = localStorage.getItem(rebootStorageKey(scope))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    const startedAt = Number(parsed?.startedAt)
    return Number.isFinite(startedAt) && startedAt > 0 ? startedAt : null
  } catch (e) {
    return null
  }
}

function writeRebootMarker(scope, startedAt) {
  try { localStorage.setItem(rebootStorageKey(scope), JSON.stringify({ startedAt })) } catch (e) {}
}

function clearRebootMarker(scope) {
  try { localStorage.removeItem(rebootStorageKey(scope)) } catch (e) {}
}

async function resolveDeviceScope() {
  try {
    const payload = await api.getGatewayDevices()
    if (!payload) return LOCAL_DEVICE_SCOPE
    return DEVICE_SLUG_RE.test(payload?.activeSlug || "") ? payload.activeSlug : LOCAL_DEVICE_SCOPE
  } catch (e) {
    // Direct/local Galaxy instances do not have the gateway directory endpoint.
    return LOCAL_DEVICE_SCOPE
  }
}

export const SystemTools = {
  name: "SystemTools",
  components: { GalaxySection, GxNotice, VersionHistoryPicker },
  data() {
    return {
      branches: [],
      currentBranch: "",
      targetBranch: "",
      versionMode: "latest",
      selectedCommit: "",
      versionCommits: [],
      versionHead: "",
      versionPage: 0,
      versionHasMore: false,
      versionLoading: false,
      versionError: "",
      versionNotice: "",
      versionGeneration: 0,
      branchLoading: true,
      branchListFallback: false,
      branchListError: "",
      advancedVersionPickerOpen: false,
      otherBranchesOpen: false,
      branchBusy: false,

      isOnroad: false,
      fastStatus: null,
      statusUnavailable: false,
      rebootStorageScope: LOCAL_DEVICE_SCOPE,
      rebootPending: false,
      rebootStartedAt: 0,
      rebootOfflineSeen: false,
      reconnectedNotice: false,
      checkedForUpdates: false,
      busy: "",
      autoUpdateBusy: false,
      profiles: [],
      profileBusy: "",
      tailscaleInstalled: false,
      tailscaleLoaded: false,
      tailscaleBusy: false,
    }
  },
  created() {
    this.rebootScopeCancelled = false
    this.rebootReady = resolveDeviceScope().then((scope) => {
      if (this.rebootScopeCancelled) return
      this.rebootStorageScope = scope
      const marker = readRebootMarker(scope)
      this.rebootPending = !!marker
      this.rebootStartedAt = marker || 0
    }).finally(() => {
      if (this.rebootScopeCancelled) return
      this.poll = usePolling(() => this.loadFastStatus(), {
        interval: 1000,
        enabled: () => this.statusPollingNeeded,
      })
      this.poll.start()
    })
  },
  mounted() {
    this.rebootReady.then(() => {
      if (this.rebootScopeCancelled) return
      this.loadBranches()
      this.loadProfiles()
      this.loadTailscale()
    })
  },
  beforeUnmount() {
    this.rebootScopeCancelled = true
    this.poll?.destroy()
    this.resetVersions()
  },
  computed: {
    primaryBranchValue() {
      if (this.otherBranchesOpen) return "other:"
      return ["StarPilot", "Dom"].includes(this.targetBranch) ? this.targetBranch : ""
    },
    otherBranches() {
      const branches = this.branches.filter(branch => !["StarPilot", "Dom"].includes(branch))
      if (this.currentBranch && !["StarPilot", "Dom"].includes(this.currentBranch) && !branches.includes(this.currentBranch)) {
        branches.unshift(this.currentBranch)
      }
      return branches
    },
    branchSwitchBlocked() {
      return this.branchLoading || this.isOnroad || !!this.fastStatus?.isOnroad || this.updateInProgress || !!this.busy
    },
    statusRebooting() { return String(this.fastStatus?.stage || "").trim().toLowerCase() === "rebooting" },
    updateNoticeTitle() {
      return this.statusRebooting ? "Device rebooting" : this.statusUnavailable ? "Reconnecting to device" : "Update in progress"
    },
    updateNoticeText() {
      if (this.statusRebooting) return "Installation finished. Waiting for the device to reconnect…"
      if (this.statusUnavailable) return "Connection interrupted. Galaxy is retrying; update completion has not been confirmed."
      return "Installation is in progress. The device will reboot when it finishes."
    },
    updateInProgress() { return !!this.fastStatus?.running || this.statusRebooting || this.rebootPending },
    statusPollingNeeded() { return this.statusUnavailable || !this.fastStatus || this.updateInProgress || this.rebootPending },
    versionChoices() { return this.targetBranch === "StarPilot" ? releaseVersions(this.versionCommits) : this.versionCommits },
    installVersionBlocked() {
      return this.branchSwitchBlocked || this.branchBusy || !this.branches.includes(this.targetBranch) ||
        (this.versionMode === "earlier" && (this.versionLoading || !/^[a-f0-9]{40}$/.test(this.selectedCommit) || !this.versionChoices.some(commit => commit.sha === this.selectedCommit)))
    },
    updateAvailable() { return this.checkedForUpdates && !!this.fastStatus?.updateAvailable && !this.updateInProgress },
    remoteCommitUrl() { return githubCommitUrl(this.fastStatus?.originRemote, this.fastStatus?.commitsUrl, this.fastStatus?.remoteCommit) },
    recentCommitsUrl() { return githubCommitsUrl(this.fastStatus?.originRemote, this.fastStatus?.commitsUrl, this.fastStatus?.branch || this.currentBranch) },
    factoryResetStatus() {
      const s = this.fastStatus
      if (!s || String(s?.lastMode || "").trim() !== "factory-reset") return null
      return {
        running: !!s.running,
        stage: String(s.stage || "idle"),
        message: String(s.message || ""),
        lastError: String(s.lastError || ""),
        progressLabel: String(s.progressLabel || ""),
        progressDetail: String(s.progressDetail || ""),
        progressPercent: toPercent(s.progressPercent),
        progressStep: Number(s.progressStep || 0),
        progressTotalSteps: Number(s.progressTotalSteps || 5),
      }
    },
  },
  methods: {
    shortCommit,
    toPercent,
    async loadBranches() {
      try {
        const data = await api.getUpdateBranches()
        const branches = [...new Set(data.branches.map(branch => String(branch || "").trim()).filter(Boolean))]
        if (!branches.length) throw new Error("No update branches were returned.")
        this.branches = branches
        this.currentBranch = String(data?.currentBranch || "").trim()
        this.branchListFallback = false
        this.branchListError = ""
        if (!this.targetBranch) {
          this.targetBranch = this.currentBranch
          this.otherBranchesOpen = !!this.targetBranch && !["StarPilot", "Dom"].includes(this.targetBranch)
        }
        this.isOnroad = !!data?.isOnroad
      } catch (e) {
        this.branchListError = e?.message || "Update branch list unavailable."
        try {
          if (!this.fastStatus) await this.loadFastStatus({ throwOnError: true })
        } catch (statusError) {
          this.fastStatus = null
        }
        const current = String(this.fastStatus?.branch || this.currentBranch || "").trim()
        this.currentBranch = current
        this.branches = [...new Set([current, ...CORE_UPDATE_BRANCHES].filter(Boolean))]
        this.branchListFallback = true
        if (!this.targetBranch) {
          this.targetBranch = current || "Dom"
          this.otherBranchesOpen = !!this.targetBranch && !CORE_UPDATE_BRANCHES.includes(this.targetBranch)
        }
        showSnackbar("Could not refresh the full branch list. Standard branches are still available.", "error")
      } finally {
        this.branchLoading = false
      }
    },
    async loadFastStatus({ throwOnError = false } = {}) {
      try {
        const status = await api.getUpdateFastStatus()
        if (!status) throw new Error("Update status unavailable")
        const stage = String(status.stage || "").trim().toLowerCase()
        if (stage === "rebooting" && !this.rebootPending) {
          this.rebootPending = true
          this.rebootStartedAt = Date.now()
          writeRebootMarker(this.rebootStorageScope, this.rebootStartedAt)
        }
        const pendingAge = this.rebootStartedAt ? Date.now() - this.rebootStartedAt : 0
        const deviceReturned = this.rebootPending && !status.running && stage !== "rebooting" &&
          (this.statusUnavailable || pendingAge >= 30_000)
        const updateFailed = this.rebootPending && stage === "error"
        this.fastStatus = status
        this.statusUnavailable = false
        this.isOnroad = !!status.isOnroad
        if (updateFailed) this.clearRebootPending(false)
        else if (deviceReturned) this.clearRebootPending()
      } catch (e) {
        this.statusUnavailable = true
        if (this.rebootPending) this.rebootOfflineSeen = true
        if (throwOnError) throw e
      }
    },
    markRebootPending() {
      this.rebootPending = true
      this.rebootStartedAt = Date.now()
      this.rebootOfflineSeen = false
      this.reconnectedNotice = false
      writeRebootMarker(this.rebootStorageScope, this.rebootStartedAt)
    },
    clearRebootPending(showNotice = true) {
      this.rebootPending = false
      this.rebootStartedAt = 0
      this.rebootOfflineSeen = false
      clearRebootMarker(this.rebootStorageScope)
      if (showNotice) this.reconnectedNotice = true
    },
    async backupToggles() {
      try {
        const blob = await api.backupToggles()
        const url = URL.createObjectURL(blob)
        const a = document.createElement("a")
        a.href = url
        a.download = "toggle-backup.json"
        a.click()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
        showSnackbar("Toggle backup downloaded.")
      } catch (e) {
        showSnackbar(e?.message || "Backup failed.", "error")
      }
    },
    async loadProfiles() {
      try {
        const data = await api.getToggleProfiles()
        this.profiles = Array.isArray(data?.slots) ? data.slots : []
        this.isOnroad = !!data?.isOnroad
      } catch (e) {
        this.profiles = []
      }
    },
    async saveProfile(profile) {
      if (this.profileBusy || this.isOnroad) return
      if (profile.saved && !(await GalaxyConfirm({
        title: `Overwrite ${profile.label}?`,
        message: "This replaces the settings currently stored in this slot.",
        confirmLabel: "Overwrite",
      }))) return
      this.profileBusy = `save-${profile.slot}`
      try {
        const result = await api.saveToggleProfile(profile.slot)
        showSnackbar(result?.message || `Saved ${profile.label}.`)
        await this.loadProfiles()
      } catch (e) {
        showSnackbar(e?.message || "Failed to save settings profile.", "error")
      } finally {
        this.profileBusy = ""
      }
    },
    async loadProfile(profile) {
      if (this.profileBusy || this.isOnroad || !profile.saved || profile.invalid) return
      if (!(await GalaxyConfirm({
        title: `Load ${profile.label}?`,
        message: "This applies every saved setting in the slot to the device.",
        confirmLabel: "Load Settings",
      }))) return
      this.profileBusy = `load-${profile.slot}`
      try {
        const result = await api.loadToggleProfile(profile.slot)
        showSnackbar(result?.message || `Loaded ${profile.label}.`)
      } catch (e) {
        showSnackbar(e?.message || "Failed to load settings profile.", "error")
      } finally {
        this.profileBusy = ""
      }
    },
    onRestoreFile(e) {
      const file = e.target.files[0]
      e.target.value = ""
      if (!file) return
      if (file.size > 5_000_000) { showSnackbar("That toggle backup file is too large.", "error"); return }
      file.text().then((text) => {
        let data
        try { data = JSON.parse(text) } catch { showSnackbar("That file is not a valid toggle backup.", "error"); return }
        if (!data || typeof data !== "object" || Array.isArray(data)) { showSnackbar("That file is not a valid toggle backup.", "error"); return }
        api.restoreToggles(data).then((res) => {
          showSnackbar(res?.message || "Toggles restored!")
        }).catch((err) => showSnackbar(err?.message || "Failed to restore toggles.", "error"))
      })
    },
    async resetDefault() {
      if (!(await GalaxyConfirm({ title: "Reset toggles to default?", message: "This resets all toggles to their default values and reboots.", confirmLabel: "Reset", danger: true }))) return
      try {
        await api.resetTogglesDefault()
        this.markRebootPending()
        showSnackbar("Resetting toggles to default... rebooting.")
      } catch (e) {
        showSnackbar("Reset failed.", "error")
      }
    },
    onPrimaryBranchSelect(e) {
      const branch = e.target.value
      if (this.branchSwitchBlocked || this.branchBusy) return
      this.otherBranchesOpen = branch === "other:"
      if (this.otherBranchesOpen) {
        // Other is navigation, never an install target.
        this.targetBranch = ""
        this.resetVersions()
      } else this.selectTargetBranch(branch)
    },
    onBranchSelect(e) { this.selectTargetBranch(e.target.value) },
    selectTargetBranch(branch) {
      if (!branch || !this.branches.includes(branch) || this.branchBusy || this.branchSwitchBlocked) return
      if (branch === this.targetBranch) return
      this.targetBranch = branch
      this.otherBranchesOpen = !["StarPilot", "Dom"].includes(branch)
      this.resetVersions()
    },
    resetVersions() {
      this.versionAbort?.abort()
      this.versionAbort = null
      this.versionGeneration++
      this.versionMode = "latest"
      this.selectedCommit = ""
      this.versionCommits = []
      this.versionHead = ""
      this.versionPage = 0
      this.versionHasMore = false
      this.versionLoading = false
      this.versionError = ""
      this.versionNotice = ""
    },
    async onVersionModeSelect(e) {
      if (this.branchSwitchBlocked || this.branchBusy) return
      const mode = e.target.value
      if (!["latest", "earlier"].includes(mode)) return
      this.resetVersions()
      this.versionMode = mode
      if (mode === "earlier") await this.loadVersions()
    },
    async loadVersions(more = false) {
      if (!this.targetBranch || !this.branches.includes(this.targetBranch) || this.versionLoading || this.versionMode !== "earlier" || (more && !this.versionHasMore)) return
      const branch = this.targetBranch
      const generation = this.versionGeneration
      let page = more ? this.versionPage + 1 : 1
      let head = more ? this.versionHead : ""
      const controller = new AbortController()
      this.versionAbort = controller
      this.versionLoading = true
      this.versionError = ""
      const originalCount = this.versionChoices.length
      const budget = more && branch === "StarPilot" ? 4 : 1
      try {
        for (let scanned = 0; scanned < budget; scanned++) {
          const data = await api.getUpdateVersions(branch, {page, head, signal: controller.signal})
          if (generation !== this.versionGeneration || controller.signal.aborted) return
          if (data?.branch !== branch || data?.page !== page || !/^[a-f0-9]{40}$/.test(data?.head || "") || (head && data.head !== head) || !Array.isArray(data?.commits)) throw new Error("Version history changed. Choose Latest and try again.")
          const commits = data.commits.filter(commit => /^[a-f0-9]{40}$/.test(commit?.sha || ""))
          this.versionCommits = page > 1 ? [...this.versionCommits, ...commits.filter(commit => !this.versionCommits.some(existing => existing.sha === commit.sha))] : commits
          if (data.cached) {
            const saved = new Date(data.cachedAt)
            const when = Number.isFinite(saved.getTime()) ? " from " + saved.toLocaleString() : ""
            this.versionNotice = "Showing saved history" + when + ". Installation still needs an online check."
          } else if (page === 1) this.versionNotice = ""
          this.versionHead = data.head
          this.versionPage = page
          this.versionHasMore = !!data.hasMore && commits.length > 0
          if (!this.versionCommits.length) this.versionError = "No versions are available for this branch. Choose Latest or another branch."
          if (!this.versionHasMore || this.versionChoices.length > originalCount) break
          page++
          head = this.versionHead
        }
      } catch (e) {
        if (generation !== this.versionGeneration || controller.signal.aborted) return
        this.versionError = e?.message || "Failed to load version history. Try again."
      } finally {
        if (generation === this.versionGeneration) {
          this.versionLoading = false
          this.versionAbort = null
        }
      }
    },
    versionDate(date) {
      const value = new Date(date)
      return Number.isNaN(value.getTime()) ? "Date unavailable" : value.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"})
    },
    async returnToLatest() {
      const branch = this.fastStatus?.versionPin?.branch
      if (this.branchSwitchBlocked || this.branchBusy || !this.branches.includes(branch)) return
      this.selectTargetBranch(branch)
      this.resetVersions()
      await this.installSelectedVersion()
    },
    async installSelectedVersion() {
      if (this.installVersionBlocked) return
      const branch = this.targetBranch
      const commit = this.versionMode === "latest" ? "latest" : this.selectedCommit
      const generation = this.versionGeneration
      this.branchBusy = true
      try {
        const selected = this.versionCommits.find(item => item.sha === commit)
        const version = commit === "latest" ? "Latest" : `${versionTitle(selected, branch === "StarPilot")}\nCommit: ${commit}`
        const policy = commit === "latest" ? "This uses the normal branch updater, including any required OS update during startup. Your automatic-update setting is unchanged." : "Automatic updates will be paused for this earlier version."
        const recovery = commit === "latest"
          ? "This replaces the current software. Settings and statistics are kept. Local code changes may be overwritten; standard Rollback saves the previous committed version."
          : "This replaces the current software. Settings and statistics are kept, and local code changes are backed up. Older versions may remove this picker; an SSH recovery copy is saved on the device."
        if (!(await GalaxyConfirm({title: "Install selected version?", message: `Branch: ${branch}\nVersion: ${version}\n\n${policy}\n\n${recovery}\n\nYour device will reboot when installation finishes.`, confirmLabel: "Install & Reboot", danger: true}))) return
        // Refresh driving/updater state after the user has reviewed the target.
        await this.loadFastStatus({throwOnError: true})
        if (this.branchSwitchBlocked || this.targetBranch !== branch || generation !== this.versionGeneration) {
          showSnackbar("Installation is unavailable while driving or updating, or the selection has changed.", "error")
          return
        }
        const result = await api.installUpdateVersion(branch, commit)
        this.markRebootPending()
        showSnackbar(result?.message || `Installing ${version} on ${branch}...`)
        await this.loadFastStatus()
      } catch (e) {
        showSnackbar(e?.message || "Installation failed.", "error")
      } finally {
        this.branchBusy = false
      }
    },
    async checkUpdates() {
      if (this.busy || this.updateInProgress) return
      this.busy = "check"
      try {
        await this.loadFastStatus({ throwOnError: true })
        this.checkedForUpdates = true
        const st = this.fastStatus
        if (this.updateInProgress) showSnackbar("An update is already running.")
        else if (st?.updateAvailable) showSnackbar(st?.message || "Update available.")
        else showSnackbar(st?.message || "No update available — you're up to date.")
      } catch (e) {
        showSnackbar("Failed to check for updates.", "error")
      } finally {
        this.busy = ""
      }
    },
    async setAutomaticUpdates(enabled) {
      if (this.autoUpdateBusy || this.isOnroad || this.updateInProgress || !this.fastStatus) return
      const previous = !!this.fastStatus.automaticUpdates
      this.autoUpdateBusy = true
      this.fastStatus = { ...this.fastStatus, automaticUpdates: !!enabled }
      try {
        const payload = await api.updateParam({ key: "AutomaticUpdates", value: !!enabled, label: "Automatic Updates" })
        showSnackbar(payload?.message || `Automatic updates ${enabled ? "enabled" : "disabled"}.`)
      } catch (e) {
        this.fastStatus = { ...this.fastStatus, automaticUpdates: previous }
        showSnackbar(e?.message || "Failed to update Automatic Updates.", "error")
      } finally {
        this.autoUpdateBusy = false
      }
    },
    async applyFastUpdate() {
      if (this.busy || this.isOnroad) return
      if (this.updateInProgress) { showSnackbar("Fast update is already running."); return }
      if (!this.checkedForUpdates || !this.updateAvailable) {
        showSnackbar("No update available. Run \"Check for Updates\" first.", "error")
        return
      }
      const st = this.fastStatus
      const confirmed = await GalaxyConfirm({
        title: "Update available",
        message: `Fast update to the latest commit on ${st?.branch || "this branch"}.\n\nYour device will reboot when the update is done.`,
        confirmLabel: "Update & Reboot",
        danger: true,
      })
      if (!confirmed) return
      await this.runUpdate("fast")
    },
    async runUpdate(action) {
      if (this.busy || this.updateInProgress) return
      this.busy = action
      try {
        if (action === "rollback") {
          const st = this.fastStatus
          if (st && !st.rollbackAvailable) {
            showSnackbar("No previous installed version is available to roll back to.", "error")
            return
          }
        }
        if (action !== "fast") {
          const actionLabels = { recover: "Recover the interrupted update?", rollback: "Roll back to the previous installed version?" }
          if (!(await GalaxyConfirm({ title: actionLabels[action] || "Continue?", message: "Your device will reboot when the operation is done.", confirmLabel: "Continue", danger: true }))) return
        }
        const fn = action === "fast" ? api.updateFast : action === "recover" ? api.updateRecover : api.updateRollback
        const payload = await fn()
        this.markRebootPending()
        showSnackbar(payload?.message || "Update started.")
        await this.loadFastStatus()
      } catch (e) {
        showSnackbar(e?.message || "Update failed.", "error")
      } finally {
        this.busy = ""
      }
    },
    async factoryReset() {
      if (!(await GalaxyConfirm({ title: "Factory reset (SAVE ME)?", message: "This wipes params, backups, themes, models, maps, and route data, then reboots. This cannot be undone.", confirmLabel: "Factory Reset", danger: true }))) return
      try {
        await api.factoryReset()
        this.markRebootPending()
        showSnackbar("SAVE ME initiated — factory resetting...")
        await this.loadFastStatus()
      } catch (e) {
        showSnackbar(e?.message || "Factory reset failed.", "error")
      }
    },
    async deleteAllDrivingRoutes() {
      if (!(await GalaxyConfirm({ title: "Delete All Driving Routes", message: "This permanently deletes all local routes from standard, high-resolution, and alternate footage storage. It does not reset settings or reboot the device.", confirmLabel: "Delete Routes", danger: true }))) return
      try {
        const payload = await api.deleteAllRoutes(true)
        showSnackbar(payload?.message || "All local driving routes deleted.")
      } catch (e) {
        showSnackbar(e?.message || "Failed to delete driving routes.", "error")
      }
    },
    async loadTailscale() {
      try {
        const data = await api.getTailscaleInstalled()
        this.tailscaleInstalled = !!data?.installed
      } catch (e) {
        this.tailscaleInstalled = false
      } finally {
        this.tailscaleLoaded = true
      }
    },
    async installTailscale() {
      if (this.tailscaleBusy) return
      this.tailscaleBusy = true
      showSnackbar("Install started...")
      try {
        const result = await api.setupTailscale()
        showSnackbar(result?.message || "Tailscale setup started.")
        if (result?.auth_url) window.open(result.auth_url, "_blank", "noopener")
        await this.loadTailscale()
      } catch (e) {
        showSnackbar(e?.message || "Failed to install Tailscale.", "error")
      } finally {
        this.tailscaleBusy = false
      }
    },
    async uninstallTailscale() {
      if (this.tailscaleBusy) return
      if (!(await GalaxyConfirm({
        title: "Uninstall Tailscale?",
        message: "This disconnects the device from your tailnet and removes the Tailscale binaries and state.",
        confirmLabel: "Uninstall",
        danger: true,
      }))) return
      this.tailscaleBusy = true
      showSnackbar("Uninstall started...")
      try {
        const result = await api.uninstallTailscale()
        showSnackbar(result?.message || "Tailscale uninstalled.")
        await this.loadTailscale()
      } catch (e) {
        showSnackbar(e?.message || "Failed to uninstall Tailscale.", "error")
      } finally {
        this.tailscaleBusy = false
      }
    },
  },
  template: `
    <div>
      <h2 style="margin-top:0;">System Tools</h2>

      <GalaxySection title="Software & Updates" icon="bi-arrow-up-circle" :collapsible="false">
        <div style="padding: var(--sp-3);">
          <div v-if="branchLoading" class="gx-loading">Loading update info...</div>
          <template v-else>
            <GxNotice v-if="isOnroad" text="Updates and branch switching are only available while offroad." style="margin-bottom:12px;" />
            <GxNotice v-if="rebootPending" tone="info" icon="bi-arrow-repeat gx-spin" :title="updateNoticeTitle"
              :text="updateNoticeText" />
            <GxNotice v-else-if="reconnectedNotice" tone="info" icon="bi-check-circle-fill" title="Device reconnected"
              text="Galaxy is connected again and the update status is current." />

            <div v-if="fastStatus" class="gx-card" style="margin-bottom:12px;">
              <div class="gx-section__header">
                <i class="bi bi-arrow-repeat"></i>
                <span class="gx-section__title">Update Status</span>
                <span v-if="updateInProgress" class="gx-chip" style="background:var(--primary);color:var(--on-primary);">{{ statusUnavailable || statusRebooting ? 'Reconnecting…' : fastStatus.progressPercent + '%' }}</span>
                <span v-else-if="updateAvailable" class="gx-chip" style="background:var(--warning);color:var(--black);">Update available</span>
                <span v-else-if="checkedForUpdates" class="gx-chip">Up to date</span>
                <span v-else class="gx-chip">Not checked</span>
              </div>
              <div style="padding: var(--sp-3); display:grid; gap:6px;">
                <div class="gx-row" style="border-top:none; min-height:0; padding:4px 0;"><span class="gx-row__label">Installed branch</span><span class="gx-row__value">{{ fastStatus.branch || currentBranch || '—' }}</span></div>
                <div v-if="updateInProgress && !statusRebooting && !statusUnavailable" class="gx-row" style="border-top:none; min-height:0; padding:4px 0;"><span class="gx-row__label">Stage</span><span class="gx-row__value">{{ fastStatus.stage }} · {{ fastStatus.progressLabel }}</span></div>
                <div class="gx-row" style="border-top:none; min-height:0; padding:4px 0;"><span class="gx-row__label">Local</span><span class="gx-row__value" style="font-family:monospace;">{{ shortCommit(fastStatus.localCommit) }}</span></div>
                <div class="gx-row" style="border-top:none; min-height:0; padding:4px 0;"><span class="gx-row__label">Remote</span><span class="gx-row__value" style="font-family:monospace;"><a v-if="remoteCommitUrl" class="gx-link" :href="remoteCommitUrl" target="_blank" rel="noopener noreferrer" :title="fastStatus.remoteCommit">{{ shortCommit(fastStatus.remoteCommit) }}</a><template v-else>{{ shortCommit(fastStatus.remoteCommit) }}</template></span></div>
                <a v-if="recentCommitsUrl" class="gx-btn gx-btn--tonal" style="justify-self:start; margin-top:4px;" :href="recentCommitsUrl" target="_blank" rel="noopener noreferrer"><i class="bi bi-github" aria-hidden="true"></i> Show recent commits on GitHub</a>
                <div v-if="updateInProgress && !statusRebooting && !statusUnavailable" class="gx-update-progress" role="progressbar" aria-label="Update progress"
                  :aria-valuenow="Math.round(fastStatus.progressPercent || 0)" aria-valuemin="0" aria-valuemax="100">
                  <div class="gx-update-progress__track">
                    <div class="gx-update-progress__fill" :class="{ 'gx-update-progress__fill--error': fastStatus.stage === 'error' }"
                      :style="{ width: toPercent(fastStatus.progressPercent) + '%' }"></div>
                  </div>
                  <div class="gx-update-progress__meta">
                    <span>Step {{ fastStatus.progressStep || 0 }}/{{ fastStatus.progressTotalSteps || 5 }}: {{ fastStatus.progressLabel || fastStatus.stage || 'Updating' }}</span>
                    <strong>{{ Math.round(toPercent(fastStatus.progressPercent)) }}%</strong>
                  </div>
                  <small v-if="fastStatus.progressDetail">{{ fastStatus.progressDetail }}</small>
                </div>
                <div v-if="fastStatus.message" class="gx-note">{{ fastStatus.message }}</div>
                <div v-if="statusUnavailable" class="gx-note">Waiting for the device to reconnect. The last update status is being kept on screen.</div>
                <div v-if="fastStatus.warning && (updateInProgress || fastStatus.updateAvailable)" class="gx-note gx-note--danger">{{ fastStatus.warning }}</div>
                <div v-if="fastStatus.agnosUpdate?.available && fastStatus.agnosUpdate?.warnings?.length" style="margin-top:4px;">
                  <div v-for="w in fastStatus.agnosUpdate.warnings" :key="w" class="gx-note gx-note--danger"><i class="bi bi-exclamation-triangle-fill"></i> {{ w }}</div>
                </div>
              </div>
            </div>

            <div class="gx-card" style="margin-bottom:12px;">
              <div class="gx-row" style="border-top:none;">
                <div class="gx-row__info">
                  <span class="gx-row__label">Automatically Install Updates</span>
                  <span class="gx-row__desc">Install updates automatically while parked with an active internet connection.</span>
                </div>
                <label class="gx-switch">
                  <input type="checkbox" :checked="!!fastStatus?.automaticUpdates"
                    :disabled="!fastStatus || isOnroad || autoUpdateBusy || updateInProgress"
                    @change="setAutomaticUpdates($event.target.checked)" />
                  <span class="gx-switch__track"></span>
                  <span class="gx-switch__thumb"></span>
                </label>
              </div>
            </div>

            <details class="gx-card" style="margin-bottom:12px;" @toggle="advancedVersionPickerOpen = $event.target.open">
              <summary class="gx-section__header" style="list-style:none;">
                <i class="bi bi-sliders"></i><span class="gx-section__title">Advanced update options</span>
                <i class="bi" :class="advancedVersionPickerOpen ? 'bi-chevron-up' : 'bi-chevron-down'" aria-hidden="true"></i>
              </summary>
              <div v-if="advancedVersionPickerOpen">
                <div class="gx-section__header" style="cursor:default;"><i class="bi bi-git-branch"></i><span class="gx-section__title">Install a Version</span></div>
                <div style="padding: var(--sp-3);">
                  <p class="gx-note" style="margin-top:0; overflow-wrap:anywhere;">Most people should stay on the latest version. Use this only to install a specific branch or historical version while troubleshooting.</p>
                <p class="gx-note" style="margin-top:0; overflow-wrap:anywhere;">Installed branch: <strong>{{ currentBranch || 'Unknown' }}</strong></p>
                <GalaxySelect id="gx-primary-branch" class="gx-field gx-field--full" aria-label="Target branch"
                  :value="primaryBranchValue" :disabled="branchSwitchBlocked || branchBusy" @change="onPrimaryBranchSelect">
                  <option value="" disabled>Select a branch</option>
                  <option value="StarPilot" data-collapsed-label="StarPilot" data-description="Stable releases. Recommended for most users.">StarPilot — Release</option>
                  <option value="Dom" data-collapsed-label="Dom" data-description="Latest features and fixes under development. Updates regularly and may introduce bugs.">Dom — Development</option>
                  <option value="other:">Other branches…</option>
                </GalaxySelect>
                <div v-if="otherBranchesOpen" style="margin-top:var(--sp-3); padding-left:var(--sp-3); border-left:2px solid var(--outline-variant);">
                  <label for="gx-other-branch" class="gx-row__label">Other branches</label>
                  <p class="gx-note">Additional branches from this installation's repository.</p>
                  <GalaxySelect id="gx-other-branch" class="gx-field gx-field--full" aria-label="Other branches"
                    :value="otherBranches.includes(targetBranch) ? targetBranch : ''" :disabled="branchSwitchBlocked || branchBusy" @change="onBranchSelect">
                    <option value="" disabled>{{ otherBranches.length ? 'Select another branch' : 'No other branches available' }}</option>
                    <option v-for="b in otherBranches" :key="b" :value="b">{{ b === currentBranch ? b + ' (current)' : b }}</option>
                  </GalaxySelect>
                </div>
                <p v-if="branchListFallback" class="gx-note">The full branch list is temporarily unavailable. Standard branches are shown; selecting a version still verifies it with the device.</p>
                <div v-if="targetBranch" style="margin-top:var(--sp-3); display:grid; gap:8px; min-width:0;">
                  <label for="gx-version-mode" class="gx-row__label">Version</label>
                  <GalaxySelect id="gx-version-mode" class="gx-field gx-field--full" aria-label="Version" :value="versionMode"
                    :disabled="branchSwitchBlocked || branchBusy || !branches.includes(targetBranch)" @change="onVersionModeSelect">
                    <option value="latest">Latest</option>
                    <option value="earlier">Choose earlier…</option>
                  </GalaxySelect>
                  <template v-if="versionMode === 'earlier'">
                    <VersionHistoryPicker id="gx-version-commit" :value="selectedCommit" :commits="versionCommits"
                      :release-branch="targetBranch === 'StarPilot'" :loading="versionLoading" :has-more="versionHasMore" :error="versionError" :notice="versionNotice"
                      :disabled="branchSwitchBlocked || branchBusy" @change="selectedCommit = $event.target.value"
                      @loadmore="loadVersions(versionCommits.length > 0 && versionHasMore)" />
                  </template>
                  <p v-if="!branches.includes(targetBranch)" class="gx-note">This installed branch is no longer listed by the repository. Select an available target branch to install a version.</p>
                  <button type="button" class="gx-btn" :disabled="installVersionBlocked" @click="installSelectedVersion">
                    <i v-if="branchBusy" class="bi bi-arrow-repeat gx-spin"></i>{{ branchBusy ? 'Starting installation…' : 'Install selected version' }}
                  </button>
                </div>
                <div v-if="fastStatus?.versionPin" class="gx-note" style="margin-top:var(--sp-3); overflow-wrap:anywhere;">
                  <p>Pinned version: <strong>{{ fastStatus.versionPin.branch }} · {{ shortCommit(fastStatus.versionPin.commit) }}</strong><br>Automatic updates were paused at installation.</p>
                  <button type="button" class="gx-btn gx-btn--tonal" :disabled="branchSwitchBlocked || branchBusy || !branches.includes(fastStatus.versionPin.branch)" @click="returnToLatest">Return to Latest</button>
                </div>
                </div>
              </div>
            </details>

            <div style="display:flex; gap:8px; margin-top:12px; flex-wrap:wrap;">
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!busy || isOnroad || updateInProgress" @click="checkUpdates">
                <i v-if="busy === 'check'" class="bi bi-arrow-repeat gx-spin"></i>
                <i v-else class="bi bi-search"></i> {{ busy === 'check' ? 'Checking...' : 'Check for Updates' }}
              </button>
              <button v-if="updateAvailable" type="button" class="gx-btn" :disabled="!!busy || isOnroad" @click="applyFastUpdate">
                <i class="bi bi-arrow-up-circle"></i> {{ busy === 'fast' ? 'Updating...' : 'Update Now' }}
              </button>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!busy || isOnroad || updateInProgress" @click="runUpdate('recover')">Recover</button>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!busy || isOnroad || updateInProgress" @click="runUpdate('rollback')">Rollback</button>
            </div>
            <p class="gx-note">Check for Updates scans for a newer commit. Use <strong>Update Now</strong> to install it.</p>
            <p class="gx-note"><strong>Recover</strong> continues an update that was interrupted (for example, by power loss mid-install). <strong>Rollback</strong> returns the device to the previously installed version if the current one has a problem.</p>
            <p v-if="checkedForUpdates && !updateAvailable && !updateInProgress" class="gx-note">
              The device is up to date. Update becomes available only after a check finds a newer commit.
            </p>
          </template>
        </div>
      </GalaxySection>

      <GalaxySection title="Backup & Restore" icon="bi-arrow-repeat" :collapsible="false">
        <div style="padding: var(--sp-3);">
          <h4 style="margin:0 0 4px;">Settings Profiles</h4>
          <p class="gx-note" style="margin:0 0 10px;">Keep two local configurations for different vehicles, drivers, or troubleshooting. Profiles never include pairing or sensitive device data.</p>
          <GxNotice v-if="isOnroad" text="Park the vehicle to save or load a profile." style="margin-bottom:12px;" />
          <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:10px; margin-bottom:16px;">
            <div v-for="profile in profiles" :key="profile.slot" class="gx-card" style="padding:12px;">
              <div style="display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:10px;">
                <strong>{{ profile.label }}</strong>
                <span class="gx-chip">{{ profile.invalid ? 'Damaged' : profile.saved ? profile.settingsCount + ' settings' : 'Empty' }}</span>
              </div>
              <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!profileBusy || isOnroad" @click="saveProfile(profile)">
                  <i class="bi bi-save"></i> {{ profileBusy === 'save-' + profile.slot ? 'Saving...' : profile.saved ? 'Overwrite' : 'Save Current' }}
                </button>
                <button type="button" class="gx-btn" :disabled="!!profileBusy || isOnroad || !profile.saved || profile.invalid" @click="loadProfile(profile)">
                  <i class="bi bi-arrow-down-circle"></i> {{ profileBusy === 'load-' + profile.slot ? 'Loading...' : 'Load' }}
                </button>
              </div>
            </div>
          </div>
        </div>
        <div style="padding: var(--sp-3); display:flex; gap:8px; flex-wrap:wrap;">
          <button type="button" class="gx-btn" @click="backupToggles"><i class="bi bi-download"></i> Backup Toggles</button>
          <button type="button" class="gx-btn gx-btn--tonal" @click="$refs.restoreInput.click()"><i class="bi bi-upload"></i> Restore Toggles</button>
          <button type="button" class="gx-btn gx-btn--tonal" @click="resetDefault">Reset to Default</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="deleteAllDrivingRoutes">Delete All Driving Routes</button>
          <input ref="restoreInput" type="file" accept=".json" style="display:none;" @change="onRestoreFile" />
        </div>
        <p class="gx-note" style="padding: 0 var(--sp-4);">Backup downloads your toggle settings as a JSON file. Restore re-applies one, and Reset to Default clears them back to stock and reboots.</p>

        <div v-if="factoryResetStatus" style="padding: var(--sp-3); border-top: 1px solid var(--border-color, rgba(255,255,255,.08));">
          <div class="gx-section__header">
            <i class="bi bi-arrow-repeat" :class="{ 'gx-spin': factoryResetStatus.running }"></i>
            <span class="gx-section__title">Factory Reset Status</span>
            <span class="gx-chip">{{ factoryResetStatus.progressStep }}/{{ factoryResetStatus.progressTotalSteps }}{{ factoryResetStatus.progressLabel ? ' · ' + factoryResetStatus.progressLabel : '' }}</span>
          </div>
          <div style="padding: var(--sp-3);">
            <div style="height:8px; border-radius:999px; background:var(--surface, rgba(255,255,255,.1)); overflow:hidden;">
              <div :style="{ height: '100%', width: factoryResetStatus.progressPercent + '%', background: factoryResetStatus.stage === 'error' ? 'var(--error)' : 'var(--primary)', transition: 'width .4s' }"></div>
            </div>
            <p v-if="factoryResetStatus.message" style="margin:8px 0 0;">{{ factoryResetStatus.message }}</p>
            <p v-if="factoryResetStatus.progressDetail" class="gx-note" style="margin:4px 0 0;">{{ factoryResetStatus.progressDetail }}</p>
            <p v-if="factoryResetStatus.lastError" class="gx-note gx-note--danger" style="margin:4px 0 0;">Last Error: {{ factoryResetStatus.lastError }}</p>
          </div>
        </div>
      </GalaxySection>

      <GalaxySection title="Tailscale" icon="bi-shield-lock" :collapsible="false">
        <div style="padding: var(--sp-3); display:grid; gap:12px;">
          <p class="gx-note" style="margin:0;">
            Tailscale creates a secure, private connection between your openpilot device and your phone or PC so you can access and control it from anywhere!
          </p>
          <GxNotice v-if="!tailscaleLoaded" tone="info" icon="bi-arrow-repeat" text="Checking Tailscale install status..." />
          <template v-else>
            <GxNotice tone="warn" text="Not recommended. Using Galaxy Tunnel is the preferred remote connection method." />
            <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
              <button v-if="!tailscaleInstalled" type="button" class="gx-btn" :disabled="tailscaleBusy" @click="installTailscale">
                <i class="bi" :class="tailscaleBusy ? 'bi-arrow-repeat gx-spin' : 'bi-download'"></i>
                {{ tailscaleBusy ? 'Installing...' : 'Install Tailscale' }}
              </button>
              <button v-else type="button" class="gx-btn gx-btn--danger" :disabled="tailscaleBusy" @click="uninstallTailscale">
                <i class="bi" :class="tailscaleBusy ? 'bi-arrow-repeat gx-spin' : 'bi-trash'"></i>
                {{ tailscaleBusy ? 'Uninstalling...' : 'Uninstall Tailscale' }}
              </button>
              <a class="gx-btn gx-btn--tonal" href="https://tailscale.com/download" target="_blank" rel="noopener">
                <i class="bi bi-box-arrow-up-right"></i> Download for your other devices
              </a>
            </div>
            <p class="gx-note" style="margin:0;">Installing opens the Tailscale login page to authenticate this device.</p>
          </template>
        </div>
      </GalaxySection>

      <GalaxySection title="Danger Zone" icon="bi-exclamation-triangle" :collapsible="false">
        <div style="padding: var(--sp-3); display:grid; gap:12px;">
          <p class="gx-note" style="margin:0;">Last resort only. <strong>Factory Reset (also known as SAVE ME)</strong> wipes params, backups, themes, models, maps, and route data, then reboots the device. This cannot be undone.</p>
          <button type="button" class="gx-btn gx-btn--danger" style="justify-self:start;" @click="factoryReset">Factory Reset Device (SAVE ME)</button>
        </div>
      </GalaxySection>
    </div>
  `,
}
