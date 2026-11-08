import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { MENU, MENU_CARDS, MenuGroupPage, menuCard, SettingsIndex, menuLocation, menuTrail, menuBack, menuGroupFromPath, settingsPageFromPath, settingsPagePath, settingsSearchPath,
  groupSections, menuState, PAGE_COMPANIONS, ROUTE_COMPANIONS, PAGE_HIDDEN_ROWS, PAGE_HEADINGS, MenuTree } from "../web/js/menu.js"
import { SETTINGS_SECTIONS, SettingsPage } from "../web/js/settings.js"
import { NAV, primaryTab } from "../web/js/bottom-nav.js"

const catalog = JSON.parse(readFileSync(new URL("../web/data/catalog.json", import.meta.url), "utf8"))
const tools = catalog.tools || catalog

// Nothing is lost: every settings page and every Tools page or sub-page has a home in the menu.
const settingsPages = new Set(SETTINGS_SECTIONS.flatMap(section => section.pages))
for (const page of ["vehicle", "conditional/cem", "conditional/ccm"]) settingsPages.add(page)
for (const page of settingsPages) {
  const location = menuLocation(settingsPagePath(page))
  assert.ok(location, `settings page ${page} has no place in the menu`)
}
for (const tool of tools) {
  assert.ok(menuLocation(tool.path), `tool ${tool.path} has no place in the menu`)
  for (const child of tool.children || []) assert.ok(menuLocation(child.path), `tool page ${child.path} has no place in the menu`)
}
for (const path of ["/settings/recovery", "/developer/connect", "/quick-select", "/theme_maker/starport",
  "/cameras/sentry-settings", "/model_laboratory", "/spotify", "/troubleshoot", "/manage_tmux"]) assert.ok(menuLocation(path), `${path} has no place in the menu`)

// Where things live.
const at = path => { const location = menuLocation(path); return [location.top?.label || "", location.node.label, location.sub || ""] }
assert.deepEqual(at("/settings/page/lane_change"), ["Steering", "Lane Changes", ""])
assert.deepEqual(at("/settings/page/slc"), ["Speed", "Speed Limit Controller", ""])
assert.deepEqual(at("/settings/page/conditional"), ["Speed", "Experimental & Chill", ""])
assert.deepEqual(at("/settings/page/conditional/cem"), ["Speed", "Experimental & Chill", "Experimental Conditions"])
assert.deepEqual(at("/settings/page/profiles"), ["Speed", "Acceleration & Braking", ""])
assert.deepEqual(at("/settings/page/traffic"), ["Speed", "Driving Personalities", "Traffic"])
// Each personality's own pages (custom following, acceleration, braking) sit under that personality.
for (const name of ["aggressive", "standard", "relaxed", "traffic"]) {
  for (const [page, label] of [["custom_following", "Custom following"], ["acceleration", "Acceleration"], ["braking", "Braking"]]) {
    const title = name[0].toUpperCase() + name.slice(1)
    assert.deepEqual(at(`/settings/page/${name}/${page}`), ["Speed", "Driving Personalities", `${title} › ${label}`])
    assert.deepEqual(menuBack(`/settings/page/${name}/${page}`), { path: `/settings/page/${name}`, label: title })
  }
}
assert.deepEqual(menuTrail("/settings/page/standard/braking").map(item => item.label), ["Settings", "Speed", "Driving Personalities", "Standard", "Braking"])
assert.deepEqual(menuTrail("/settings/page/standard/braking").map(item => item.id),
  ["/settings", "/settings/menu/speed", "/settings/page/personalities", "/settings/page/standard", "/settings/page/standard/braking"])
assert.deepEqual(at("/manage_models"), ["", "Driving Model", ""])
assert.deepEqual(at("/model_laboratory"), ["", "Driving Model", ""])
assert.deepEqual(at("/theme_maker/starport"), ["Connections", "StarPort", "StarPort Layout"])
// Cameras: its own group, the same three pages.
assert.deepEqual(at("/cameras/vasm"), ["Cameras", "Blind Spot Warnings", ""])
assert.deepEqual(at("/cameras/pip"), ["Cameras", "Blind Spot Camera", ""])
assert.deepEqual(at("/settings/page/pip"), ["Cameras", "Blind Spot Camera", ""])
assert.deepEqual(at("/cameras/events"), ["Cameras", "Sentry Mode", ""])
assert.deepEqual(at("/cameras/sentry-settings"), ["Cameras", "Sentry Mode", ""])
assert.deepEqual(at("/cameras"), ["Cameras", "Sentry Mode", ""])
assert.deepEqual(MENU.find(node => node.id === "cameras").children.map(node => node.label), ["Blind Spot Warnings", "Blind Spot Camera", "Sentry Mode"])
assert.equal(settingsPagePath("pip"), "/cameras/pip")
assert.deepEqual(at("/settings/page/display"), ["Screen & Sound", "Brightness & Timeout", ""])
assert.deepEqual(at("/settings/page/data"), ["System", "Power & Data", ""])
assert.deepEqual(at("/settings/page/localization"), ["System", "Language & Units", ""])
assert.deepEqual(at("/device-preferences"), ["Developer", "Logs & Diagnostics", "Force Drive State"])
assert.deepEqual(at("/vehicle"), ["Car", "Vehicle", ""])
assert.deepEqual(at("/settings/page/wheel"), ["Car", "Wheel Controls", ""])
assert.deepEqual(at("/logs/troubleshoot"), ["Help", "Troubleshoot", ""])
assert.deepEqual(at("/troubleshoot"), ["Help", "Troubleshoot", ""])
// Tuning's tabs switch inside the page, so the addresses that open a tab are the page itself (no stale "Live plots").
for (const path of ["/tuning/flm", "/tuning/plots"]) {
  assert.deepEqual(at(path), ["Steering", "Tuning and Analysis", ""])
  assert.equal(menuBack(path), null)
}
// Old addresses of sub-pages keep the sub-page's place (breadcrumb and back link), not the parent page's.
assert.deepEqual(at("/spotify"), ["Connections", "StarPort", "Spotify and Phone Audio"])
assert.deepEqual(at("/manage_tmux"), ["Developer", "Logs & Diagnostics", "tmux Live View"])
assert.deepEqual(menuBack("/manage_tmux"), { path: "/logs", label: "Logs & Diagnostics" })
assert.deepEqual(at("/logs/help"), ["Help", "Help & Community", ""])
assert.deepEqual(at("/logs/monitor"), ["Developer", "System Monitor", ""])
assert.deepEqual(at("/settings/recovery"), ["Help", "Changed Settings", ""])
assert.deepEqual(at("/logs/crashes"), ["Developer", "Logs & Diagnostics", "Crash Reports"])
assert.equal(menuLocation("/settings"), null)

// The agreed groups, in order; every page is at most two clicks deep (no group inside a group).
const group = id => MENU.find(node => node.id === id)
assert.deepEqual(MENU.filter(node => !node.main).map(node => node.label), ["Driving Model", "Steering", "Speed", "Car", "Cameras",
  "Screen & Sound", "Connections", "Maps", "System", "Developer", "Help"])
for (const node of MENU) for (const child of node.children || []) assert.ok(!child.children, `${child.label} is a group inside ${node.label}`)
assert.deepEqual(group("steering").children.map(node => node.label), ["Lane Changes", "Lane Centering", "Always On Lateral", "Steering and Torque", "Tuning and Analysis"])
assert.deepEqual(group("speed").children.map(node => node.label), ["Driving Personalities", "Experimental & Chill", "Acceleration & Braking",
  "Curve Speed Controller", "Speed Limit Controller"])
assert.deepEqual(group("car").children.map(node => node.label), ["Vehicle", "Wheel Controls"])
assert.deepEqual(group("screen-sound").children.map(node => node.label), ["Visuals", "Colors & Layout", "Quick Select", "Brightness & Timeout", "Sounds & Alerts"])
assert.deepEqual(group("connections").children.map(node => node.label), ["Bluetooth", "StarPort", "Galaxy Access & Install"])
assert.deepEqual(group("system").children.map(node => node.label), ["Updates & Backup", "Power & Data", "Language & Units"])
assert.deepEqual(group("developer").children.map(node => node.label), ["Developer Options", "System Monitor", "Logs & Diagnostics"])
assert.deepEqual(group("help").children.map(node => node.label), ["Troubleshoot", "Changed Settings", "Help & Community"])
// Maps: the page's tabs are menu pages now (no tab bar on the page).
assert.deepEqual(group("maps").children.map(node => [node.label, node.path]), [["Navigation", "/navigation"], ["Offline Maps", "/navigation/maps"],
  ["GPS Source", "/navigation/gps"]])
// Destination and Navigation Setup are one page now: the old Setup route opens Navigation.
assert.deepEqual(at("/navigation/setup"), ["Maps", "Navigation", ""])
assert.equal(menuGroupFromPath("/settings/menu/maps").label, "Maps")
const navigationTool = tools.find(tool => tool.path === "/navigation")
assert.deepEqual(navigationTool.children.map(child => child.path), ["/navigation", "/navigation/maps", "/navigation/setup", "/navigation/gps"])
// Steering's icon is the drawn steering wheel (menu.css), not an icon-font glyph.
assert.equal(group("steering").icon, "gx-icon-steering-wheel")
assert.match(readFileSync(new URL("../web/css/menu.css", import.meta.url), "utf8"), /\.bi\.gx-icon-steering-wheel::before \{[^}]*mask: url\("data:image\/svg\+xml,/)
assert.deepEqual(PAGE_HEADINGS, { power: "Power & Data", profiles: "Acceleration & Braking", conditional: "Experimental & Chill", display: "Brightness & Timeout",
  localization: "Language & Units", traffic: "Traffic" })

// Merged pages and moved rows.
assert.equal(PAGE_COMPANIONS.profiles, undefined, "Experimental & Chill is its own page, not a card on Acceleration & Braking")
assert.deepEqual(PAGE_COMPANIONS.power, { page: "data", heading: "Data Uploads" })
assert.deepEqual(PAGE_COMPANIONS.aol, { page: "vehicle", heading: "AOL options for this car", onlyRows: ["Three-Finger AOL Tap", "Disengage AOL on Brake"] })
assert.deepEqual(ROUTE_COMPANIONS["/vehicle"], { page: "localization", heading: "Driving side", onlyRows: ["Right-Hand Drive"] })
assert.deepEqual(ROUTE_COMPANIONS["/cameras/vasm"], { page: "sounds", heading: "Blind spot alert sounds", onlyRows: ["Loud Blindspot Alert", "Blindspot Alert While Lateral Is Off"] })
assert.deepEqual(PAGE_HIDDEN_ROWS.localization, ["Right-Hand Drive"])
assert.equal(settingsSearchPath({ page: "localization", label: "Right-Hand Drive" }), "/vehicle")
assert.equal(settingsSearchPath({ page: "localization", label: "Language" }), "/settings/page/localization")
assert.equal(settingsSearchPath({ page: "conditional", label: "Saved driving mode" }), "/settings/page/conditional")
// Turn Signal Guidance sits at the end of Lane Changes' list (page-guides.js `include`), not a companion page.
assert.equal(PAGE_COMPANIONS.lane_change, undefined)
assert.deepEqual(PAGE_HIDDEN_ROWS.torque, ["Turn Signal Guidance"])
assert.equal(settingsSearchPath({ page: "torque", label: "Turn Signal Guidance" }), "/settings/page/lane_change")
assert.equal(settingsSearchPath({ page: "data", label: "Always Allow Uploads" }), "/settings/page/power")
assert.equal(settingsSearchPath({ page: "conditional/cem", label: "Stop lights" }), "/settings/page/conditional/cem")

// Routes for settings pages accept only page ids.
assert.equal(settingsPageFromPath("/settings/page/conditional/cem"), "conditional/cem")
for (const bad of ["/settings/page/", "/settings/page/../x", "/settings/page/A", "/settings/recovery", null]) assert.equal(settingsPageFromPath(bad), null)
assert.equal(menuGroupFromPath("/settings/menu/steering").label, "Steering")
for (const bad of ["/settings/menu/driving", "/settings/menu/vehicle", "/settings/menu/model-manager", "/settings/menu/nope"]) assert.equal(menuGroupFromPath(bad), null)

// Phone navigation tree and desktop back link.
const trail = path => menuTrail(path).map(item => item.label)
assert.deepEqual(trail("/settings/page/conditional/cem"), ["Settings", "Speed", "Experimental & Chill", "Experimental Conditions"])
assert.deepEqual(menuTrail("/settings/page/conditional/cem").map(item => item.id), ["/settings", "/settings/menu/speed", "/settings/page/conditional", "/settings/page/conditional/cem"])
assert.deepEqual(trail("/settings/menu/screen-sound"), ["Settings", "Screen & Sound"])
assert.deepEqual(trail("/manage_models"), ["Settings", "Driving Model"])
assert.deepEqual(trail("/navigation"), ["Settings", "Maps", "Navigation"])
assert.deepEqual(trail("/navigation/setup"), ["Settings", "Maps", "Navigation"])
assert.deepEqual(trail("/navigation/gps"), ["Settings", "Maps", "GPS Source"])
assert.deepEqual(trail("/settings"), [])
assert.deepEqual(trail("/"), [])
assert.deepEqual(menuBack("/settings/page/conditional/ccm"), { path: "/settings/page/conditional", label: "Experimental & Chill" })
assert.equal(menuBack("/settings/page/conditional"), null)

// Phone group page: one list of the group's pages; Steering and Torque hides on angle-steered cars.
assert.deepEqual(groupSections(group("steering")).map(section => [section.label, section.items.map(item => item.label)]), [
  ["", ["Lane Changes", "Lane Centering", "Always On Lateral", "Steering and Torque", "Tuning and Analysis"]],
])
menuState.torque = false
assert.ok(!groupSections(group("steering"))[0].items.some(item => item.label === "Steering and Torque"))
menuState.torque = null
assert.ok(!groupSections(group("connections"), ["/starport"])[0].items.some(item => item.label === "StarPort"))
// Phone group pages: every page is a card like the Settings tab, with its own icon and one line (MENU_CARDS).
const iconCss = readFileSync(new URL("../web/vendor/bootstrap-icons/bootstrap-icons.min.css", import.meta.url), "utf8")
const leaves = MENU.flatMap(node => node.children || [])
for (const leaf of leaves) {
  const card = menuCard(leaf)
  assert.ok(MENU_CARDS[leaf.id], `${leaf.label} has no card`)
  // A Bootstrap icon, or the steering wheel drawn in menu.css.
  assert.ok(iconCss.includes(`.${card.icon}::before`) || card.icon === "gx-icon-steering-wheel", `${leaf.label}: icon ${card.icon} is not in Bootstrap Icons`)
  assert.ok(card.summary && card.summary.length <= 45, `${leaf.label}: one short line`)
}
assert.deepEqual(Object.keys(MENU_CARDS).sort(), leaves.map(leaf => leaf.id).sort(), "no cards for pages that are not in the menu")
assert.match(MenuGroupPage.template, /<section v-for="item in section\.items" :key="item\.id" class="gx-card gx-menu-card">/)
assert.match(MenuGroupPage.template, /class="gx-menu-card__icon"><i class="bi" :class="card\(item\)\.icon"/)
assert.match(MenuGroupPage.template, /class="gx-menu-card__summary">\{\{ card\(item\)\.summary \}\}/)
assert.doesNotMatch(MenuGroupPage.template, /gx-menu-link/)
assert.deepEqual(["lane-changes", "lane-centering", "aol"].map(id => MENU_CARDS[id][0]), ["bi-sign-merge-right", "bi-arrows-collapse-vertical", "gx-icon-steering-wheel"])
// Phone Settings tab: the Driving Model card describes itself (it is a page, not a group).
const cards = SettingsIndex.computed.cards.call({ hiddenPaths: [] })
assert.deepEqual(cards.map(card => card.label), MENU.filter(node => !node.main).map(node => node.label))
assert.equal(cards[0].path, "/manage_models")
assert.match(cards[0].summary, /driving models/)

// Desktop tree: groups only open (one branch at a time); the open page keeps its branch open.
const tree = { path: "/settings/page/slc", go: () => {}, hiddenPaths: [], expanded: [] }
tree.here = MenuTree.computed.here.call(tree)
MenuTree.watch.here.handler.call(tree, tree.here)
assert.deepEqual(tree.expanded, ["speed"])
let opened = null
const view = { ...tree, go: path => { opened = path } }
MenuTree.methods.select.call(view, { id: "screen-sound", group: true, open: false })
assert.deepEqual(view.expanded, ["screen-sound"], "opening a group closes the others")
MenuTree.methods.select.call(view, { id: "display", group: false })
assert.equal(opened, "/settings/page/display")
const rows = MenuTree.computed.rows.call({ ...tree, here: "slc", expanded: ["speed"] })
assert.deepEqual(rows.filter(row => row.level === 1).map(row => row.label), MENU.filter(node => !node.main).map(node => node.label))
assert.ok(!rows.some(row => row.level > 2), "two levels at most")
assert.ok(rows.find(row => row.id === "slc").active)
assert.ok(!rows.some(row => row.label === "Experimental Conditions"), "sub-pages are not in the menu")

// Phone bar: Home · Settings · Routes; every page that is not Home or Routes sits under Settings.
assert.deepEqual(NAV.map(item => item.name), ["Home", "Settings", "Routes"])
assert.equal(primaryTab("/"), 0)
assert.equal(primaryTab("/recordings/abc"), 2)
for (const path of ["/settings", "/settings/page/slc", "/cameras", "/tools"]) assert.equal(primaryTab(path), 1)

// App wiring.
const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
assert.match(app, /<MenuTree :path="route\.path" :go="goFromMenu"/)
assert.doesNotMatch(app, /togglePin|gx-drawer__pin|gx-appbar__menu/)
assert.match(app, /<SettingsIndex v-else-if="route\.path === '\/settings'"/)
assert.match(app, /<MenuGroupPage v-else-if="menuGroup"/)
assert.match(app, /menu-mode embedded/)
// A hidden tool hides all its pages from the menu (e.g. all four Maps pages without Navigation & Maps).
assert.match(app, /hiddenToolPaths\(\) \{[^}]*flatMap\(tool => \[tool\.path, \.\.\.\(tool\.children \|\| \[\]\)\.map\(child => child\.path\)\]\)/)
assert.match(app, /'\/navigation\/gps'\]\.includes\(route\.path\)/)
// Vehicle and Blind Spot Warnings show their borrowed rows below the page (ROUTE_COMPANIONS).
for (const path of ["/vehicle", "/cameras/vasm"]) {
  const start = app.indexOf(`<template v-else-if="route.path === '${path}'">`)
  assert.ok(start > 0, path)
  const block = app.slice(start, app.indexOf("</template>", start))
  assert.match(block, /:initial-page="routeCompanion\.page"/)
  assert.match(block, /:only-rows="routeCompanion\.onlyRows"/)
}
// No settings page shows the old Toggles category bar any more, however it is reached (Developer Options opens the developer section).
const settingsSource = readFileSync(new URL("../web/js/settings.js", import.meta.url), "utf8")
assert.doesNotMatch(settingsSource, /class="gx-settings-tabs"/)
// Borrowed rows this car does not have show nothing (no empty card), e.g. Tesla AOL rows on another car.
assert.match(settingsSource, /<section v-else-if="!emptyBorrow" class="gx-settings"/)
const emptyBorrow = (props) => SettingsPage.computed.emptyBorrow.call({ onlyRows: ["Three-Finger AOL Tap"], state: { status: "idle", data: {}, error: "" },
  visibleRows: [], ...props })
assert.equal(emptyBorrow(), true, "no borrowed rows on this car: nothing shown")
assert.equal(emptyBorrow({ visibleRows: [{ row: { label: "Three-Finger AOL Tap" } }] }), false)
assert.equal(emptyBorrow({ onlyRows: null }), false, "a normal page is never hidden")
assert.equal(emptyBorrow({ state: { status: "loading", data: null, error: "" } }), false, "still loading: keep the loading state")
assert.equal(emptyBorrow({ state: { status: "idle", data: {}, error: "offline" } }), false, "errors still show")
// Borrowed Sounds rows do not bring the sound pack catalog with them.
assert.match(settingsSource, /<template v-if="!onlyRows"><SoundPacks v-if="state\.data\.page === 'sounds'"/)
assert.match(app, /v-else-if="route\.path === '\/developer\/connect'"[^>]*initial-section="developer"\s*menu-mode/)
const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8")
assert.match(html, /css\/menu\.css/)
console.log("Galaxy menu: every settings page and tool has a place")
