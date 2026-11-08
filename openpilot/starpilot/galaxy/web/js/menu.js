// Galaxy menu: one tree for the desktop side menu and the phone Settings tab.
// The pages themselves are unchanged; this file only decides where each one lives.
import { reactive } from "../vendor/vue/vue.esm-browser.js"

// Every settings page (and sub-page) opens on its own route, e.g. /settings/page/aggressive/custom_following.
export const SETTINGS_PAGE_ROUTE = "/settings/page/"
// Phone: a top-level group's own page, e.g. /settings/menu/driving.
export const MENU_GROUP_ROUTE = "/settings/menu/"
const page = id => SETTINGS_PAGE_ROUTE + id

export function settingsPageFromPath(path) {
  if (typeof path !== "string" || !path.startsWith(SETTINGS_PAGE_ROUTE)) return null
  const id = path.slice(SETTINGS_PAGE_ROUTE.length)
  return /^[a-z0-9_]+(\/[a-z0-9_]+)*$/.test(id) ? id : null
}

// Pages that are not plain settings lists open where they always have.
export function settingsPagePath(id) {
  if (id === "appearance") return "/appearance"
  if (id === "ui_layout") return "/theme_maker"
  if (id === "favorites") return "/quick-select"
  if (id === "developer") return "/developer/connect"
  if (id === "vehicle") return "/vehicle"
  if (id === "pip") return "/cameras/pip"
  return page(id)
}

// A search hit opens the page that now holds that row.
export function settingsSearchPath(hit) {
  if (hit.page === "data") return page("power")
  if (hit.page === "localization" && RIGHT_HAND_DRIVE_ROWS.includes(hit.label)) return "/vehicle"
  if (hit.page === "torque" && hit.label === "Turn Signal Guidance") return page("lane_change")
  if (hit.page === "lane_change" && SIGNAL_LANE_ROWS.includes(hit.label)) return page("conditional/cem")
  return settingsPagePath(hit.page)
}

// The tree. Groups (children) only open in the menu; leaves (path) open a page.
// `also` lists other routes that are this same page; `subs` names routes reached from the page's own buttons.
export const MENU = Object.freeze([
  { id: "home", label: "Home", icon: "bi-house-fill", path: "/", main: true },
  { id: "routes", label: "Routes", icon: "bi-camera-reels", path: "/recordings", main: true },
  // 8 Nov 2026: every page is at most two clicks deep. Steering and Speed are top-level groups (no "Driving" wrapper).
  { id: "model-manager", label: "Driving Model", icon: "bi-cpu", path: "/manage_models", also: ["/model_laboratory"],
    summary: "Choose, download and pair driving models" },
  // Bootstrap Icons has no steering wheel: gx-icon-steering-wheel is drawn in menu.css.
  { id: "steering", label: "Steering", icon: "gx-icon-steering-wheel", children: [
    { id: "lane-changes", label: "Lane Changes", path: page("lane_change"), also: ["/driving/lane_change"] },
    { id: "lane-centering", label: "Lane Centering", path: page("lane"), also: ["/driving/lane"] },
    { id: "aol", label: "Always On Lateral", path: page("aol"), also: ["/driving/aol"] },
    // Torque-steered cars only: on angle-steered cars the page has nothing left to set once
    // Turn Signal Guidance moves to Lane Changes, so the item hides.
    { id: "torque", label: "Steering and Torque", path: page("torque"), also: ["/driving/torque"], torqueOnly: true },
    // /tuning/flm and /tuning/plots open a tab of this same page (its tabs switch without changing the address).
    { id: "tuning", label: "Tuning and Analysis", path: "/tuning", also: ["/tuning/flm", "/tuning/plots"] },
  ] },
  { id: "speed", label: "Speed", icon: "bi-speedometer2", children: [
    { id: "personalities", label: "Driving Personalities", path: page("personalities"),
      subs: { [page("aggressive")]: "Aggressive", [page("standard")]: "Standard", [page("relaxed")]: "Relaxed",
        [page("traffic")]: "Traffic", "/driving/traffic": "Traffic" } },
    // Conditional Driving Modes: its own item, no longer a second card on the planner page.
    { id: "experimental-chill", label: "Experimental & Chill", path: page("conditional"), also: ["/driving/conditional"],
      subs: { [page("conditional/cem")]: "Experimental Conditions", [page("conditional/ccm")]: "Chill Conditions" } },
    { id: "long-planner", label: "Acceleration & Braking", path: page("profiles"), also: ["/driving/profiles"] },
    { id: "csc", label: "Curve Speed Controller", path: page("curve"), also: ["/driving/curve"] },
    { id: "slc", label: "Speed Limit Controller", path: page("slc"), also: ["/driving/slc"] },
  ] },
  { id: "car", label: "Car", icon: "bi-car-front", children: [
    { id: "vehicle", label: "Vehicle", path: "/vehicle" },
    { id: "wheel", label: "Wheel Controls", path: page("wheel") },
  ] },
  { id: "cameras", label: "Cameras", icon: "bi-camera-video", children: [
    { id: "blind-spot-warnings", label: "Blind Spot Warnings", path: "/cameras/vasm" },
    { id: "blind-spot", label: "Blind Spot Camera", path: "/cameras/pip", also: [page("pip")] },
    // The old Cameras & Monitoring page (/cameras) opens Sentry Mode.
    { id: "sentry", label: "Sentry Mode", path: "/cameras/events", also: ["/cameras", "/cameras/sentry-settings"] },
  ] },
  { id: "screen-sound", label: "Screen & Sound", icon: "bi-display", children: [
    { id: "visuals", label: "Visuals", path: "/appearance" },
    { id: "colors-layout", label: "Display Layout", path: "/theme_maker" },
    { id: "quick-select", label: "Quick Select", path: "/quick-select" },
    { id: "display", label: "Brightness & Timeout", path: page("display") },
    { id: "sounds", label: "Sounds & Alerts", path: page("sounds") },
  ] },
  { id: "connections", label: "Connections", icon: "bi-broadcast", children: [
    { id: "bluetooth", label: "Bluetooth", path: "/bluetooth" },
    // /spotify is the old address of Spotify and Phone Audio: a sub-page, not StarPort itself.
    { id: "starport", label: "StarPort", path: "/starport",
      subs: { "/starport/audio": "Spotify and Phone Audio", "/spotify": "Spotify and Phone Audio", "/theme_maker/starport": "StarPort Layout" } },
    { id: "galaxy-access", label: "Galaxy Access & Install", path: "/galaxy" },
  ] },
  // The Maps page's own tab bar is gone: its four pages are here.
  { id: "maps", label: "Maps", icon: "bi-map", children: [
    // Navigation = destination and route, with navigation setup below (/navigation/setup opens it at the setup).
    { id: "navigation", label: "Navigation", path: "/navigation", also: ["/navigation/setup"] },
    { id: "offline-maps", label: "Offline Maps", path: "/navigation/maps" },
    { id: "gps-source", label: "GPS Source", path: "/navigation/gps" },
  ] },
  { id: "system", label: "System", icon: "bi-gear", children: [
    { id: "updates", label: "Updates & Backup", path: "/system" },
    { id: "power-data", label: "Power & Data", path: page("power"), also: [page("data")] },
    { id: "language", label: "Language & Units", path: page("localization") },
  ] },
  { id: "developer", label: "Developer", icon: "bi-code-slash", children: [
    { id: "developer-options", label: "Developer Options", path: "/developer/connect" },
    { id: "system-monitor", label: "System Monitor", path: "/logs/monitor" },
    // /manage_tmux is the old address of tmux Live View (a sub-page).
    { id: "logs", label: "Logs & Diagnostics", path: "/logs",
      subs: { "/logs/crashes": "Crash Reports", "/logs/starport": "StarPort Logs",
        "/logs/tmux": "tmux Live View", "/manage_tmux": "tmux Live View", "/device-preferences": "Force Drive State" } },
  ] },
  { id: "help", label: "Help", icon: "bi-question-circle", children: [
    // Troubleshoot also holds Changed Settings (8 Nov 2026): /settings/recovery opens it at that list.
    { id: "troubleshoot", label: "Troubleshoot", path: "/logs/troubleshoot", also: ["/troubleshoot", "/settings/recovery"] },
    { id: "help-community", label: "Help & Community", path: "/logs/help" },
  ] },
])

// Phone group pages draw each page as a card like the Settings tab: an icon and one line saying what is on it.
export const MENU_CARDS = Object.freeze({
  "lane-changes": ["bi-sign-merge-right", "Auto lane changes, speed, nudge and gaps"],
  "lane-centering": ["bi-arrows-collapse-vertical", "Strength, offset and model path preference"],
  aol: ["gx-icon-steering-wheel", "Steering without cruise, pauses and resume"],
  torque: ["bi-sliders", "Steering torque settings for this car"],
  tuning: ["bi-graph-up", "Auto tuning, corrections and live plots"],
  personalities: ["bi-people", "Aggressive, Standard, Relaxed and Traffic"],
  "experimental-chill": ["bi-shuffle", "When to switch to Chill or Experimental"],
  "long-planner": ["bi-speedometer", "Profiles, stopping and takeoffs"],
  csc: ["bi-bezier2", "Slowing for curves, with learning"],
  slc: ["bi-signpost", "Speed-limit offsets, sources and timing"],
  vehicle: ["bi-car-front-fill", "Vehicle selection and settings for this car"],
  wheel: ["bi-joystick", "Steering wheel buttons and scroll wheels"],
  "blind-spot-warnings": ["bi-exclamation-triangle", "Side window warnings from the cabin camera"],
  "blind-spot": ["bi-pip", "When the camera shows, and its crop"],
  sentry: ["bi-shield-check", "Motion events and notifications"],
  visuals: ["bi-eye", "Camera view, widgets and lead info"],
  "colors-layout": ["bi-palette", "Driving screen widgets, colors and road"],
  "quick-select": ["bi-grid-3x3-gap", "Your three driving-screen shortcuts"],
  display: ["bi-brightness-high", "Brightness and screen timeouts"],
  sounds: ["bi-volume-up", "Alert sounds and sound packs"],
  bluetooth: ["bi-bluetooth", "Saved devices and connections"],
  starport: ["bi-cast", "Your car screen, Spotify and phone audio"],
  "galaxy-access": ["bi-globe", "Remote access, app install and hotspot"],
  navigation: ["bi-signpost-split", "Destination, route and navigation setup"],
  "offline-maps": ["bi-map", "Download and manage offline maps"],
  "gps-source": ["bi-crosshair", "Comma, car or phone GPS"],
  updates: ["bi-arrow-repeat", "Branch updates, version history and backups"],
  "power-data": ["bi-battery-charging", "Parked power and data uploads"],
  language: ["bi-translate", "Language and Galaxy slider units"],
  "developer-options": ["bi-code-slash", "Developer driving options and resets"],
  "system-monitor": ["bi-activity", "Temperatures, CPU, memory and processes"],
  logs: ["bi-file-earmark-text", "Crash reports, StarPort logs and tmux"],
  troubleshoot: ["bi-tools", "Car details, changed settings and resets"],
  "help-community": ["bi-life-preserver", "Guides and community help"],
})
export const menuCard = node => { const [icon, summary] = MENU_CARDS[node.id] || []; return { icon: icon || node.icon || "bi-chevron-right", summary: summary || node.summary || "" } }

// Conditional Experimental's signal-lane rows: the device serves them with Lane Changes, but they belong with
// Experimental conditions (they only change its Signal speed rule).
const SIGNAL_LANE_ROWS = Object.freeze(["Signal lane detection", "Signal lane width"])
// Right-Hand Drive is about the car, so it shows on Vehicle (Car) rather than Language & Units.
const RIGHT_HAND_DRIVE_ROWS = Object.freeze(["Right-Hand Drive"])
// Vehicle settings rows that change how Always On Lateral behaves on this car.
const AOL_VEHICLE_ROWS = Object.freeze(["Three-Finger AOL Tap", "Disengage AOL on Brake"])
// Sounds & Alerts rows for blind spot alerts, also shown with Blind Spot Warnings.
const BLIND_SPOT_SOUND_ROWS = Object.freeze(["Loud Blindspot Alert", "Blindspot Alert While Lateral Is Off"])
// Extra settings pages shown on another page, below its own settings.
export const PAGE_COMPANIONS = Object.freeze({
  power: { page: "data", heading: "Data Uploads" },
  "conditional/cem": { page: "lane_change", heading: "Signal speed: turn or lane change", onlyRows: SIGNAL_LANE_ROWS },
  // The car's own AOL options (Vehicle settings) also show with Always On Lateral; the card hides on cars without them.
  aol: { page: "vehicle", heading: "AOL options for this car", onlyRows: AOL_VEHICLE_ROWS },
  // Lane Changes shows Turn Signal Guidance in its own list instead (page-guides.js `include`).
})
// The same for pages that are not settings lists: shown below the page by app.js.
export const ROUTE_COMPANIONS = Object.freeze({
  "/vehicle": { page: "localization", heading: "Driving side", onlyRows: RIGHT_HAND_DRIVE_ROWS },
  "/cameras/vasm": { page: "sounds", heading: "Blind spot alert sounds", onlyRows: BLIND_SPOT_SOUND_ROWS },
})
// Rows that now live on another page.
export const PAGE_HIDDEN_ROWS = Object.freeze({ torque: ["Turn Signal Guidance"], lane_change: SIGNAL_LANE_ROWS,
  localization: RIGHT_HAND_DRIVE_ROWS })
// Page titles that changed with the menu.
export const PAGE_HEADINGS = Object.freeze({ power: "Power & Data", profiles: "Acceleration & Braking",
  conditional: "Experimental & Chill", display: "Brightness & Timeout", localization: "Language & Units", traffic: "Traffic" })

// ---- Lookup --------------------------------------------------------------------------------
const NODES = new Map(), PARENTS = new Map()
;(function index(nodes, parent) {
  for (const node of nodes) {
    NODES.set(node.id, node)
    PARENTS.set(node.id, parent)
    if (node.children) index(node.children, node.id)
  }
})(MENU, null)

const words = segment => segment.replace(/[-_]+/g, " ").replace(/^./, letter => letter.toLocaleUpperCase())

export const menuNode = id => NODES.get(id) || null
export function menuAncestors(id) {
  const out = []
  for (let parent = PARENTS.get(id); parent; parent = PARENTS.get(parent)) out.unshift(parent)
  return out
}
const topOf = id => menuAncestors(id)[0] || id

// Which menu item a route belongs to: the page itself, or one of its sub-pages.
export function menuLocation(path) {
  if (typeof path !== "string") return null
  let best = null
  for (const node of NODES.values()) {
    if (!node.path) continue
    let score = 0, sub = null, parent = null, tail = null
    // A page under one of the node's sub-pages (e.g. Aggressive › Custom following) belongs to that sub-page.
    const under = node.subs && Object.keys(node.subs).find(key => path.startsWith(key + "/"))
    if (node.path === path || node.also?.includes(path)) score = 10000
    else if (node.subs && Object.hasOwn(node.subs, path)) { score = 9000; sub = node.subs[path] }
    else if (under) {
      score = 8000 + under.length
      parent = { path: under, label: node.subs[under] }
      tail = words(path.slice(under.length + 1).split("/").join(" "))
      sub = `${parent.label} › ${tail}`
    } else if (node.path !== "/" && path.startsWith(node.path + "/")) {
      score = node.path.length
      sub = path.slice(node.path.length + 1).split("/").map(part => part.replace(/[-_]+/g, " ")).join(" › ")
      sub = sub.replace(/^./, letter => letter.toLocaleUpperCase())
    }
    if (score > (best?.score || 0)) best = { score, node, sub, parent, tail }
  }
  if (!best) return null
  const { node, sub, parent, tail } = best
  const top = menuNode(topOf(node.id))
  return { node, sub, top: top.id === node.id ? null : top, parent, tail }
}

// Phone navigation tree: Settings › group › page › sub-page, each step a route.
export function menuTrail(path) {
  if (path === "/settings") return []
  const group = menuGroupFromPath(path)
  if (group) return [{ id: "/settings", label: "Settings" }, { id: path, label: group.label }]
  const location = menuLocation(path)
  if (!location || location.node.main) return []
  const items = [{ id: "/settings", label: "Settings" }]
  if (location.top) items.push({ id: MENU_GROUP_ROUTE + location.top.id, label: location.top.label })
  if (location.sub) items.push({ id: location.node.path, label: location.node.label })
  if (location.parent) items.push({ id: location.parent.path, label: location.parent.label })
  items.push({ id: path, label: location.tail || location.sub || location.node.label })
  return items
}

// Where a page sits in the menu, for search results: "Driving › Cruise › Speed Limit Controller › Limit sources".
export function menuPlace(path) {
  const location = menuLocation(path)
  if (!location) return ""
  const parts = [...menuAncestors(location.node.id).map(id => menuNode(id).label), location.node.label]
  if (location.sub) parts.push(location.sub)
  return parts.join(" › ")
}

// Desktop: a sub-page shows one link back up to its page (a page below a sub-page, back to that sub-page).
export function menuBack(path) {
  const location = menuLocation(path)
  if (location?.parent) return location.parent
  return location?.sub ? { path: location.node.path, label: location.node.label } : null
}

export function menuGroupFromPath(path) {
  if (typeof path !== "string" || !path.startsWith(MENU_GROUP_ROUTE)) return null
  const node = menuNode(path.slice(MENU_GROUP_ROUTE.length))
  return node?.children && !PARENTS.get(node.id) ? node : null
}

// ---- What this comma shows -----------------------------------------------------------------
// Steering and Torque is hidden when the car steers by angle (the page then offers nothing to set).
export const menuState = reactive({ torque: null })
let torqueCheck = null
export function checkTorqueTuning({ fetcher = (...args) => fetch(...args), unauthorized = () => {} } = {}) {
  if (torqueCheck || menuState.torque !== null) return torqueCheck
  torqueCheck = (async () => {
    try {
      const response = await fetcher("./api/settings/pages/torque", { credentials: "same-origin", cache: "no-store" })
      if (response.status === 401) { unauthorized(); return }
      if (!response.ok) return
      const data = await response.json()
      const hidden = PAGE_HIDDEN_ROWS.torque
      menuState.torque = (data?.rows || []).some(row => !row.heading && !hidden.includes(row.label) && (row.action || row.page))
    } catch { /* Keep the item visible when the comma cannot answer. */ } finally { torqueCheck = null }
  })()
  return torqueCheck
}

const visibleNode = (node, hiddenPaths) => !(node.torqueOnly && menuState.torque === false) && !(node.path && hiddenPaths.includes(node.path))
function visibleChildren(node, hiddenPaths) {
  return (node.children || []).filter(child => visibleNode(child, hiddenPaths) && (!child.children || visibleChildren(child, hiddenPaths).length))
}

// Phone: a group's own pages first, then one card per sub-group.
export function groupSections(group, hiddenPaths = []) {
  const kids = visibleChildren(group, hiddenPaths)
  const sections = [{ id: group.id, label: "", items: kids.filter(child => !child.children) }]
  for (const child of kids.filter(child => child.children)) sections.push({ id: child.id, label: child.label, items: visibleChildren(child, hiddenPaths) })
  return sections.filter(section => section.items.length)
}

// ---- Desktop side menu -----------------------------------------------------------------------
export const MenuTree = {
  name: "MenuTree",
  props: {
    path: { type: String, required: true }, go: { type: Function, required: true },
    hiddenPaths: { type: Array, default: () => [] },
  },
  data: () => ({ expanded: [], menuState }),
  computed: {
    here() { return menuLocation(this.path)?.node.id || null },
    rows() {
      const here = this.here, ancestors = here ? menuAncestors(here) : []
      const rows = []
      const walk = (nodes, level) => {
        for (const node of nodes) {
          if (node.main || !visibleNode(node, this.hiddenPaths)) continue
          const kids = node.children ? visibleChildren(node, this.hiddenPaths) : null
          if (kids && !kids.length) continue
          const open = !!kids && this.expanded.includes(node.id)
          rows.push({ id: node.id, label: node.label, icon: level === 1 ? node.icon : "", level, group: !!kids, open,
            active: node.id === here, ancestor: ancestors.includes(node.id) })
          if (open) walk(kids, level + 1)
        }
      }
      walk(MENU, 1)
      return rows
    },
  },
  watch: {
    // Opening a page leaves only its own branch open.
    here: { immediate: true, handler(id) { if (id) this.expanded = menuAncestors(id) } },
  },
  methods: {
    select(row) {
      const node = menuNode(row.id)
      if (!row.group) { this.go(node.path); return }
      // One open branch at a time: opening a group closes the others.
      this.expanded = row.open ? this.expanded.filter(id => id !== row.id && !menuAncestors(id).includes(row.id))
        : [...menuAncestors(row.id), row.id]
    },
  },
  template: `<div class="gx-menu-tree">
    <button v-for="row in rows" :key="row.id" type="button" class="gx-nav-item gx-menu-item"
      :class="['gx-menu-item--l' + row.level, { active: row.active, 'gx-menu-item--ancestor': row.ancestor && !row.active }]"
      :aria-expanded="row.group ? String(row.open) : null" :aria-current="row.active ? 'page' : null" @click="select(row)">
      <i v-if="row.icon" class="bi" :class="row.icon" aria-hidden="true"></i><span>{{ row.label }}</span>
      <i v-if="row.group" class="bi bi-chevron-right gx-menu-item__chev" aria-hidden="true"></i>
    </button>
  </div>`,
}

// ---- Phone Settings tab ----------------------------------------------------------------------
export const SettingsIndex = {
  name: "SettingsIndex",
  props: { go: { type: Function, required: true }, hiddenPaths: { type: Array, default: () => [] } },
  data: () => ({ menuState }),
  computed: {
    cards() {
      return MENU.filter(node => !node.main && visibleNode(node, this.hiddenPaths)).map(node => {
        const kids = node.children ? visibleChildren(node, this.hiddenPaths) : null
        return kids && !kids.length ? null : { id: node.id, label: node.label, icon: node.icon,
          summary: kids ? kids.map(child => child.label).join(", ") : node.summary || "", path: kids ? MENU_GROUP_ROUTE + node.id : node.path }
      }).filter(Boolean)
    },
  },
  template: `<section class="gx-view gx-menu-index" aria-label="Settings">
    <div class="gx-page-header"><div><h2>Settings</h2></div></div>
    <div class="gx-menu-cards">
      <section v-for="card in cards" :key="card.id" class="gx-card gx-menu-card">
        <button type="button" class="gx-menu-card__head" @click="go(card.path)">
          <span class="gx-menu-card__icon"><i class="bi" :class="card.icon" aria-hidden="true"></i></span>
          <span class="gx-menu-card__text"><span class="gx-menu-card__title">{{ card.label }}</span><span class="gx-menu-card__summary">{{ card.summary }}</span></span>
          <i class="bi bi-chevron-right gx-menu-card__chev" aria-hidden="true"></i>
        </button>
      </section>
    </div>
  </section>`,
}

export const MenuGroupPage = {
  name: "MenuGroupPage",
  props: { group: { type: Object, required: true }, go: { type: Function, required: true }, hiddenPaths: { type: Array, default: () => [] } },
  data: () => ({ menuState }),
  computed: { sections() { return groupSections(this.group, this.hiddenPaths) } },
  methods: { card: menuCard },
  // Each page is a card in the same style as the Settings tab (icon, title, one line, chevron).
  template: `<section class="gx-view gx-menu-group" :aria-label="group.label">
    <div class="gx-page-header"><div><h2>{{ group.label }}</h2></div></div>
    <template v-for="section in sections" :key="section.id">
      <h3 v-if="section.label" class="gx-menu-group__label">{{ section.label }}</h3>
      <div class="gx-menu-cards">
        <section v-for="item in section.items" :key="item.id" class="gx-card gx-menu-card">
          <button type="button" class="gx-menu-card__head" @click="go(item.path)">
            <span class="gx-menu-card__icon"><i class="bi" :class="card(item).icon" aria-hidden="true"></i></span>
            <span class="gx-menu-card__text"><span class="gx-menu-card__title">{{ item.label }}</span><span class="gx-menu-card__summary">{{ card(item).summary }}</span></span>
            <i class="bi bi-chevron-right gx-menu-card__chev" aria-hidden="true"></i>
          </button>
        </section>
      </div>
    </template>
  </section>`,
}
