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
import { GxTrail } from "./trail.js"
import { pageGuide, guidedRows, guidedCards, guideSaveValue } from "./page-guides.js"

const post = (fetcher, path, body, signal) => fetcher(path, {
  method: "POST", credentials: "same-origin", cache: "no-store", signal,
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
})

export const SETTINGS_SECTIONS = Object.freeze([
  { id: "lateral", label: "Lateral (Steering)", icon: "bi-arrows-move", pages: ["aol", "lane_change", "torque", "lane"] },
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

// Page IDs encode nested settings; personalities are the existing flat-ID exception.
export function settingsTrailPages(page) {
  if (!page || page === "hub") return []
  const parts = page.split("/")
  const pages = parts.map((_, index) => parts.slice(0, index + 1).join("/"))
  return ["aggressive", "standard", "relaxed"].includes(parts[0]) ? ["profiles", ...pages] : pages
}

export const settingsBusy = state => ["updating", "saving"].includes(state.status) || !!state.pending || !!state.reviewing

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

const SETTINGS_PAGE_INTRO = `
  <p class="gx-page-description">{{ pageDescription }}</p>
  <a v-if="state.data?.page === 'slc'" class="gx-btn gx-btn--tonal" href="https://wiki.firestar.link/exclusive/speed-limit-controller/#vision-speed-limits" target="_blank" rel="noopener noreferrer"><i class="bi bi-book" aria-hidden="true"></i> Vision speed limits guide</a>
`

export const SettingsPage = {
  // Named so a guided page (page-guides.js) can show another page's rows inside one of its cards.
  name: "SettingsPage",
  emits: ["nested", "open-page"],
  components: { GxDialog, GxState, GxTrail, GalaxySettingRow, OnroadLayoutPage, FavoritesPage, SoundPacks, CloudProviderPage, ReverseProxySettings, GxNotice },
  props: {
    mode: { type: String, required: true }, unauthorized: { type: Function, required: true },
    initialPage: { type: String, default: "hub" }, title: { type: String, default: "Toggles" },
    initialSection: { type: String, default: null }, returnTo: { type: Function, default: null },
    navigationPath: { type: Boolean, default: false },
    // Label of the page that opened this one (e.g. "Driving"); shows the navigation tree with that page as its root.
    trailRoot: { type: String, default: "" },
    // Opened from the Galaxy menu (menu.js): the page title sits on top, the menu shows where the page lives,
    // and Manage opens each sub-page as its own route (emits "open-page").
    menuMode: { type: Boolean, default: false },
    // A second settings page shown below another page's settings (e.g. Conditional Driving Modes on Long Planner).
    embedded: { type: Boolean, default: false },
    heading: { type: String, default: "" },
    // Show only, or leave out, rows with these labels (a row that now lives on another page).
    onlyRows: { type: Array, default: null },
    hideRows: { type: Array, default: null },
    // Rows only, no card of its own: shown inside a card of the page above (page-guides.js `include`).
    bare: { type: Boolean, default: false },
  },
  setup(props) {
    const navigation = route.view
    const navigationKey = `${props.initialPage}:${props.initialSection || ""}`
    const saved = navigation.settings?.key === navigationKey ? navigation.settings : null
    const state = reactive({ status: "idle", data: null, pending: null, error: "", query: "", searchOpen: false, section: "lateral", layoutOpen: false, layoutPanel: "widgets", favoritesOpen: false, soundChoicesDirty: false, testingSound: false, parents: [], pageTitles: {}, developerOpen: props.initialSection === "developer", panelDirection: 1 })
    if (saved) Object.assign(state, { section: saved.section, query: saved.query, searchOpen: saved.searchOpen, parents: saved.parents.slice(), developerOpen: saved.developerOpen })
    const feed = new SettingsFeed({ publish: (update) => Object.assign(state, update), unauthorized: props.unauthorized })
    return { state, feed, sections: SETTINGS_SECTIONS, navigation, navigationKey, restoredPage: saved?.page }
  },
  computed: {
    busy() { return settingsBusy(this.state) || !!this.state.testingSound },
    pageTitle() { return this.state.data?.page && this.state.data.page !== "hub" ? this.state.data.title : this.title },
    pageDescription() {
      const page = this.state.data?.page || this.initialPage
      if (pageGuide(page)?.intro) return pageGuide(page).intro
      if (this.state.developerOpen || page === "developer") return "Choose developer driving options and cloud services."
      if (page === "conditional" || page.startsWith("conditional/")) return "Choose when to switch between Chill and Experimental. Applies on supported vehicles when StarPilot controls acceleration and braking."
      if (["aggressive", "standard", "relaxed"].includes(page)) return "Adjust acceleration, braking and following for this driving style."
      if (page === "sentry") return this.state.data?.subtitle || "Checking motion monitor…"
      return {
        pip: "Choose when to show the camera and adjust its saved crop. Use the crop editor for a live preview.",
        sounds: "Adjust alert and chime volumes. Test alert plays on your comma while offroad; Auto previews a quiet-cabin starting level. Immediate warnings keep their safety volume ramp.",
        display: "Adjust brightness and screen timing when custom display settings are on.",
        lane_change: "Configure lane-change assistance, signal timing, steering pace and following gap.",
        profiles: "Adjust acceleration, braking and following for each driving personality.",
        traffic: "Tune low-speed following and jerk, blending toward Relaxed at higher speeds.",
        aol: "Keep steering assistance active independently of cruise control.",
        torque: "Tune how StarPilot applies steering torque.",
        lane: "Lane centering uses detected lane lines and road edges to calculate a target position, then compares it with the driving model’s predicted path. It adds a limited, smoothed steering correction to the model’s request to bring the path closer to that target.\n\nWith two suitable boundaries, it aims for the centre between them. With only one, it can add correction away from that boundary when the model path comes too close. The model remains the basis for steering, while model preference controls how much it can reduce the added lane-centering correction.",
        curve: "Set how the vehicle slows for curves ahead.",
        slc: "Control how speed limits change your set speed.",
        wheel: "Assign what the steering-wheel controls do.",
        data: "Choose what uploads when the vehicle uses mobile data.",
      }[page] || ""
    },
    navigationItems() {
      if (!this.navigationPath || !this.state.data) return []
      const page = this.state.data.page
      const pages = settingsTrailPages(page)
      const section = this.activeSection
      const items = []
      if (page === "hub" || section.pages.length > 1) items.push({ id: "section", label: section.label })
      return [...items, ...pages.map(id => ({ id, label: id === page ? this.state.data.title :
        this.state.pageTitles[id] || id.split("/").at(-1).replaceAll("_", " ") }))]
    },
    trailEnabled() { return this.navigationPath || (!!this.trailRoot && !!this.returnTo) },
    // The navigation tree shown in the first card's header (Toggles: section › page › sub-page;
    // pages opened from elsewhere: that page › page › sub-page).
    trailItems() {
      if (this.navigationPath) return this.navigationItems
      if (!this.trailEnabled || !this.state.data) return []
      const page = this.state.data.page
      const pages = settingsTrailPages(page)
      const start = pages.indexOf(this.initialPage)
      return [{ id: "root", label: this.trailRoot }, ...(start < 0 ? [page] : pages.slice(start)).map(id => ({ id, label: id === page ? this.state.data.title :
        this.state.pageTitles[id] || id.split("/").at(-1).replaceAll("_", " ") }))]
    },
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
      const rows = guidedRows(this.state.data?.page, this.state.data?.rows || [])
      const inHub = (this.navigationPath || this.initialPage === "hub") && this.state.data?.page === "hub"
      const section = this.activeSection
      const entries = inHub && SECTION_LINKS[section.id]
        ? SECTION_LINKS[section.id].map((row, index) => ({ row: { ...row, available: true, action: false, value: row.value || "Customize " + row.label.toLocaleLowerCase() }, index }))
        : rows.map((row, index) => ({ row, index })).filter(({ row }) => !inHub || section.pages.includes(row.page))
      if (inHub && section.id === "longitudinal") {
        entries.sort((a, b) => section.pages.indexOf(a.row.page) - section.pages.indexOf(b.row.page))
      }
      return entries.filter(({ row }) => !isPipCropRow(row))
        .filter(({ row }) => row.heading || ((!this.onlyRows || this.onlyRows.includes(row.label)) && !this.hideRows?.includes(row.label)))
        .filter(({ row, index }) => {
        const previous = rows[index - 1]
        return !(["sounds", "display"].includes(this.state.data?.page) && row.repairValue === "Auto" &&
          previous?.choices?.includes("Auto") && settingControl(previous) === "slider" && row.label === `Use Auto ${previous.label}`)
      }).filter(({ row }) =>
        !needle || `${row.label} ${row.value} ${row.reason}`.toLocaleLowerCase().includes(needle))
    },
    // A heading row starts its own card; the rows after it belong to that card even when search hides the heading.
    rowCards() {
      // A page shown for a few of its rows under another page (onlyRows) stays a plain list.
      const guided = this.onlyRows ? null : guidedCards(this.state.data?.page, this.visibleRows, this.state.query)
      if (guided) return guided
      // Only some rows, shown under another page: in the order they were asked for.
      if (this.onlyRows) return [{ title: "", entries: this.visibleRows.filter(({ row }) => !row.heading)
        .sort((a, b) => this.onlyRows.indexOf(a.row.label) - this.onlyRows.indexOf(b.row.label)) }]
      const titles = []
      const owner = (this.state.data?.rows || []).map((row) => (row.heading ? titles.push(row.label) : titles.length))
      const cards = [{ title: "", entries: [] }, ...titles.map((title) => ({ title, entries: [] }))]
      for (const entry of this.visibleRows) if (!entry.row.heading) cards[owner[entry.index] || 0].entries.push(entry)
      return cards.filter((card, index) => !index || card.entries.length)
    },
  },
  watch: {
    "state.data"(data) {
      if (!this.trailEnabled || !data) return
      this.rememberPathPage(data)
      this.loadPathTitles()
    },
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
    // An embedded page shares its route with the page above it, which keeps the route's saved state.
    if (this.embedded) { this.pathRequest?.abort(); this.feed.stop(); return }
    const { data, section, query, searchOpen, parents, developerOpen } = this.state
    this.navigation.settings = {
      key: this.navigationKey, page: data?.page || this.restoredPage || this.initialPage, section, query, searchOpen, parents: parents.slice(), developerOpen,
    }
    this.navigation.searchPage = this.initialPage === "hub" ? "" : this.initialPage
    this.pathRequest?.abort(); this.feed.stop()
  },
  methods: {
    guideSaveValue,
    rememberPathPage(data) {
      if (typeof data?.page !== "string" || typeof data.title !== "string") return
      this.state.pageTitles[data.page] = data.title
      for (const row of data.rows || []) {
        if (row.page && typeof row.label === "string") this.state.pageTitles[row.page] = row.label
      }
    },
    async loadPathTitles() {
      if (this.pathRequest || !this.feed.active || !this.state.data) return
      const missing = settingsTrailPages(this.state.data.page).slice(0, -1).filter(page => !this.state.pageTitles[page])
      if (!missing.length) return
      // Search can open a deep page directly. Read only missing ancestor labels;
      // never replace the settings view or interrupt its save/poll request.
      const controller = new AbortController()
      this.pathRequest = controller
      const timer = setTimeout(() => controller.abort(), 4000)
      try {
        await Promise.all(missing.map(async page => {
          try {
            const response = await fetch(`./api/settings/pages/${encodeURIComponent(page)}`, {
              credentials: "same-origin", cache: "no-store", signal: controller.signal,
            })
            if (controller.signal.aborted) return
            if (response.status === 401) { controller.abort(); this.feed.stop(); this.unauthorized(); return }
            if (!response.ok) return
            const data = await response.json()
            if (!controller.signal.aborted && data?.page === page && Array.isArray(data.rows)) this.rememberPathPage(data)
          } catch { /* The main settings feed owns connection feedback and retry. */ }
        }))
      } finally {
        clearTimeout(timer)
        if (this.pathRequest === controller) this.pathRequest = null
      }
    },
    jumpToPage(page) {
      if (this.busy || this.state.status === "loading" || !this.trailItems.some(item => item.id === page)) return
      if (page === this.state.data?.page) return
      if (page === "root") { this.returnTo?.(); return }
      this.state.panelDirection = -1
      this.state.query = ""
      if (page === "hub" || page === "section") { this.selectSection(this.activeSection); return }
      const above = settingsTrailPages(page).slice(0, -1)
      this.state.parents = this.navigationPath ? ["hub", ...above] : above.slice(Math.max(0, above.indexOf(this.initialPage)))
      this.feed.load(page)
    },
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
      if (this.menuMode) { this.$emit("open-page", page); return }
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
    selectCategory(section) {
      // Categories remain available inside editors; use their existing leave
      // handling so changing categories cannot discard unsaved work.
      this.requestRouteLeave(() => {
        if (this.busy) return
        this.state.layoutOpen = false
        this.state.favoritesOpen = false
        this.state.query = ""
        this.state.searchOpen = false
        this.selectSection(section)
      })
    },
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
      const nested = this.navigationPath || this.initialPage === "hub" ? !this.atSectionRoot : this.state.data?.page !== this.initialPage
      if (!nested) return false
      this.back()
      return true
    },
    back() {
      if (this.state.developerOpen) return
      if (this.navigationPath) {
        const pages = settingsTrailPages(this.state.data?.page)
        this.jumpToPage(pages.length > 1 ? pages.at(-2) : "section")
        return
      }
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
    <div class="gx-settings-shell" :class="{ 'gx-settings-shell--path': navigationPath }">
      <template v-if="navigationPath">
        <div v-if="!state.layoutOpen && !state.favoritesOpen" class="gx-settings__header gx-page-header"><h2>{{ pageTitle }}</h2></div>
        <!-- Galaxy menu: the side menu (menu.js) replaced the Toggles category bar, so no page shows it. -->
      </template>
    <OnroadLayoutPage ref="layoutEditor" v-if="state.layoutOpen" :mode="mode" :unauthorized="unauthorized" :initial-panel="state.layoutPanel" @target="openLayoutTarget" @close="closeLayout" />
    <FavoritesPage ref="favoritesEditor" v-else-if="state.favoritesOpen" :mode="mode" :unauthorized="unauthorized" @close="closeFavorites" />
    <section v-else class="gx-settings" :class="{ 'gx-settings--path': trailEnabled, 'gx-settings--menu': menuMode, 'gx-settings--embedded': embedded, 'gx-settings--bare': bare }" :aria-label="heading || title" :inert="busy" :aria-busy="busy || undefined">
      <div v-if="menuMode && !embedded" class="gx-settings__header gx-page-header"><h2>{{ heading || state.data?.title || title }}</h2>
        <button v-if="state.data && state.data.page !== 'hub' && state.data.rows.length" type="button" class="gx-icon-btn" :aria-label="state.searchOpen ? 'Close page search' : 'Search within this page'" :aria-pressed="state.searchOpen" @click="togglePageSearch"><i class="bi" :class="state.searchOpen ? 'bi-x-lg' : 'bi-search'" aria-hidden="true"></i></button></div>
      <h3 v-if="embedded && heading" class="gx-menu-embed-title">{{ heading }}</h3>
      <div v-if="!menuMode && !navigationPath && title && (initialPage !== 'hub' || atSectionRoot)" class="gx-settings__header gx-page-header"><h2>{{ title }}</h2></div>
      <div v-if="pageDescription && !onlyRows" class="gx-settings__summary">${SETTINGS_PAGE_INTRO}</div>
      <GxState v-if="mode !== 'local'">Local settings are unavailable in preview.</GxState>
      <template v-else>
        <GxNotice v-if="state.data && state.data.vehicleIdentified === false && !bare" tone="info">Vehicle not identified yet. Turn on the car and wait for fingerprinting; vehicle-specific settings will then appear here.</GxNotice>
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
            <section v-for="(card, cardIndex) in rowCards" :key="card.id || card.title || 'main'" class="gx-card gx-settings__section">
              <div v-if="cardIndex && !card.guide" class="gx-section__header"><i class="bi bi-car-front" aria-hidden="true"></i><span class="gx-section__title">{{ card.title }}</span></div>
              <div v-else-if="!cardIndex && !menuMode" class="gx-section__header"><GxTrail v-if="trailItems.length" :items="trailItems" :disabled="busy" label="Toggle hierarchy" @select="jumpToPage" />
                <template v-else><i class="bi" :class="activeSection.icon" aria-hidden="true"></i><span class="gx-section__title">{{ state.data.page === 'hub' ? activeSection.label : state.data.title }}</span></template>
                <button v-if="state.data.page !== 'hub' && state.data.rows.length" type="button" class="gx-icon-btn" :aria-label="state.searchOpen ? 'Close page search' : 'Search within this page'" :aria-pressed="state.searchOpen" @click="togglePageSearch"><i class="bi" :class="state.searchOpen ? 'bi-x-lg' : 'bi-search'" aria-hidden="true"></i></button>
                </div>
              <template v-if="card.guide && card.title"><div class="gx-section__header gx-guide__header"><i class="bi" :class="card.icon" aria-hidden="true"></i><h3 class="gx-section__title">{{ card.title }}</h3></div>
                <p v-if="card.intro" class="gx-guide__intro">{{ card.intro }}</p></template>
              <div v-if="state.searchOpen && !cardIndex" class="gx-settings__search"><input ref="pageSearch" v-model="state.query" class="gx-field" type="search" aria-label="Search within this page" placeholder="Search within…"></div>
              <GalaxySettingRow v-for="{ row, index } in card.entries" :key="rowKey(row, index)" :row="row" :index="index" :class="{ 'gx-guide__sub': row.guideSub }"
                :busy="busy" :disabled="!!state.error || !row.available" :save-value="(index, value) => feed.previewValue(index, guideSaveValue(row, value))" :test-alert="state.data.page === 'sounds' ? testAlert : null"
                @open="open" @review="(index, direction) => feed.preview(index, direction)" @reset-default="index => feed.resetDefault(index)" />
              <SettingsPage v-if="card.include && mode === 'local'" :key="'include:' + card.include.page" bare embedded menu-mode :mode="mode" :unauthorized="unauthorized"
                :initial-page="card.include.page" :title="card.title" :only-rows="card.include.rows" />
              <div v-if="!cardIndex && !visibleRows.length && !card.include" class="gx-empty">No matching settings.</div>
            </section>
            <SoundPacks v-if="state.data.page === 'sounds'" :unauthorized="unauthorized" :busy="busy" @installed="refreshSoundChoices" />
          </div>
          </Transition>
        </div>
        <GxDialog v-if="state.pending" labelledby="gx-settings-confirm-title" @close="feed.cancel()"><h3 id="gx-settings-confirm-title">Confirm Saved Preference</h3><p>{{ state.pending.question }}</p>
            <div class="gx-settings__controls gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="feed.cancel()">Cancel</button>
              <button type="button" class="gx-btn" @click="feed.confirm()">Save</button></div></GxDialog>
      </template>
    </section>
    </div>`,
}
