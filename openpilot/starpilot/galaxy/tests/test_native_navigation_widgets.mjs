import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { OnroadLayoutPage, validSnapshot, validDocument, widgetSize, clampPlacement } from "../web/js/onroad-layout.js"

// Use the server's real registry so a UI gate cannot silently reject a newly exposed widget.
const data = JSON.parse(execFileSync(process.env.PYTHON || "python3", ["-c", `
import json
from openpilot.starpilot.ui.onroad_customization import customization_metadata, default_document
print(json.dumps(dict(document=default_document(), defaults=default_document(), metadata=customization_metadata(),
                     revision="native", editable=True, valid=True, activeProfile="large", supportedProfiles=["large"])))
`], { encoding: "utf8" }))
assert.equal(validSnapshot(data), true)
for (const version of [11, 12, 13, 14]) {
  assert.equal(validSnapshot({ ...data, document: { ...data.document, version } }), true)
  assert.equal(validSnapshot({ ...data, defaults: { ...data.defaults, version } }), true)
}
for (const version of [9, 10, 15]) {
  assert.equal(validSnapshot({ ...data, document: { ...data.document, version } }), false)
  assert.equal(validSnapshot({ ...data, defaults: { ...data.defaults, version } }), false)
}
const vm = { ...OnroadLayoutPage.setup({ mode: "local", unauthorized() {} }), $emit() {}, $refs: {} }
for (const [key, getter] of Object.entries(OnroadLayoutPage.computed)) Object.defineProperty(vm, key, { get: () => getter.call(vm) })
for (const [key, method] of Object.entries(OnroadLayoutPage.methods)) vm[key] = method.bind(vm)
for (const feed of [vm.artFeed, vm.backdropFeed]) {
  feed.start = () => { feed.active = true }
  feed.update = () => {}
  feed.stop = () => { feed.active = false }
}
vm.state.data = data
vm.state.draft = structuredClone(data.document)
vm.state.status = "ready"
for (const [sidebar, y, height] of [["On", 900, 150], ["Off", 880, 170]]) {
  vm.state.draft.display = { DeveloperSidebar: sidebar }
  assert.deepEqual(vm.favoriteZones, [{ id: "favorites_drawer", label: "Quick Select drawer", x:30, y, width:340, height,
    excludes:data.metadata.profiles.large.inputZones[0].excludes }])
}
vm.state.draft = structuredClone(data.document)
for (const id of ['current_speed', 'cruise_limits', 'steering_wheel', 'driver_monitor'])
  assert(vm.favoriteZones[0].excludes.includes(id))
for (const id of ['nav_map', 'pip_left', 'pip_right', 'torque_bar']) assert(!vm.favoriteZones[0].excludes.includes(id))
assert.deepEqual(vm.quickSelectShelves.map(({x,y,width,height}) => [x,y,width,height]), [[30,842,656,208], [30,378,208,672]])
assert.equal(vm.quickSelectBlocked, false)
Object.assign(vm.layout.current_speed, {x:470, y:730})
assert(vm.quickSelectShelves[0].conflicts.includes('current_speed'))
assert(!vm.quickSelectShelves[1].conflicts.includes('current_speed'))
Object.assign(vm.layout.current_speed, {x:50, y:730})
assert.equal(vm.quickSelectBlocked, true)
vm.state.draft = structuredClone(data.document)
const savedLayouts = JSON.parse(JSON.stringify(vm.state.draft.layouts))
vm.setReduceMotion(true)
assert.equal(vm.state.draft.reduceMotion, true)
vm.state.profile = 'compact'
vm.setReduceMotion(false)
assert.equal(vm.state.draft.reduceMotion, true)
vm.state.profile = 'large'
assert.deepEqual(vm.state.draft.layouts, savedLayouts)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
assert.equal(validDocument({...vm.state.draft, reduceMotion: 'true'}, data.metadata), false)
const malformedShelves = structuredClone(data)
malformedShelves.metadata.profiles.large.quickSelectShelves[0].width = NaN
assert.equal(validSnapshot(malformedShelves), false)
vm.state.draft = structuredClone(data.document)
assert.equal(data.document.layouts.large.nav_card.enabled, true, "the native turn card stays on after an upgrade")
assert(!vm.inactiveWidgets.some(widget => widget.id === "nav_card"))
vm.changePosition("nav_card", 100, 300)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
for (const id of ["nav_map", "stopped_timer", "jetlink_status"]) {
  assert(vm.inactiveWidgets.some(widget => widget.id === id))
  vm.add(id)
  assert.equal(vm.layout[id].enabled, true)
  vm.changePosition(id, 100, 300)
  assert.equal(vm.layout[id].x, 100)
  assert.equal(validDocument(vm.state.draft, data.metadata), true)
}
vm.state.selected = "nav_map"
vm.boxInput("width", { target: { value: "900" } })
vm.boxInput("height", { target: { value: "500" } })
vm.opacityInput({ target: { value: "35" } })
assert.deepEqual(widgetSize(vm.selectedWidget, vm.selectedPosition), [900, 500])
assert.equal(vm.layout.nav_map.opacity, 35)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
vm.state.selected = "jetlink_status"
vm.opacityInput({ target: { value: "40" } })
assert.equal(vm.layout.jetlink_status.opacity, 40)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
vm.state.selected = "nav_map"
vm.remove("nav_map")
assert.equal(vm.layout.nav_map.enabled, false)
vm.undo()
assert.equal(vm.layout.nav_map.enabled, true)
assert.equal(vm.layout.nav_map.opacity, 35)
assert.equal(validSnapshot({ ...data, document: vm.state.draft }), true)

// Use actual native geometry for source attachment, bounds, and the transient timer.
assert.deepEqual(data.metadata.profiles.large.widgets.cruise_limits.sourcesExtent, {inline:104, card:112, actionGap:8})
vm.state.draft = structuredClone(data.document)
vm.state.selected = "cruise_limits"
vm.setSpeedSources("speedSources", true)
assert.equal(vm.layout.speed_limit_actions.y, 381)
vm.setSpeedSources("speedSourcesCard", true)
assert.equal(vm.layout.speed_limit_actions.y, 389)
vm.setSpeedSources("speedSources", false)
assert.equal(vm.layout.speed_limit_actions.y, 267)
const overlapping = structuredClone(data.document)
overlapping.layouts.large.cruise_limits.y = 550
assert.equal(validDocument(overlapping, data.metadata), false)
// Disable torque while testing source attachment at the lower screen edge.
vm.layout.torque_bar.enabled = false
vm.layout.cruise_limits.x = 1430
vm.layout.speed_limit_actions.x = 1454
vm.layout.cruise_limits.y = 690
vm.layout.speed_limit_actions.y = 882
vm.setSpeedSources("speedSources", true)
assert.equal(vm.layout.cruise_limits.y, 568)
assert.equal(vm.layout.speed_limit_actions.y, 882)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
Object.assign(vm.layout.speed_limit_actions, {x:600, y:500})
vm.setSpeedSources("speedSources", false)
assert.deepEqual([vm.layout.speed_limit_actions.x, vm.layout.speed_limit_actions.y], [600, 500])
vm.state.profile = "compact"
vm.add("stopped_timer")
assert.equal(vm.layout.stopped_timer.enabled, true)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
assert.deepEqual(clampPlacement(vm.profile, "speed_limit_actions", 146, 44, vm.layout), {x:146, y:44})
console.log("Native Big widgets: real metadata, optional add/move/remove, map resize/opacity and undo passed")
