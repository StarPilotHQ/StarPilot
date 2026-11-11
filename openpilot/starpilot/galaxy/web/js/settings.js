import { requestJson } from "./startup.js"
import { GxDialog } from "./dialog.js"
import { GxState } from "./state.js"
import { connectionError } from "./polling.js"
import { reactive, nextTick } from "../vendor/vue/vue.esm-browser.js"
import { GalaxySettingRow, settingControl } from "./galaxy-setting-row.js"
import { ReverseProxySettings } from "./reverse-proxy.js"
import { OnroadLayoutPage } from "./onroad-layout.js"
import { FavoritesPage } from "./favorites.js"
import { SoundPacks } from "./sound-packs.js"
import { CloudProviderPage } from "./cloud-provider.js"
import { GxNotice } from "./notice.js"
import { route } from "./route-state.js"

const post = (fetcher, path, body, signal) => fetcher(path, {
  method: "POST", credentials: "same-origin", cache: "no-store", signal,
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
})

export const SETTINGS_SECTIONS = Object.freeze([
  { id: "lateral", label: "Lateral (Steering)", icon: "bi-arrows-move", pages: ["aol", "lane_change", "torque", "lane", "vehicle"] },
  { id: "longitudinal", label: "Longitudinal (Speed & Following)", icon: "bi-speedometer2", pages: ["profiles", "conditional", "curve", "slc", "personalities", "aggressive", "standard", "relaxed", "traffic"] },
  { id: "wheel", label: "Wheel Controls", icon: "bi-controller", pages: ["wheel"] },
  { id: "visual", label: "Visual (Display & UI)", icon: "bi-eye", pages: ["appearance", "ui_layout", "favorites", "pip"] },
  { id: "sounds", label: "Sounds & Alerts", icon: "bi-volume-up", pages: ["sounds"] },
  { id: "device", label: "Device & Data", icon: "bi-hdd", pages: ["power", "display", "data"] },
  { id: "localization", label: "Localization", icon: "bi-globe", pages: ["localization"] },
  { id: "developer", label: "Developer", icon: "bi-code-slash", pages: ["developer"] },
])

// Crop numeric rows and the inline editor row are controlled from the dedicated
// Blind Spot Camera page; they stay writable through the settings API for the
// widget/VASM editor but must not surface in the generic toggles list.
export const isPipCropRow = (row) => ["Visual camera crop editor", "Camera crop size", "Restore default camera crop"].includes(row.label) ||
  /^Vehicle (left|right) crop/.test(row.label || "")

const SECTION_LINKS = {
  device: [{ label: "Parked Power", page: "power", value: "Adjust shutdown hours and battery-voltage cutoff" }, { label: "Display", page: "display", value: "Adjust screen brightness and display timing" }, { label: "Data Uploads", page: "data", value: "Choose mobile-data uploads and usage-statistics sharing" }],
  visual: [{ label: "Driving Screen Widgets, Colors & Layout", page: "ui_layout" }, { label: "Quick Select", page: "favorites" }, { label: "Blind Spot Camera and Preview", page: "pip" }],
}

const activeSettings = new Set()

export const settingsBusy = state => ["updating", "saving"].includes(state.status) || !!state.pending || !!state.reviewing

export function sentryMonitorStatus(data, error = "") {
  const subtitle = typeof data?.subtitle === "string" ? data.subtitle : ""
  const label = subtitle.split(":", 1)[0]
  if (error || !subtitle || label.startsWith("Waiting for monitor") || label === "Unavailable" || subtitle.includes("status is unavailable"))
    return { label: "Status unavailable", tone: "warning", detail: error ? "Reconnecting to the motion monitor…" : subtitle || "Checking motion monitor…" }
  if (label === "Monitoring motion") return { label: "Armed", tone: "armed", detail: subtitle }
  if (label.startsWith("Arming")) return { label: "Arming", tone: "warning", detail: subtitle }
  return { label: "Disarmed", tone: "disarmed", detail: subtitle }
}

export class SettingsFeed {
  constructor({ publish, unauthorized = () => { }, fetcher = (...args) => fetch(...args),
    later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.request = null
    this.timer = null
    this.pollTimer = null
    this.polling = false
    this.saving = false
    this.page = "hub"
    this.data = null
    this.pending = null
    this.error = ""
  }

  stop() {
    activeSettings.delete(this)
    this.active = false
    this.generation++
    this.request?.abort()
    if (this.timer !== null) this.cancelTimer(this.timer)
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.request = this.timer = this.pollTimer = null
    this.polling = false
    this.saving = false
    this.data = null
    this.pending = null
    this.error = ""
    this.publish({ status: "idle", data: null, pending: null, error: "" })
  }

  start(page = "hub") {
    this.stop()
    this.active = true
    activeSettings.add(this)
    this.page = page
    return this.load(page)
  }

  async run(operation, { saving = false, background = false } = {}) {
    if (!this.active) return null
    const generation = ++this.generation
    this.request?.abort()
    if (this.timer !== null) this.cancelTimer(this.timer)
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.pollTimer = null
    const request = new AbortController()
    this.request = request
    this.saving = saving
    this.polling = background
    let failed = false
    this.timer = this.later(() => {
      if (!this.active || generation !== this.generation || this.request !== request) return
      request.abort()
      this.generation++
      this.request = this.timer = null
      this.saving = false
      this.polling = false
      if (!background) this.data = this.pending = null
      else this.expireMonitorStatus()
      this.error = saving ? "Saving timed out. Checking saved values before another change." : "Settings could not connect. Reconnecting automatically…"
      this.publish({ status: background && this.data ? "ready" : "unavailable", data: this.data, pending: this.pending, error: this.error })
      this.scheduleParkedRefresh()
    }, 4000)
    try {
      const response = await operation(request.signal)
      if (!this.active || generation !== this.generation || request.signal.aborted) return null
      const accessCode = response.status === 503 ? (await response.clone().json().catch(() => null))?.code : null
      if (!this.active || generation !== this.generation || request.signal.aborted) return null
      if (response.status === 401 || ["access_unavailable", "setup_required"].includes(accessCode)) {
        this.stop()
        this.unauthorized()
        return null
      }
      if (response.status === 409) {
        const error = new Error("Saved settings changed. Refresh this page.")
        error.stale = true
        throw error
      }
      if (!response.ok) throw new Error(response.status === 503 ? "The device could not read these preferences. Reconnecting automatically…" : "The device could not complete the settings request. Check your connection and try again.")
      const data = await response.json().catch(() => { throw new Error("Galaxy could not load settings. Refresh to reconnect.") })
      if (!this.active || generation !== this.generation || request.signal.aborted) return null
      return data
    } catch (error) {
      failed = true
      if (this.active && generation === this.generation && !request.signal.aborted) {
        this.pending = null
        this.expireMonitorStatus()
        if (error?.stale) this.data = null
        this.publish({
          status: this.data ? "ready" : "unavailable", data: this.data, pending: null,
          error: this.error = connectionError(error)
        })
      }
      return null
    } finally {
      if (this.request === request) {
        if (this.timer !== null) this.cancelTimer(this.timer)
        this.request = this.timer = null
        this.saving = false
        this.polling = false
        if (failed) this.scheduleParkedRefresh()
      }
    }
  }

  expireMonitorStatus() {
    if (this.data?.page === "sentry") this.data = { ...this.data, subtitle: "Motion monitor status is unavailable. Reconnecting automatically…" }
  }

  scheduleParkedRefresh() {
    if (!this.active || this.pending || this.request || this.saving || this.pollTimer !== null) return
    const generation = this.generation
    this.pollTimer = this.later(() => {
      this.pollTimer = null
      if (this.active && this.generation === generation && !this.pending && !this.request && !this.saving)
        this.load(this.page, { keepData: true, quiet: true })
    }, this.error ? 3000 : 1500)
  }

  async load(page = this.page, { keepData = page === this.page && this.data !== null, quiet = keepData } = {}) {
    if (!this.active || this.saving || (quiet && (this.pending || this.request))) return
    this.page = page
    this.pending = null
    if (!keepData) this.data = null
    if (!quiet) this.publish({ status: keepData ? "saving" : this.error ? "unavailable" : "loading", data: this.data, pending: null, error: this.error })
    const operation = this.run((signal) => this.fetcher(`./api/settings/pages/${encodeURIComponent(page)}`,
      { credentials: "same-origin", cache: "no-store", signal }), { background: quiet })
    const attempt = this.generation
    const data = await operation
    if (data && this.active && this.generation === attempt && this.page === page) {
      this.data = data
      this.error = ""
      this.publish({ status: "ready", data, pending: null, error: "" })
    }
    this.scheduleParkedRefresh()
  }

  async preview(row, direction, draft = null) {
    if (!this.active || !this.data?.view || this.pending || (this.request && !this.polling)) return
    this.publish({ status: "updating", data: this.data, pending: null, error: "" })
    const data = await this.run((signal) => post(this.fetcher, "./api/settings/preview",
      { view: this.data.view, row, direction, ...(draft === null ? {} : { draft }) }, signal))
    if (data) {
      this.pending = data
      this.publish({ status: "ready", data: this.data, pending: data, error: "" })
    }
  }

  async resetDefault(row) {
    if (!this.active || !this.data?.view || !this.data.rows?.[row]?.resetAvailable ||
      this.pending || (this.request && !this.polling)) return
    this.publish({ status: "updating", data: this.data, pending: null, error: "" })
    const data = await this.run((signal) => post(this.fetcher, "./api/settings/reset-default",
      { view: this.data.view, row }, signal))
    if (data) {
      this.pending = data
      await this.confirm()
    }
  }

  async previewValue(row, value) {
    if (!this.active || !this.data?.view || this.pending || (this.request && !this.polling)) return
    this.publish({ status: "updating", data: this.data, pending: null, error: "" })
    const data = await this.run((signal) => post(this.fetcher, "./api/settings/preview",
      { view: this.data.view, row, value }, signal))
    if (data) {
      this.pending = data
      await this.confirm()
    }
  }

  cancel() {
    this.pending = null
    if (this.active) this.publish({ status: "ready", data: this.data, pending: null, error: "" })
    this.scheduleParkedRefresh()
  }

  async confirm() {
    if (!this.active || !this.pending?.intent) return
    const intent = this.pending.intent
    this.pending = null
    this.publish({ status: "saving", data: this.data, pending: null, error: "" })
    const page = this.page
    const operation = this.run((signal) => post(this.fetcher, "./api/settings/confirm",
      { intent, confirmed: true, page }, signal), { saving: true })
    const attempt = this.generation
    const result = await operation
    if (result?.saved === true) {
      if (page === "localization") globalThis.dispatchEvent?.(new Event("galaxy-language-changed"))
      for (const feed of activeSettings) {
        if (feed !== this && !feed.pending && !feed.saving) feed.load(feed.page, { keepData: true, quiet: true })
      }
    }
    if (this.active && this.generation === attempt) {
      if (result?.saved === true && result.page?.page === page && result.page?.view) {
        this.data = result.page
        this.error = ""
        this.publish({ status: "ready", data: this.data, pending: null, error: "" })
        this.scheduleParkedRefresh()
        return
      }
      const reload = this.load(page, { keepData: true, quiet: false })
      const refreshed = this.generation
      await reload
      if (result?.saved !== true && this.active && this.generation === refreshed && this.data) this.publish({
        status: "ready", data: this.data, pending: null,
        error: "Save could not be confirmed. Current saved values are shown; review them before trying again."
      })
    }
  }
}

export const SettingsPage = {
  emits: ["nested"],
  components: { GxDialog, GxState, GalaxySettingRow, OnroadLayoutPage, FavoritesPage, SoundPacks, CloudProviderPage, ReverseProxySettings, GxNotice },
  props: {
    mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    initialPage: { type: String, default: "hub" }, title: { type: String, default: "Toggles" },
    initialSection: { type: String, default: null }, returnTo: { type: Function, default: null }
  },
  setup(props) {
    const navigation = route.view
    const navigationKey = `${props.initialPage}:${props.initialSection || ""}`
    const saved = navigation.settings?.key === navigationKey ? navigation.settings : null
    const state = reactive({ status: "idle", data: null, pending: null, error: "", query: "", searchOpen: false, section: "lateral", layoutOpen: false, layoutPanel: "widgets", favoritesOpen: false, soundChoicesDirty: false, testingSound: false, openCards: [], parents: [], developerOpen: props.initialSection === "developer", panelDirection: 1 })
    if (saved) Object.assign(state, { section: saved.section, query: saved.query, searchOpen: saved.searchOpen, parents: saved.parents.slice(), developerOpen: saved.developerOpen })
    const feed = new SettingsFeed({ publish: (update) => Object.assign(state, update), unauthorized: props.unauthorized })
    return { state, feed, sections: SETTINGS_SECTIONS, navigation, navigationKey, restoredPage: saved?.page }
  },
  computed: {
    sentryStatus() { return sentryMonitorStatus(this.state.data, this.state.error) },
    busy() { return settingsBusy(this.state) || !!this.state.testingSound },
    activeSection() {
      if (this.state.developerOpen) return this.sections.at(-1)
      const page = (this.state.data?.page || "hub").split("/")[0]
      return this.sections.find((section) => page === "hub" ? section.id === this.state.section : section.pages.includes(page)) || this.sections[0]
    },
    atSectionRoot() {
      if (this.state.developerOpen || (!this.state.data && this.initialPage === "hub" && !this.state.parents?.length)) return true
      return this.state.data?.page === "hub" ||
        (this.activeSection.pages.length === 1 && this.state.data?.page === this.activeSection.pages[0])
    },
    visibleRows() {
      const needle = this.state.query.trim().toLocaleLowerCase()
      const rows = this.state.data?.rows || []
      const inHub = this.initialPage === "hub" && this.state.data?.page === "hub"
      const section = this.activeSection
      const entries = inHub && SECTION_LINKS[section.id]
        ? SECTION_LINKS[section.id].map((row, index) => ({ row: { ...row, available: true, action: false, value: row.value || "Customize " + row.label.toLocaleLowerCase() }, index }))
        : rows.map((row, index) => ({ row, index })).filter(({ row }) => !inHub || section.pages.includes(row.page))
      if (inHub && section.id === "longitudinal") {
        entries.sort((a, b) => section.pages.indexOf(a.row.page) - section.pages.indexOf(b.row.page))
      }
      return entries.filter(({ row }) => !isPipCropRow(row)).filter(({ row, index }) => {
        const previous = rows[index - 1]
        return !(["sounds", "display"].includes(this.state.data?.page) && row.repairValue === "Auto" &&
          previous?.choices?.includes("Auto") && settingControl(previous) === "slider" && row.label === `Use Auto ${previous.label}`)
      }).filter(({ row }) =>
        !needle || `${row.label} ${row.value} ${row.reason}`.toLocaleLowerCase().includes(needle))
    },
    // A heading row starts its own card; the rows after it belong to that card even when search hides the heading.
    rowCards() {
      const heads = []
      const owner = (this.state.data?.rows || []).map((row) => {
        if (row.heading) heads.push({ title: row.label, key: row.key, collapsible: !!row.collapsed })
        return heads.length
      })
      const cards = [{ title: "", key: "main", entries: [], collapsible: false, open: true }, ...heads.map(({ title, key, collapsible }) => ({ title, key, entries: [], collapsible, open: !collapsible }))]
      for (const entry of this.visibleRows) if (!entry.row.heading) cards[owner[entry.index] || 0].entries.push(entry)
      for (const card of cards) if (card.collapsible) card.open = !!this.state.query || this.state.openCards.includes(card.key)
      return cards.filter((card, index) => !index || card.entries.length)
    },
  },
  watch: {
    "state.pending"() { this.flushSoundChoices() },
    "state.status"(status, previous) {
      if (previous === "saving" && status === "ready") this.state.soundChoicesDirty = false
      else this.flushSoundChoices()
    },
  },
  async mounted() {
    if (this.mode !== "local") return
    await this.feed.start(this.restoredPage || (this.state.developerOpen ? "developer" : this.initialPage))
    await nextTick()
    if (this.feed.active && this.navigation.scroll) window.scrollTo(this.navigation.scroll.x, this.navigation.scroll.y)
  },
  beforeUnmount() {
    const { data, section, query, searchOpen, parents, developerOpen } = this.state
    this.navigation.settings = {
      key: this.navigationKey, page: data?.page || this.restoredPage || this.initialPage, section, query, searchOpen, parents: parents.slice(), developerOpen,
    }
    this.navigation.searchPage = this.initialPage === "hub" ? "" : this.initialPage
    this.feed.stop()
  },
  methods: {
    async testAlert(key) {
      if (this.busy) return
      this.state.testingSound = true
      try {
        await requestJson("./api/sounds/preview", { timeout: 10000, request: {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }),
        } })
      } catch (error) {
        if (error?.status === 401) this.unauthorized()
        throw error
      } finally { this.state.testingSound = false }
    },
    open(page) {
      if (this.busy) return
      this.$emit?.("nested")
      this.state.query = ""
      this.state.panelDirection = 1
      if (page === "ui_layout" || page === "appearance") {
        this.feed.stop()
        this.state.layoutPanel = page === "appearance" ? "display" : "widgets"
        this.state.layoutOpen = true
      }
      else if (page === "favorites") { this.feed.stop(); this.state.favoritesOpen = true }
      else {
        if (this.state.data?.page && this.state.data.page !== page) (this.state.parents ||= []).push(this.state.data.page)
        this.feed.load(page)
      }
    },
    toggleCard(title) {
      const open = this.state.openCards
      const at = open.indexOf(title)
      if (at < 0) open.push(title)
      else open.splice(at, 1)
    },
    togglePageSearch() {
      this.state.searchOpen = !this.state.searchOpen
      if (this.state.searchOpen) this.$nextTick(() => this.$refs.pageSearch?.focus())
      else this.state.query = ""
    },
    async openCropEditor() { (await import("./router.js")).navigate("/cameras/pip") },
    // The embedded editor switches targets through the routed editor, which keeps the leave guard.
    async openLayoutTarget(target) { (await import("./router.js")).navigate(target === "projection" ? "/theme_maker/starport" : "/theme_maker") },
    rowKey(row, index) { return `${this.state.data.page}:${row.key || row.page || index}` },
    closeLayout() { this.state.layoutOpen = false; this.state.section = "visual"; this.feed.start(this.initialPage) },
    closeFavorites() { this.state.favoritesOpen = false; this.state.section = "visual"; this.feed.start("hub") },
    selectSection(section) {
      if (this.busy) return
      if (this.sections) {
        const from = this.sections.findIndex((item) => item.id === this.state.section)
        const to = this.sections.findIndex((item) => item.id === section.id)
        if (from !== -1 && to !== -1) this.state.panelDirection = to < from ? -1 : 1
      }
      this.state.parents = []
      this.state.section = section.id
      this.state.developerOpen = section.id === "developer"
      if (!this.feed.active) { this.feed.start(section.pages.length === 1 ? section.pages[0] : "hub"); return }
      this.state.query = ""
      const page = section.pages.length === 1 ? section.pages[0] : "hub"
      if (this.state.data?.page !== page) this.feed.load(page, { keepData: false, quiet: false })
    },
    requestRouteLeave(proceed) {
      if (this.busy || this.$refs.favoritesEditor?.busy || this.$refs.cloudProvider?.busy) return
      if (this.state.layoutOpen) this.$refs.layoutEditor.requestLeave(proceed)
      else proceed()
    },
    navigateBack() {
      if (this.state.pending) { this.feed.cancel(); return true }
      if (this.busy) return true
      if (this.state.layoutOpen) { this.$refs.layoutEditor.requestLeave("back"); return true }
      if (this.state.favoritesOpen) { if (!this.$refs.favoritesEditor?.busy) this.closeFavorites(); return true }
      if (this.state.developerOpen) return false
      const nested = this.initialPage === "hub" ? !this.atSectionRoot : this.state.data?.page !== this.initialPage
      if (!nested) return false
      this.back()
      return true
    },
    back() {
      if (this.state.developerOpen) return
      this.state.panelDirection = -1
      const page = this.state.data?.page || this.initialPage
      this.state.section = this.activeSection?.id || this.state.section
      this.state.query = ""
      if (page === this.initialPage && this.returnTo) { this.returnTo(); return }
      const parent = this.state.parents?.pop()
      if (parent) { this.feed.load(parent); return }
      if (this.initialPage !== "hub") { this.feed.load(this.initialPage); return }
      this.feed.load(page.includes("/") ? page.split("/")[0] :
        ["aggressive", "standard", "relaxed"].includes(page) ? "profiles" : "hub")
    },
    refreshSoundChoices() {
      this.state.soundChoicesDirty = true
      this.flushSoundChoices()
    },
    flushSoundChoices() {
      if (!this.state.soundChoicesDirty || this.state.data?.page !== "sounds" || this.feed.saving || this.state.pending) return
      this.state.soundChoicesDirty = false
      this.feed.load("sounds", { keepData: true })
    },
  },
  template: `
    <OnroadLayoutPage ref="layoutEditor" v-if="state.layoutOpen" :mode="mode" :unauthorized="unauthorized" :initial-panel="state.layoutPanel" @target="openLayoutTarget" @close="closeLayout" />
    <FavoritesPage ref="favoritesEditor" v-else-if="state.favoritesOpen" :mode="mode" :unauthorized="unauthorized" @close="closeFavorites" />
    <section v-else class="gx-settings" :aria-label="title" :inert="busy" :aria-busy="busy || undefined">
      <div v-if="title && (initialPage !== 'hub' || atSectionRoot)" class="gx-settings__header gx-page-header"><h2>{{ title }}</h2></div>
        <div v-if="mode === 'local' && initialPage === 'hub' && atSectionRoot" class="gx-settings-tabs" aria-label="Settings sections">
          <button v-for="section in sections" :key="section.id" type="button" class="gx-chip" :aria-pressed="section.id === activeSection.id" @click="selectSection(section)">{{ section.label }}</button>
        </div>
      <p class="gx-page-description" v-if="state.developerOpen">Choose developer driving options and cloud services.</p>
      <p class="gx-page-description" v-else-if="initialPage === 'pip'">Choose when to show the camera and adjust its saved crop. Use the crop editor for a live preview.</p>
      <p class="gx-page-description" v-else-if="(state.data?.page || initialPage) === 'sounds'">Adjust alert and chime volumes. Test alert plays on your comma while offroad; Auto previews a quiet-cabin starting level. Immediate warnings keep their safety volume ramp.</p>
      <p class="gx-page-description" v-else-if="(state.data?.page || initialPage) === 'display'">Adjust brightness and screen timing when custom display settings are on.</p>
      <p class="gx-page-description" v-else-if="initialPage === 'lane_change'">Configure lane-change assistance, signal timing, steering pace and following gap.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'conditional' || state.data?.page.startsWith('conditional/')">Choose when to switch between Chill and Experimental. Applies on supported vehicles when StarPilot controls acceleration and braking.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'profiles'">Adjust acceleration, braking and following for each driving personality.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'traffic'">Tune low-speed following and jerk, blending toward Relaxed at higher speeds.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'aol'">Keep steering assistance active independently of cruise control.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'torque'">Tune how StarPilot applies steering torque.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'lane'">Choose lane-centering behavior and alerts.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'curve'">Set how the vehicle slows for curves ahead.</p>
      <div v-else-if="state.data?.page === 'slc'"><p class="gx-page-description">Control how speed limits change your set speed.</p>
        <a class="gx-btn gx-btn--tonal" href="https://wiki.firestar.link/exclusive/speed-limit-controller/#vision-speed-limits" target="_blank" rel="noopener noreferrer"><i class="bi bi-book" aria-hidden="true"></i> Vision speed limits guide</a></div>
      <p class="gx-page-description" v-else-if="state.data?.page === 'wheel'">Assign what the steering-wheel controls do.</p>
      <p class="gx-page-description" v-else-if="state.data?.page === 'data'">Choose what uploads when the vehicle uses mobile data.</p>
      <p class="gx-page-description" v-else-if="['aggressive','standard','relaxed'].includes(state.data?.page)">Adjust acceleration, braking and following for this driving style.</p>
      <GxState v-if="mode !== 'local'">Local settings are unavailable in preview.</GxState>
      <template v-else>
        <GxNotice v-if="state.data && state.data.vehicleIdentified === false" tone="info">Vehicle not identified yet. Turn on the car and wait for fingerprinting; vehicle-specific settings will then appear here.</GxNotice>
        <CloudProviderPage ref="cloudProvider" v-if="state.developerOpen" :mode="mode" :unauthorized="unauthorized" />
        <ReverseProxySettings v-if="state.developerOpen" :mode="mode" :unauthorized="unauthorized" />
        <GxState v-if="state.status === 'loading'" loading>Reading your device’s saved preferences…</GxState>
        <GxNotice tone="danger" v-else-if="state.status === 'unavailable' && !state.error">The device could not read these preferences. Reconnecting automatically…</GxNotice>
        <GxNotice tone="danger" v-if="state.error">{{ state.error }}
          </GxNotice>
        <div v-if="!state.developerOpen || state.data?.page === 'developer'" class="gx-panel-stage" :style="{'--panel-direction': state.panelDirection}">
          <Transition name="gx-panel">
          <div v-if="state.data" :key="state.data.page + activeSection.id" class="gx-settings__body">
            <div v-if="state.data.page === 'pip'" class="gx-settings__actions gx-actions">
              <button type="button" class="gx-btn gx-btn--tonal" @click="openCropEditor"><i class="bi bi-crop" aria-hidden="true"></i> Crop editor</button>
            </div>
            <section v-for="(card, cardIndex) in rowCards" :key="card.key" class="gx-card gx-settings__section">
              <button v-if="card.collapsible" type="button" class="gx-section__header gx-section__header--toggle" :aria-expanded="card.open" @click="toggleCard(card.key)"><i class="bi bi-car-front" aria-hidden="true"></i><span class="gx-section__title">{{ card.title }}</span><i class="bi" :class="card.open ? 'bi-chevron-up' : 'bi-chevron-down'" aria-hidden="true"></i></button>
              <div v-else-if="cardIndex" class="gx-section__header"><i class="bi bi-car-front" aria-hidden="true"></i><span class="gx-section__title">{{ card.title }}</span></div>
              <div v-else class="gx-section__header"><i class="bi" :class="activeSection.icon" aria-hidden="true"></i><span class="gx-section__title">{{ state.data.page === 'hub' ? activeSection.label : state.data.title }}</span>
                <div v-if="state.data.page === 'sentry'" class="gx-sentry-status" :class="'gx-sentry-status--' + sentryStatus.tone" role="status">
                  <strong class="gx-sentry-status__label"><span class="gx-status-dot" aria-hidden="true"></span>{{ sentryStatus.label }}</strong>
                  <span class="gx-sentry-status__detail">{{ sentryStatus.detail }}</span>
                </div>
                <button v-if="state.data.page !== 'hub' && state.data.rows.length" type="button" class="gx-icon-btn" :aria-label="state.searchOpen ? 'Close page search' : 'Search within this page'" :aria-pressed="state.searchOpen" @click="togglePageSearch"><i class="bi" :class="state.searchOpen ? 'bi-x-lg' : 'bi-search'" aria-hidden="true"></i></button>
                </div>
              <div v-if="state.searchOpen && !cardIndex" class="gx-settings__search"><input ref="pageSearch" v-model="state.query" class="gx-field" type="search" aria-label="Search within this page" placeholder="Search within…"></div>
              <GalaxySettingRow v-for="{ row, index } in (card.open ? card.entries : [])" :key="rowKey(row, index)" :row="row" :index="index"
                :busy="busy" :disabled="!!state.error || !row.available" :save-value="(index, value) => feed.previewValue(index, value)" :test-alert="state.data.page === 'sounds' ? testAlert : null"
                @open="open" @review="(index, direction) => feed.preview(index, direction)" @reset-default="index => feed.resetDefault(index)" />
              <div v-if="!cardIndex && !visibleRows.length" class="gx-empty">No matching settings.</div>
            </section>
            <SoundPacks v-if="state.data.page === 'sounds'" :unauthorized="unauthorized" :busy="busy" @installed="refreshSoundChoices" />
          </div>
          </Transition>
        </div>
        <GxDialog v-if="state.pending" labelledby="gx-settings-confirm-title" @close="feed.cancel()"><h3 id="gx-settings-confirm-title">Confirm Saved Preference</h3><p>{{ state.pending.question }}</p>
            <div class="gx-settings__controls gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="feed.cancel()">Cancel</button>
              <button type="button" class="gx-btn" @click="feed.confirm()">Save</button></div></GxDialog>
      </template>
    </section>`,
}
