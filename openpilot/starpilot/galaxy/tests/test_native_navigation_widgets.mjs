import { compile } from "../web/vendor/vue/vue.esm-browser.js"
import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { LayoutWidgetPreview, OnroadLayoutPage, validSnapshot, validDocument, widgetSize, clampPlacement } from "../web/js/onroad-layout.js"

// Use the server's real registry so a UI gate cannot silently reject a newly exposed widget.
const data = JSON.parse(execFileSync(process.env.PYTHON || "python3", ["-c", `
import json
from openpilot.starpilot.ui.onroad_customization import customization_metadata, default_document
print(json.dumps(dict(document=default_document(), defaults=default_document(), metadata=customization_metadata(),
                     revision="native", editable=True, valid=True, activeProfile="large", supportedProfiles=["large"])))
`], { encoding: "utf8" }))
assert.equal(validSnapshot(data), true)
const signalEdges = JSON.parse(execFileSync(process.env.PYTHON || "python3", ["-c", `
import json
from openpilot.starpilot.ui.onroad_customization import SIGNAL_SIZES, default_document, signal_size, validate_document, widget_bounds
documents = []
for style in SIGNAL_SIZES:
  document = default_document()
  placed = document['layouts']['large']['network_signal']
  placed.update(size=style, scale=50, enabled=True, wifiName=True)
  bounds = widget_bounds('large', 'network_signal')
  width, height = signal_size(placed)
  placed.update(x=bounds['x'] + bounds['width'] - width, y=bounds['y'] + bounds['height'] - height)
  documents.append(validate_document(document))
print(json.dumps(documents))
`], { encoding: "utf8" }))
for (const document of signalEdges) assert.equal(validDocument(document, data.metadata), true)
assert.equal(validSnapshot(data), true)
assert.equal(data.document.version, 16)
for (const version of [9, 10, 15, 17]) {
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

// Use actual native geometry for source growth, bounds, and the transient timer.
assert.deepEqual(data.metadata.profiles.large.widgets.cruise_limits.sourcesExtent, {inline:114, card:122})
vm.state.draft = structuredClone(data.document)
vm.state.selected = "cruise_limits"
vm.setSpeedSources("speedSources", true)
vm.setSpeedSources("speedSourcesCard", true)
assert.equal(vm.layout.cruise_limits.y, 75)
vm.setSpeedSources("speedSources", false)
const overlapping = structuredClone(data.document)
overlapping.layouts.large.cruise_limits.y = 550
assert.equal(validDocument(overlapping, data.metadata), false)
// Disable torque while testing sources at the lower screen edge.
vm.layout.torque_bar.enabled = false
vm.layout.cruise_limits.x = 1300
vm.layout.cruise_limits.y = 690
vm.setSpeedSources("speedSources", true)
assert.equal(vm.layout.cruise_limits.y, 1050 - vm.profile.widgets.cruise_limits.height)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
const beyondNewPromptWidth = JSON.parse(JSON.stringify(vm.state.draft))
beyondNewPromptWidth.layouts.large.cruise_limits.x = 1430
assert.equal(validDocument(beyondNewPromptWidth, data.metadata), false)
vm.setSpeedSources("speedSources", false)
const signal = vm.profile.widgets.network_signal, signalBounds = signal.bounds || vm.profile.bounds
assert.equal(signal.label, "Connections")
vm.layout.network_signal.enabled = true
vm.state.selected = "network_signal"
vm.setWidgetSize("horizontal")
assert.deepEqual(widgetSize(signal, vm.layout.network_signal), signal.sizes.horizontal)
assert.ok(vm.layout.network_signal.x + signal.sizes.horizontal[0] <= signalBounds.x + signalBounds.width)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
vm.setWidgetSize("vertical")
assert.deepEqual(widgetSize(signal, vm.layout.network_signal), signal.sizes.vertical)
// Every style shrinks from full size down to 30%; growing back pulls it inside the screen.
assert.deepEqual(signal.scale, {min: 30, max: 100, default: 100})
assert.equal(vm.layout.network_signal.scale, 100)
assert.equal(vm.layout.network_signal.bluetooth, true)
vm.setWidgetToggle("bluetooth", "Off")
assert.equal(vm.layout.network_signal.bluetooth, false)
vm.setWidgetToggle("bluetooth", "On")
assert.equal(vm.layout.network_signal.bluetooth, true)
for (const style of ["ring", "horizontal", "vertical"]) {
  vm.setWidgetSize(style)
  for (const scale of [30, 50, 100]) {
    vm.scaleInput({ target: { value: String(scale) } })
    assert.deepEqual(widgetSize(signal, vm.layout.network_signal), signal.sizes[style].map(side => Math.round(side * scale / 100)))
    assert.equal(validDocument(vm.state.draft, data.metadata), true)
  }
}
vm.scaleInput({ target: { value: "30" } })
vm.scaleInput({ target: { value: "25" } })
assert.equal(vm.layout.network_signal.scale, 30)
vm.layout.network_signal.x = signalBounds.x + signalBounds.width - widgetSize(signal, vm.layout.network_signal)[0]
vm.scaleInput({ target: { value: "100" } })
assert.ok(vm.layout.network_signal.x + signal.sizes.vertical[0] <= signalBounds.x + signalBounds.width)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
const badScale = JSON.parse(JSON.stringify(vm.state.draft))
badScale.layouts.large.network_signal.scale = 20
assert.equal(validDocument(badScale, data.metadata), false)
vm.state.profile = "compact"
vm.add("stopped_timer")
assert.equal(vm.layout.stopped_timer.enabled, true)
assert.equal(validDocument(vm.state.draft, data.metadata), true)
assert.deepEqual(clampPlacement(vm.profile, "steering_wheel", 146, 44, vm.layout), {x:146, y:44})
console.log("Native Big widgets: real metadata, optional add/move/remove, map resize/opacity and undo passed")

// C4 has the same independent native navigation switch; StarPort is another document.
const compact = structuredClone(data)
compact.activeProfile = "compact"
compact.supportedProfiles = ["compact"]
assert.equal(validSnapshot(compact), true)
assert.equal(compact.document.layouts.compact.nav_card.enabled, true)
compact.document.layouts.compact.nav_card.enabled = false
assert.equal(validDocument(compact.document, compact.metadata), true)
assert.equal(compact.document.layouts.large.nav_card.enabled, true)

// Exercise the rendered fallback, not just the stored toggle value.
const renderSignal = compile(LayoutWidgetPreview.template, { decodeEntities: value => value.replaceAll("&amp;", "&") })
function previewText(node) {
  if (!node || typeof node !== "object") return ""
  if (Array.isArray(node)) return node.map(previewText).join(" ")
  return typeof node.children === "string" ? node.children : previewText(node.children)
}
for (const size of ["ring", "horizontal", "vertical"]) {
  for (const bluetooth of [true, false]) {
    const tree = renderSignal({ widget: { kind: "network_signal", size, width: 268, height: 196, bluetooth },
      palette: {}, profile: "large", scene: "engaged", sprite: null, native: false }, [])
    const text = previewText(tree)
    assert.equal(text.includes("iPhone 15"), bluetooth, `${size} Bluetooth preview follows the toggle`)
    if (!bluetooth) assert(!text.includes("Connected") && !text.includes("AirPods"))
  }
}
