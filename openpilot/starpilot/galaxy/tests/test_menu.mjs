import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { MENU, menuLocation, menuTrail, menuBack, menuGroupFromPath, settingsPageFromPath, settingsPagePath, settingsSearchPath,
  groupSections, menuState, PAGE_COMPANIONS, PAGE_HIDDEN_ROWS, MenuTree } from "../web/js/menu.js"
import { SETTINGS_SECTIONS } from "../web/js/settings.js"
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
assert.deepEqual(at("/settings/page/lane_change"), ["Driving", "Lane Changes", ""])
assert.deepEqual(at("/settings/page/slc"), ["Driving", "Speed Limit Controller", ""])
assert.deepEqual(at("/settings/page/conditional/cem"), ["Driving", "Long Planner", "Experimental Conditions"])
assert.deepEqual(at("/settings/page/traffic"), ["Driving", "Driving Personalities", "Traffic"])
// Each personality's own pages (custom following, acceleration, braking) sit under that personality.
for (const name of ["aggressive", "standard", "relaxed", "traffic"]) {
  for (const [page, label] of [["custom_following", "Custom following"], ["acceleration", "Acceleration"], ["braking", "Braking"]]) {
    const title = name[0].toUpperCase() + name.slice(1)
    assert.deepEqual(at(`/settings/page/${name}/${page}`), ["Driving", "Driving Personalities", `${title} › ${label}`])
    assert.deepEqual(menuBack(`/settings/page/${name}/${page}`), { path: `/settings/page/${name}`, label: title })
  }
}
assert.deepEqual(menuTrail("/settings/page/standard/braking").map(item => item.label), ["Settings", "Driving", "Driving Personalities", "Standard", "Braking"])
assert.deepEqual(menuTrail("/settings/page/standard/braking").map(item => item.id),
  ["/settings", "/settings/menu/driving", "/settings/page/personalities", "/settings/page/standard", "/settings/page/standard/braking"])
assert.deepEqual(at("/theme_maker/starport"), ["Controls & Connections", "StarPort", "StarPort layout"])
assert.deepEqual(at("/cameras/pip"), ["Device & Visuals", "Cameras & Monitoring", "Blind Spot Camera and Preview"])
assert.deepEqual(at("/settings/page/data"), ["Device & Visuals", "Power & Data", ""])
assert.deepEqual(at("/device-preferences"), ["Developer", "Logs & Diagnostics", "Force Drive State"])
assert.deepEqual(at("/vehicle"), ["", "Vehicle", ""])
assert.equal(menuLocation("/settings"), null)

// Driving holds Model Manager, Steering and Cruise; Cruise is in the agreed order.
const driving = MENU.find(node => node.id === "driving")
assert.deepEqual(driving.children.map(node => node.label), ["Model Manager", "Steering", "Cruise"])
assert.deepEqual(driving.children[1].children.map(node => node.label), ["Lane Changes", "Lane Centering", "Always On Lateral", "Steering and Torque", "Tuning and Analysis"])
assert.deepEqual(driving.children[2].children.map(node => node.label), ["Long Planner", "Driving Personalities", "Curve Speed Controller", "Speed Limit Controller"])

// Merged pages and moved rows.
assert.deepEqual(PAGE_COMPANIONS.profiles, { page: "conditional", heading: "Conditional Driving Modes" })
assert.deepEqual(PAGE_COMPANIONS.power, { page: "data", heading: "Data Uploads" })
// Turn Signal Guidance sits at the end of Lane Changes' list (page-guides.js `include`), not a companion page.
assert.equal(PAGE_COMPANIONS.lane_change, undefined)
assert.deepEqual(PAGE_HIDDEN_ROWS.torque, ["Turn Signal Guidance"])
assert.equal(settingsSearchPath({ page: "torque", label: "Turn Signal Guidance" }), "/settings/page/lane_change")
assert.equal(settingsSearchPath({ page: "data", label: "Always Allow Uploads" }), "/settings/page/power")
assert.equal(settingsSearchPath({ page: "conditional/cem", label: "Stop lights" }), "/settings/page/conditional/cem")

// Routes for settings pages accept only page ids.
assert.equal(settingsPageFromPath("/settings/page/conditional/cem"), "conditional/cem")
for (const bad of ["/settings/page/", "/settings/page/../x", "/settings/page/A", "/settings/recovery", null]) assert.equal(settingsPageFromPath(bad), null)
assert.equal(menuGroupFromPath("/settings/menu/driving").label, "Driving")
for (const bad of ["/settings/menu/steering", "/settings/menu/vehicle", "/settings/menu/nope"]) assert.equal(menuGroupFromPath(bad), null)

// Phone navigation tree and desktop back link.
const trail = path => menuTrail(path).map(item => item.label)
assert.deepEqual(trail("/settings/page/conditional/cem"), ["Settings", "Driving", "Long Planner", "Experimental Conditions"])
assert.deepEqual(menuTrail("/settings/page/conditional/cem").map(item => item.id), ["/settings", "/settings/menu/driving", "/settings/page/profiles", "/settings/page/conditional/cem"])
assert.deepEqual(trail("/settings/menu/device"), ["Settings", "Device & Visuals"])
assert.deepEqual(trail("/navigation"), ["Settings", "Maps"])
assert.deepEqual(trail("/settings"), [])
assert.deepEqual(trail("/"), [])
assert.deepEqual(menuBack("/settings/page/conditional/ccm"), { path: "/settings/page/profiles", label: "Long Planner" })
assert.equal(menuBack("/settings/page/profiles"), null)

// Phone group page: own pages first, then each sub-group; Steering and Torque hides on angle-steered cars.
assert.deepEqual(groupSections(driving).map(section => [section.label, section.items.map(item => item.label)]), [
  ["", ["Model Manager"]],
  ["Steering", ["Lane Changes", "Lane Centering", "Always On Lateral", "Steering and Torque", "Tuning and Analysis"]],
  ["Cruise", ["Long Planner", "Driving Personalities", "Curve Speed Controller", "Speed Limit Controller"]],
])
menuState.torque = false
assert.ok(!groupSections(driving)[1].items.some(item => item.label === "Steering and Torque"))
menuState.torque = null
const device = MENU.find(node => node.id === "device")
assert.deepEqual(groupSections(device).map(section => section.label), ["", "Driving Screen", "Cameras"])
assert.ok(!groupSections(MENU.find(node => node.id === "controls"), ["/starport"])[0].items.some(item => item.label === "StarPort"))

// Desktop tree: groups only open (one branch at a time); the open page keeps its branch open.
const tree = { path: "/settings/page/slc", go: () => {}, hiddenPaths: [], expanded: [] }
tree.here = MenuTree.computed.here.call(tree)
MenuTree.watch.here.handler.call(tree, tree.here)
assert.deepEqual(tree.expanded, ["driving", "cruise"])
let opened = null
const view = { ...tree, go: path => { opened = path } }
MenuTree.methods.select.call(view, { id: "device", group: true, open: false })
assert.deepEqual(view.expanded, ["device"], "opening a group closes the others")
MenuTree.methods.select.call(view, { id: "display", group: false })
assert.equal(opened, "/settings/page/display")
const rows = MenuTree.computed.rows.call({ ...tree, here: "slc", expanded: ["driving", "cruise"] })
assert.deepEqual(rows.filter(row => row.level === 1).map(row => row.label), ["Driving", "Vehicle", "Controls & Connections", "Device & Visuals", "Software", "Maps", "Developer"])
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
// No settings page shows the old Toggles category bar any more, however it is reached (Developer Options opens the developer section).
const settingsSource = readFileSync(new URL("../web/js/settings.js", import.meta.url), "utf8")
assert.doesNotMatch(settingsSource, /class="gx-settings-tabs"/)
assert.match(app, /v-else-if="route\.path === '\/developer\/connect'"[^>]*initial-section="developer"\s*menu-mode/)
const html = readFileSync(new URL("../web/index.html", import.meta.url), "utf8")
assert.match(html, /css\/menu\.css/)
console.log("Galaxy menu: every settings page and tool has a place")
