import { startLocalization } from "./localization.js"
import { rememberInstallPrompt } from "./install-app.js"
import { toggleHaptic } from "./haptics.js"
import { GxState } from "./state.js"
import { BottomNav, NAV, primaryTab } from "./bottom-nav.js"
import { UpdateRestart } from "./update-restart.js"
import { DeviceRestartWatch } from "./device-restart.js"
import { bootScreenMode, dismissBootScreen, showBootScreen, showRestartScreen } from "./boot-screen.js"
import { GxNotice } from "./notice.js"
import { createApp, reactive, defineAsyncComponent } from "../vendor/vue/vue.esm-browser.js"
import { route, navigate, navigateBack, ensureBackEntry, setRouteLeaveGuard, startRouter } from "./router.js"
import { loadCatalog } from "./startup.js"
import { Tools } from "./tools.js"
import { GxTrail, toolTrail } from "./trail.js"
import { MenuTree, SettingsIndex, MenuGroupPage, menuTrail, menuBack as menuBackLink, menuLocation, menuGroupFromPath, settingsPageFromPath,
  settingsPagePath, settingsSearchPath, PAGE_COMPANIONS, PAGE_HIDDEN_ROWS, PAGE_HEADINGS, checkTorqueTuning } from "./menu.js"
import { FavoritesPage } from "./favorites.js"
window.addEventListener("beforeinstallprompt", rememberInstallPrompt)
const Logs = page(() => import("./logs.js").then((module) => module.Logs))
const SoftwarePage = page(() => import("./software-status.js").then((module) => module.SoftwarePage))
const NavigationPage = page(() => import("./navigation.js").then((module) => module.NavigationPage))
const ModelsPage = page(() => import("./models.js").then((module) => module.ModelsPage))
import { SettingsPage } from "./settings.js"
import { SettingsRecoveryPage } from "./settings-recovery.js"
import { ToggleSearch } from "./toggle-search.js"
import { SaveFeedback } from "./save-feedback.js"
const FlmPage = page(() => import("./flm.js").then((module) => module.FlmPage))
import { LocalAuth } from "./auth-client.js"
import { Home } from "./home.js"
import { LocalAccess } from "./local-access.js"
import { MenuTile } from "./menu-tile.js"
const LocalRecordingsPage = page(() => import("./record-history.js").then((module) => module.LocalRecordingsPage))
const CamerasPage = page(() => import("./cameras.js").then((module) => module.CamerasPage))
const SentryEventsPage = page(() => import("./sentry.js").then((module) => module.SentryEventsPage))
const VasmPage = page(() => import("./vasm.js").then((module) => module.VasmPage))
const PipPage = page(() => import("./pip.js").then((module) => module.PipPage))
import { DrivingPage, drivingPage } from "./driving.js"
import { DevicePreferencesPage, devicePage } from "./device-preferences.js"
const BluetoothPage = page(() => import("./bluetooth.js").then((module) => module.BluetoothPage))
const StarPortPage = page(() => import("./starport.js").then((module) => module.StarPortPage))
const SpotifyPage = page(() => import("./spotify.js").then((module) => module.SpotifyPage))
const VehicleControlsPage = page(() => import("./vehicle-controls.js").then((module) => module.VehicleControlsPage))
const OnroadLayoutPage = page(() => import("./onroad-layout.js").then((module) => module.OnroadLayoutPage))
const GalaxyPage = page(() => import("./galaxy.js").then((module) => module.GalaxyPage))
import { DevicePicker } from "./device-picker.js"
import { DeviceState } from "./device-state.js"
import { spawnAmbientStars, pauseStarsOnSlowFirefoxScroll } from "./ambient-stars.js"

function page(loader) {
  return defineAsyncComponent({
    loader, delay: 150, timeout: 10000,
    loadingComponent: { components: { GxState }, template: '<GxState loading>Opening page…</GxState>' },
    errorComponent: { components: { GxState }, template: '<GxState tone="danger">This page could not open.<template #actions><button class="gx-btn" @click="reload">Reload page</button></template></GxState>',
      methods: { reload() { location.reload() } } },
    onError(_error, retry, fail, attempts) { if (attempts < 2) setTimeout(retry, 500); else fail() },
  })
}

const THEME_KEY = "galaxy-preview-theme"
const stored = (key) => { try { return localStorage.getItem(key) } catch { return null } }
const save = (key, value) => { try { localStorage.setItem(key, value) } catch {} }

const state = reactive({
  tools: [],
  loading: true,
  updateRestarting: false,
  error: "",
  pageError: "",
  monitorMode: "sample",
  drawerOpen: false,
  theme: stored(THEME_KEY) === "light" ? "light" : "dark",
  searchPage: "",
})
const updateRestart = new UpdateRestart({ publish: (waiting, error) => {
  state.updateRestarting = waiting
  if (waiting) showRestartScreen()
  else if (error) showBootScreen("failed", error, true)
  else if (bootScreenMode() === "restart" && !state.loading) dismissBootScreen()
} })
const deviceRestart = new DeviceRestartWatch({ paused: () => state.updateRestarting })
const authState = reactive({ status: "checking", error: "", localAccess: false, gatewayAccess: false })
const authForm = reactive({ password: "" })
const auth = new LocalAuth({ publish: (update) => {
  Object.assign(authState, update)
  if (update.status === "unavailable") scheduleReconnect()
} })



createApp({
  components: { GxTrail, BottomNav, Home, LocalAccess, Tools, Logs, SoftwarePage, NavigationPage, ModelsPage, SettingsPage, SettingsRecoveryPage, ToggleSearch, SaveFeedback, FlmPage, LocalRecordingsPage, CamerasPage, SentryEventsPage, VasmPage, PipPage, DrivingPage, DevicePreferencesPage, BluetoothPage, StarPortPage, SpotifyPage, VehicleControlsPage, OnroadLayoutPage, GalaxyPage, DevicePicker, DeviceState, MenuTile, MenuTree, SettingsIndex, MenuGroupPage, FavoritesPage },
  data: () => ({ state, authState, authForm, route, NAV, routeDirection: 1, updateRestart, deviceRestart, nextLayoutLeavePresentation: "inline" }),
  computed: {
    visibleTools() { return state.tools.filter(tool => tool.visibility !== "authenticated" ||
      (state.monitorMode === "local" && authState.status === "authenticated")).sort((a, b) =>
        Number(a.path === "/galaxy") - Number(b.path === "/galaxy")) },
    currentTool() { return state.tools.find((tool) => route.path === tool.path || route.path.startsWith(tool.path + "/")) },
    drivingSettingsPage() { return drivingPage(route.path) },
    deviceSettingsPage() { return devicePage(route.path) },
    // Galaxy menu (menu.js): tools this connection cannot open stay out of the menu.
    hiddenToolPaths() { const shown = new Set(this.visibleTools.map(tool => tool.path)); return state.tools.map(tool => tool.path).filter(path => !shown.has(path)) },
    menuGroup() { return menuGroupFromPath(route.path) },
    menuSettingsPage() { return settingsPageFromPath(route.path) },
    menuCompanion() { return PAGE_COMPANIONS[this.menuSettingsPage] || null },
    menuHeading() { return PAGE_HEADINGS[this.menuSettingsPage] || "" },
    menuHiddenRows() { return PAGE_HIDDEN_ROWS[this.menuSettingsPage] || null },
    menuTitle() { const location = menuLocation(route.path); return location ? location.sub || location.node.label : "Settings" },
    menuMapped() { return menuTrail(route.path).length > 0 },
    menuBack() { return menuBackLink(route.path) },
    // Navigation tree: the menu path on phones (Settings › group › page › sub-page); Tools pages outside the menu keep theirs.
    pageTrail() {
      if (state.loading || state.error || state.pageError || (state.monitorMode === "local" && authState.status !== "authenticated")) return []
      const trail = menuTrail(route.path)
      if (trail.length) return trail
      return toolTrail(route.path, this.visibleTools)
    },
    pageName() {
      if (this.currentTool) return this.currentTool.name
      if (route.path === "/device-preferences") return "Force Drive State"
      if (route.path === "/model_laboratory") return "Model Manager"
      return NAV.find((item) => item.path === route.path)?.name || "This page"
    },
    isLight() { return state.theme === "light" },
    connectionLabel() {
      if (state.monitorMode !== "local") return "Preview"
      if (authState.status === "authenticated") return authState.localAccess ? "Local connection" : "Remote connection"
      return authState.status === "checking" ? "Connecting…" : "Not connected"
    },
  },
  errorCaptured(error, _instance, info) {
    console.error("Galaxy page error", info, error)
    if (updateRestart.prepared) updateRestart.start(updateRestart.prepared, { provisional: true })
    else state.pageError = "This page could not finish loading."
    return false
  },
  watch: { 'route.path'(path, previous) {
    state.pageError = ""
    if (path !== "/settings") state.searchPage = ""
    else if (route.view.searchPage !== undefined) state.searchPage = route.view.searchPage
    this.routeDirection = route.direction < 0 || primaryTab(path) < primaryTab(previous) ? -1 : 1
  },
  // The menu shows Steering and Torque only on torque-steered cars; ask the comma once signed in.
  'authState.status'(status) { if (status === "authenticated" && state.monitorMode === "local") checkTorqueTuning({ unauthorized: this.sessionExpired }) } },
  methods: {
    toggleHaptic,
    setPageActive(element, active) { element.inert = !active; element.setAttribute("aria-hidden", String(!active)) },
    reloadPage() { location.reload() },
    go(path) { navigate(path, () => { state.drawerOpen = false; state.searchPage = "" }) },
    goFromMenu(path) {
      state.drawerOpen = false
      if (path === route.path) return
      this.nextLayoutLeavePresentation = "modal"
      this.go(path)
    },
    // A settings hit opens the page that now holds that row (menu.js); a tool hit opens the tool.
    openSearchHit(hit) { navigate(hit.page ? settingsSearchPath(hit) : hit.path || "/settings", () => { state.drawerOpen = false; state.searchPage = "" }) },
    openSettingsPage(page) { this.go(settingsPagePath(page)) },
    returnFromSearch() { state.searchPage = "" },
    returnToDriving() { this.go("/driving") },
    returnToDevice() { this.go("/device-preferences") },
    ensureBackEntry,
    routeBack() { state.drawerOpen = false; navigateBack() },
    toggleTheme() {
      state.theme = this.isLight ? "dark" : "light"
      document.documentElement.dataset.theme = state.theme
      save(THEME_KEY, state.theme)
    },
    isActive(path) { return route.path === path || route.path.startsWith(path + "/") },
    async signIn() {
      const password = authForm.password
      authForm.password = ""
      await auth.login(password)
    },
    async signOut() { await auth.logout() },
    sessionExpired() { if (initializing) return; auth.expired(); initialize() },
  },
  mounted() {
    this.stopLocalization = startLocalization()
    document.documentElement.dataset.theme = state.theme
    this.visibility = () => {
      if (!document.hidden) deviceRestart.wake()
      clearTimeout(reconnectTimer)
      reconnectTimer = null
      if (!document.hidden && (state.error || authState.status === "unavailable") && !initializing) initialize()
    }
    document.addEventListener("visibilitychange", this.visibility)
    window.addEventListener("pageshow", this.visibility)
    setRouteLeaveGuard((proceed, { back } = {}) => {
      if (back && this.$refs.activeSettings?.navigateBack()) return
      if (back && state.searchPage) { this.returnFromSearch(); return }
      const presentation = this.nextLayoutLeavePresentation
      this.nextLayoutLeavePresentation = "inline"
      if (this.$refs.activeLayout) this.$refs.activeLayout.requestLeave(proceed, presentation)
      else if (this.$refs.activeSettings) this.$refs.activeSettings.requestRouteLeave(proceed)
      else proceed()
    })
  },
  beforeUnmount() {
    this.stopLocalization?.()
    reconnectEnabled = false
    clearTimeout(reconnectTimer)
    startupGeneration++
    document.removeEventListener("visibilitychange", this.visibility)
    window.removeEventListener("pageshow", this.visibility)
    deviceRestart.stop()
  },
  template: `
    <div class="gx-app gx-nav-pinned" :inert="state.updateRestarting" @change.capture="toggleHaptic">
      <header class="gx-appbar">
        <button type="button" class="gx-icon-btn gx-appbar__back gx-back-btn" aria-label="Back" @click="routeBack"><i class="bi bi-arrow-left"></i></button>
        <div class="gx-appbar__pill">
          <button type="button" class="gx-appbar__home" aria-label="Galaxy Home" @click="go('/')"><span class="gx-brand" aria-hidden="true"></span><span class="gx-appbar__title">Galaxy</span></button>
          <ToggleSearch :enabled="!state.loading" :settings-enabled="state.monitorMode === 'local' && authState.status === 'authenticated'"
            :tools="visibleTools" :unauthorized="sessionExpired" :open-page="openSearchHit" />
          <div class="gx-appbar__right">
            <DeviceState v-if="state.monitorMode === 'local' && authState.status === 'authenticated'" :unauthorized="sessionExpired" :connection="connectionLabel" />
            <span v-else class="gx-status-pill"><span class="gx-status-dot offline"></span>{{ connectionLabel }}</span>
            <button v-if="state.monitorMode === 'local' && authState.status === 'authenticated' && !authState.localAccess && !authState.gatewayAccess" type="button" class="gx-btn gx-btn--tonal" @click="signOut">Sign out</button></div>
        </div>
        <button type="button" class="gx-icon-btn gx-theme-toggle" :aria-label="isLight ? 'Switch to dark mode' : 'Switch to light mode'" @click="toggleTheme"><i class="bi" :class="isLight ? 'bi-moon-stars-fill' : 'bi-sun-fill'"></i></button>
      </header>
      <!-- Galaxy menu: always shown on desktop (no pin), replaced by the Settings tab on phones (menu.js). -->
      <aside class="gx-drawer gx-drawer--menu" aria-label="Galaxy navigation">
        <div class="gx-drawer__header"><span class="gx-brand" aria-hidden="true"></span><span class="gx-drawer-title">Galaxy</span></div>
        <div class="gx-nav-section"><div class="gx-nav-section__title">Main</div>
          <button v-for="item in NAV.filter(item => item.path !== '/settings')" :key="item.path" type="button" class="gx-nav-item" :class="{active:isActive(item.path)}" :aria-current="isActive(item.path) ? 'page' : null" @click="goFromMenu(item.path)"><i class="bi" :class="item.icon"></i><span>{{ item.name }}</span></button>
        </div>
        <div class="gx-nav-section"><div class="gx-nav-section__title">Settings</div>
          <MenuTree :path="route.path" :go="goFromMenu" :hidden-paths="hiddenToolPaths" />
        </div>
        <div class="gx-nav-footer"><DevicePicker /></div>
      </aside>
      <main class="gx-content" :style="{'--route-direction': routeDirection}"><div class="gx-route-stage">
        <Transition name="gx-route" @before-enter="setPageActive($event, true)" @before-leave="setPageActive($event, false)"><div class="gx-route" :key="isActive('/recordings') ? '/recordings' : route.path">
        <GxTrail v-if="pageTrail.length" class="gx-page-trail" :class="{ 'gx-menu-trail': menuMapped }" label="Navigation tree" :items="pageTrail" @select="go" />
        <button v-if="menuBack && pageTrail.length" type="button" class="gx-settings-path__parent gx-menu-back" @click="go(menuBack.path)"><i class="bi bi-chevron-left" aria-hidden="true"></i>{{ menuBack.label }}</button>
        <template v-if="state.loading && !state.error"></template>
        <GxNotice v-else-if="state.error" tone="danger">{{ state.error }}</GxNotice>
        <section v-else-if="state.monitorMode === 'local' && authState.status !== 'authenticated'" class="gx-card gx-auth" aria-label="Galaxy sign in">
          <div class="gx-section__header"><i class="bi bi-shield-lock"></i><h2 class="gx-section__title">Galaxy access</h2></div>
          <p v-if="authState.status === 'checking'">Checking Galaxy access…</p>
          <p v-else-if="authState.status === 'setup_required'">Remote Galaxy access is not configured.</p>
          <p v-else-if="authState.status === 'unavailable'">Galaxy access is unavailable.</p>
          <p v-else-if="authState.status === 'gateway_login'"><a class="gx-btn" href="/">Reconnect to Galaxy</a></p>
          <form v-else @submit.prevent="signIn"><label for="galaxy-password">Password</label><input id="galaxy-password" class="gx-field" type="password" v-model="authForm.password" minlength="8" maxlength="255" autocomplete="current-password" required><button class="gx-btn" type="submit">Sign in</button></form>
          <GxNotice v-if="authState.error" tone="danger">{{ authState.error }}</GxNotice>

        </section>
        <GxNotice v-else-if="state.pageError" tone="danger">{{ state.pageError }} <template #actions><button class="gx-btn" @click="reloadPage">Reload page</button></template></GxNotice>
        <Home v-else-if="route.path === '/'" :mode="state.monitorMode" :unauthorized="sessionExpired" :go="go" />
        <LocalRecordingsPage v-else-if="isActive('/recordings') && authState.localAccess" :mode="state.monitorMode" :unauthorized="sessionExpired" :route-path="route.path" :go="go" />
        <div v-else-if="isActive('/recordings')" class="gx-stack">
          <GxState title="Local connection required">Routes are unavailable through the Galaxy tunnel. Open Galaxy locally to access Routes.</GxState>
          <LocalAccess :mode="state.monitorMode" :on-unauthorized="sessionExpired" />
        </div>
        <CamerasPage v-else-if="route.path === '/cameras'" :mode="state.monitorMode" :go="go" :unauthorized="sessionExpired" :tools="currentTool?.children || []" />
        <SentryEventsPage v-else-if="['/cameras/events', '/cameras/sentry-settings'].includes(route.path)" :mode="state.monitorMode" :unauthorized="sessionExpired" :go="go" />
        <PipPage v-else-if="route.path === '/cameras/pip'" :mode="state.monitorMode" :unauthorized="sessionExpired" :go="go" />
        <VasmPage v-else-if="route.path === '/cameras/vasm'" :mode="state.monitorMode" :unauthorized="sessionExpired" :go="go" />
        <Tools v-else-if="route.path === '/tools'" :tools="visibleTools" :mode="state.monitorMode" />
        <SettingsPage @nested="ensureBackEntry" ref="activeSettings" v-else-if="route.path === '/developer/connect'" :mode="state.monitorMode" :unauthorized="sessionExpired" initial-section="developer"
          menu-mode title="Developer Options" heading="Developer Options" @open-page="openSettingsPage" />
        <GalaxyPage v-else-if="route.path === '/galaxy'" :mode="state.monitorMode" :unauthorized="sessionExpired" />
        <!-- Visuals: the comma screen options as their own settings page (they used to be the layout editor's Options tab). -->
        <SettingsPage @nested="ensureBackEntry" ref="activeSettings" v-else-if="route.path === '/appearance'" key="/appearance" :mode="state.monitorMode" :unauthorized="sessionExpired"
          menu-mode initial-page="appearance" title="Visuals" heading="Visuals" @open-page="openSettingsPage" />
        <OnroadLayoutPage ref="activeLayout" v-else-if="['/theme_maker', '/theme_maker/starport'].includes(route.path)" :key="route.path" :projection="route.path === '/theme_maker/starport'" initial-panel="widgets" :mode="state.monitorMode" :unauthorized="sessionExpired" @target="go($event === 'projection' ? '/theme_maker/starport' : '/theme_maker')" @close="routeBack" />
        <SettingsRecoveryPage v-else-if="route.path === '/settings/recovery'" :mode="state.monitorMode" :unauthorized="sessionExpired" />
        <Logs v-else-if="route.path === '/logs' || route.path.startsWith('/logs/') || ['/troubleshoot', '/manage_tmux'].includes(route.path)" :path="route.path === '/troubleshoot' ? '/logs/troubleshoot' : route.path === '/manage_tmux' ? '/logs/tmux' : route.path" :mode="state.monitorMode" :unauthorized="sessionExpired" :tools="currentTool?.children || []" />
        <SoftwarePage v-else-if="route.path === '/system'" :mode="state.monitorMode" :unauthorized="sessionExpired" :restarting="baseline => updateRestart.start({ ...baseline, instance: deviceRestart.instance })"
          :preparing="baseline => updateRestart.prepare({ ...baseline, instance: deviceRestart.instance })" :cancel-preparation="() => updateRestart.cancelPreparation()" />
        <NavigationPage v-else-if="['/navigation', '/navigation/maps', '/navigation/setup'].includes(route.path)" :mode="state.monitorMode" :unauthorized="sessionExpired" :go="go" :tabs="currentTool?.children || []"
          :initial-tab="route.path === '/navigation/maps' ? 'maps' : route.path === '/navigation/setup' ? 'setup' : 'route'" />
        <ModelsPage v-else-if="['/manage_models', '/model_laboratory'].includes(route.path)" :mode="state.monitorMode" :unauthorized="sessionExpired" :focus="route.path === '/model_laboratory' ? 'laboratory' : ''" />
        <BluetoothPage v-else-if="route.path === '/bluetooth'" :mode="state.monitorMode" :unauthorized="sessionExpired" />
        <StarPortPage v-else-if="route.path === '/starport'" :mode="state.monitorMode" :local-access="authState.localAccess" :unauthorized="sessionExpired" />
        <SpotifyPage v-else-if="['/starport/audio', '/spotify'].includes(route.path)" :mode="state.monitorMode" :unauthorized="sessionExpired" />
        <VehicleControlsPage v-else-if="route.path === '/vehicle'" :mode="state.monitorMode" :unauthorized="sessionExpired" />
        <DevicePreferencesPage v-else-if="route.path === '/device-preferences'" :mode="state.monitorMode" :go="go" />
        <SettingsPage @nested="ensureBackEntry" ref="activeSettings" v-else-if="deviceSettingsPage" :key="deviceSettingsPage" :mode="state.monitorMode" :unauthorized="sessionExpired" :initial-page="deviceSettingsPage" :title="deviceSettingsPage === 'sounds' ? 'Sounds & Alerts' : 'Display'" :return-to="returnToDevice" />
        <DrivingPage v-else-if="route.path === '/driving'" :mode="state.monitorMode" :go="go" />
        <SettingsPage @nested="ensureBackEntry" ref="activeSettings" v-else-if="drivingSettingsPage" :key="drivingSettingsPage" :mode="state.monitorMode" :unauthorized="sessionExpired" :initial-page="drivingSettingsPage" :title="'Driving settings · ' + drivingSettingsPage.replaceAll('_', ' ')" :return-to="returnToDriving" trail-root="Driving" />
        <MenuGroupPage v-else-if="menuGroup" :key="menuGroup.id" :group="menuGroup" :go="go" :hidden-paths="hiddenToolPaths" />
        <FavoritesPage v-else-if="route.path === '/quick-select'" :mode="state.monitorMode" :unauthorized="sessionExpired" @close="routeBack" />
        <!-- A settings page opened from the menu, plus any page now shown with it (menu.js PAGE_COMPANIONS). -->
        <template v-else-if="menuSettingsPage">
          <SettingsPage @nested="ensureBackEntry" ref="activeSettings" :key="menuSettingsPage" menu-mode :mode="state.monitorMode" :unauthorized="sessionExpired"
            :initial-page="menuSettingsPage" :title="menuHeading || menuTitle" :heading="menuHeading" :hide-rows="menuHiddenRows" @open-page="openSettingsPage" />
          <SettingsPage v-if="menuCompanion && state.monitorMode === 'local'" :key="menuSettingsPage + ':' + menuCompanion.page" menu-mode embedded :mode="state.monitorMode" :unauthorized="sessionExpired"
            :initial-page="menuCompanion.page" :title="menuCompanion.heading" :heading="menuCompanion.heading" :only-rows="menuCompanion.onlyRows || null" @open-page="openSettingsPage" />
        </template>
        <SettingsPage @nested="ensureBackEntry" ref="activeSettings" v-else-if="route.path === '/settings' && state.searchPage" :key="state.searchPage || 'hub'" :mode="state.monitorMode" :unauthorized="sessionExpired"
          navigation-path :initial-page="state.searchPage || 'hub'" :return-to="state.searchPage ? returnFromSearch : null" />
        <SettingsIndex v-else-if="route.path === '/settings'" :go="go" :hidden-paths="hiddenToolPaths" />
        <FlmPage v-else-if="['/tuning', '/tuning/flm', '/tuning/plots'].includes(route.path)" :initial-view="route.path === '/tuning/plots' ? 'plots' : 'overview'" :mode="state.monitorMode" :unauthorized="sessionExpired" :go="go" />
        <GxState v-else :title="pageName">This capability is unavailable in this build. No operation was attempted.</GxState>
      </div></Transition></div></main>
      <BottomNav :path="route.path" @navigate="go" />
      <SaveFeedback />
    </div>
  `,
}).component("GxState", GxState).component("GxNotice", GxNotice).mount("#galaxy-app")

const galaxyBackground = document.getElementById("galaxy-bg")
spawnAmbientStars(galaxyBackground)
pauseStarsOnSlowFirefoxScroll(galaxyBackground)

startRouter()

let startupGeneration = 0
let reconnectTimer = null
let initializing = false
let reconnectEnabled = true
function scheduleReconnect() {
  if (!reconnectEnabled || document.hidden || initializing || reconnectTimer !== null) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (reconnectEnabled && !document.hidden && !initializing && (state.error || authState.status === "unavailable")) initialize()
  }, 3000)
}
async function initialize() {
  const generation = ++startupGeneration
  initializing = true
  clearTimeout(reconnectTimer)
  reconnectTimer = null
  state.loading = !state.tools.length
  if (state.loading && !state.updateRestarting) showBootScreen("connecting", "Connecting to your device…")
  try {
    const catalog = await loadCatalog()
    if (generation !== startupGeneration) return
    state.monitorMode = catalog.mode
    deviceRestart.track(catalog.instance)
    state.tools = catalog.tools
    state.error = ""
    if (state.monitorMode === "local") await auth.check()
    else authState.status = "preview"
  } catch {
    if (generation === startupGeneration) state.error = "Galaxy could not connect. Reconnecting automatically…"
  } finally {
    if (generation === startupGeneration) {
      state.loading = false
      if (bootScreenMode() === "connecting") dismissBootScreen()
      initializing = false
      if (state.error || authState.status === "unavailable") scheduleReconnect()
    }
  }
}
initialize()
