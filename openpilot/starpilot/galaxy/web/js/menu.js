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
  return page(id)
}

// A search hit opens the page that now holds that row.
export function settingsSearchPath(hit) {
  if (hit.page === "data") return page("power")
  if (hit.page === "conditional") return page("profiles")
  if (hit.page === "torque" && hit.label === "Turn Signal Guidance") return page("lane_change")
  if (hit.page === "lane_change" && SIGNAL_LANE_ROWS.includes(hit.label)) return page("conditional/cem")
  return settingsPagePath(hit.page)
}

// The tree. Groups (children) only open in the menu; leaves (path) open a page.
// `also` lists other routes that are this same page; `subs` names routes reached from the page's own buttons.
export const MENU = Object.freeze([
  { id: "home", label: "Home", icon: "bi-house-fill", path: "/", main: true },
  { id: "routes", label: "Routes", icon: "bi-camera-reels", path: "/recordings", main: true },
  { id: "driving", label: "Driving", icon: "bi-speedometer2", children: [
    { id: "model-manager", label: "Model Manager", path: "/manage_models", also: ["/model_laboratory"] },
    { id: "steering", label: "Steering", children: [
      { id: "lane-changes", label: "Lane Changes", path: page("lane_change"), also: ["/driving/lane_change"] },
      { id: "lane-centering", label: "Lane Centering", path: page("lane"), also: ["/driving/lane"] },
      { id: "aol", label: "Always On Lateral", path: page("aol"), also: ["/driving/aol"] },
      // Torque-steered cars only: on angle-steered cars the page has nothing left to set once
      // Turn Signal Guidance moves to Lane Changes, so the item hides.
      { id: "torque", label: "Steering and Torque", path: page("torque"), also: ["/driving/torque"], torqueOnly: true },
      { id: "tuning", label: "Tuning and Analysis", path: "/tuning", subs: { "/tuning/flm": "Auto Learn", "/tuning/plots": "Live plots" } },
    ] },
    { id: "cruise", label: "Cruise", children: [
      { id: "long-planner", label: "Long Planner", path: page("profiles"), also: ["/driving/profiles", page("conditional"), "/driving/conditional"],
        subs: { [page("conditional/cem")]: "Experimental Conditions", [page("conditional/ccm")]: "Chill Conditions" } },
      { id: "personalities", label: "Driving Personalities", path: page("personalities"),
        subs: { [page("aggressive")]: "Aggressive", [page("standard")]: "Standard", [page("relaxed")]: "Relaxed",
          [page("traffic")]: "Traffic", "/driving/traffic": "Traffic" } },
      { id: "csc", label: "Curve Speed Controller", path: page("curve"), also: ["/driving/curve"] },
      { id: "slc", label: "Speed Limit Controller", path: page("slc"), also: ["/driving/slc"] },
    ] },
  ] },
  { id: "vehicle", label: "Vehicle", icon: "bi-car-front", path: "/vehicle", summary: "Vehicle selection and settings" },
  { id: "controls", label: "Controls & Connections", icon: "bi-controller", children: [
    { id: "wheel", label: "Wheel Controls", path: page("wheel") },
    { id: "bluetooth", label: "Bluetooth", path: "/bluetooth" },
    { id: "starport", label: "StarPort", path: "/starport", also: ["/spotify"],
      subs: { "/starport/audio": "Spotify and Phone Audio", "/spotify": "Spotify and Phone Audio", "/theme_maker/starport": "StarPort layout" } },
    { id: "galaxy-access", label: "Galaxy Access & Install", path: "/galaxy" },
  ] },
  { id: "device", label: "Device & Visuals", icon: "bi-display", children: [
    { id: "driving-screen", label: "Driving Screen", children: [
      { id: "visuals", label: "Visuals", path: "/appearance" },
      { id: "colors-layout", label: "Colors & Layout", path: "/theme_maker" },
      { id: "quick-select", label: "Quick Select", path: "/quick-select" },
    ] },
    { id: "display", label: "Display", path: page("display") },
    { id: "cameras", label: "Cameras", children: [
      { id: "cameras-monitoring", label: "Cameras & Monitoring", path: "/cameras",
        subs: { "/cameras/pip": "Blind Spot Camera and Preview", "/cameras/events": "Sentry", "/cameras/sentry-settings": "Sentry settings", "/cameras/vasm": "V-ASM" } },
      { id: "blind-spot", label: "Blind Spot Camera and Preview", path: page("pip") },
    ] },
    { id: "sounds", label: "Sounds & Alerts", path: page("sounds") },
    { id: "power-data", label: "Power & Data", path: page("power"), also: [page("data")] },
    { id: "language", label: "Language & Units", path: page("localization") },
  ] },
  { id: "software", label: "Software", icon: "bi-arrow-repeat", children: [
    { id: "updates", label: "Updates & Backup", path: "/system" },
    { id: "changed-settings", label: "Changed Settings", path: "/settings/recovery" },
  ] },
  { id: "maps", label: "Maps", icon: "bi-map", path: "/navigation", summary: "Destinations, offline maps and setup",
    subs: { "/navigation/maps": "Offline Maps", "/navigation/setup": "Setup" } },
  { id: "developer", label: "Developer", icon: "bi-code-slash", children: [
    { id: "developer-options", label: "Developer Options", path: "/developer/connect" },
    { id: "logs", label: "Logs & Diagnostics", path: "/logs", also: ["/manage_tmux"],
      subs: { "/logs/monitor": "System Monitor", "/logs/crashes": "Crash Reports", "/logs/starport": "StarPort Logs",
        "/logs/tmux": "tmux Live View", "/manage_tmux": "tmux Live View", "/logs/troubleshoot": "Troubleshoot", "/troubleshoot": "Troubleshoot",
        "/device-preferences": "Force Drive State", "/logs/help": "Help & Community" } },
  ] },
])

// Conditional Experimental's signal-lane rows: the device serves them with Lane Changes, but they belong with
// Experimental conditions (they only change its Signal speed rule).
const SIGNAL_LANE_ROWS = Object.freeze(["Signal lane detection", "Signal lane width"])
// Extra settings pages shown on another page, below its own settings.
export const PAGE_COMPANIONS = Object.freeze({
  profiles: { page: "conditional", heading: "Conditional Driving Modes" },
  power: { page: "data", heading: "Data Uploads" },
  "conditional/cem": { page: "lane_change", heading: "Signal speed: turn or lane change", onlyRows: SIGNAL_LANE_ROWS },
  // Lane Changes shows Turn Signal Guidance in its own list instead (page-guides.js `include`).
})
// Rows that now live on another page.
export const PAGE_HIDDEN_ROWS = Object.freeze({ torque: ["Turn Signal Guidance"], lane_change: SIGNAL_LANE_ROWS })
// Page titles that changed with the menu.
export const PAGE_HEADINGS = Object.freeze({ power: "Power & Data" })

// ---- Lookup --------------------------------------------------------------------------------
const NODES = new Map(), PARENTS = new Map()
;(function index(nodes, parent) {
  for (const node of nodes) {
    NODES.set(node.id, node)
    PARENTS.set(node.id, parent)
    if (node.children) index(node.children, node.id)
  }
})(MENU, null)

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
    let score = 0, sub = null
    if (node.path === path || node.also?.includes(path)) score = 10000
    else if (node.subs && Object.hasOwn(node.subs, path)) { score = 9000; sub = node.subs[path] }
    else if (node.path !== "/" && path.startsWith(node.path + "/")) {
      score = node.path.length
      sub = path.slice(node.path.length + 1).split("/").map(part => part.replace(/[-_]+/g, " ")).join(" › ")
      sub = sub.replace(/^./, letter => letter.toLocaleUpperCase())
    }
    if (score > (best?.score || 0)) best = { score, node, sub }
  }
  if (!best) return null
  const { node, sub } = best
  const top = menuNode(topOf(node.id))
  return { node, sub, top: top.id === node.id ? null : top }
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
  items.push({ id: path, label: location.sub || location.node.label })
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

// Desktop: a sub-page shows one link back up to its page.
export function menuBack(path) {
  const location = menuLocation(path)
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
  template: `<section class="gx-view gx-menu-group" :aria-label="group.label">
    <div class="gx-page-header"><div><h2>{{ group.label }}</h2></div></div>
    <div class="gx-menu-cards">
      <section v-for="section in sections" :key="section.id" class="gx-card gx-menu-section" :aria-label="section.label || group.label">
        <div v-if="section.label" class="gx-section__header"><span class="gx-section__title">{{ section.label }}</span></div>
        <button v-for="item in section.items" :key="item.id" type="button" class="gx-menu-link" @click="go(item.path)">
          <span>{{ item.label }}</span><i class="bi bi-chevron-right" aria-hidden="true"></i>
        </button>
      </section>
    </div>
  </section>`,
}
