import assert from "node:assert/strict"
import * as documentHelpers from "../web/js/onroad-layout-document.js"
import * as layoutModule from "../web/js/onroad-layout.js"
import { ONROAD_LAYOUT_TEMPLATE } from "../web/js/onroad-layout-template.js"
import { readFileSync } from "node:fs"
import { LayoutWidgetPreview, OnroadLayoutFeed, OnroadLayoutPage, validSnapshot, validDocument, clampPlacement, previewPoint, withAlpha, widgetPalette,
  withDisplay, layoutDocument, previewDocument } from "../web/js/onroad-layout.js"
import { SettingsPage, SETTINGS_SECTIONS } from "../web/js/settings.js"
import { loadCatalog } from "../web/js/startup.js"

const copy = (data) => JSON.parse(JSON.stringify(data))
const widget = (label, kind, width, height, x, y) => ({ label, kind, width, height, default: { x, y, enabled: true } })
export function snapshot() {
  const metadata = { profiles: {
    large: { label: "Large UI", width: 2160, height: 1080, bounds: { x: 30, y: 30, width: 1800, height: 1020 }, reservedZones: [], widgets: {
      current_speed: widget("Current speed", "current_speed", 580, 300, 640, 30),
      cruise_limits: widget("Cruise and speed limit", "cruise_limits", 480, 522, 88, 75),
      speed_limit_actions: widget("Speed limit actions", "speed_limit_actions", 480, 108, 88, 567),
      steering_wheel: widget("Steering wheel", "steering_wheel", 192, 192, 1588, 75),
      driver_monitor: widget("Driver monitoring", "driver_monitor", 192, 192, 88, 808),
      torque_bar: { ...widget("Torque bar", "torque_bar", 998, 240, 439, 759), layer: "underlay" },
    } },
    compact: { label: "Small UI", width: 536, height: 240, bounds: { x: 0, y: 0, width: 476, height: 240 },
      protectedWidget: "speed_limit_actions", reservedZones: [], widgets: {
      max_speed: widget("Maximum speed", "max_speed", 162, 162, 0, 0),
      speed_limit: widget("Speed limit", "speed_limit", 118, 132, 330, 20),
      speed_limit_actions: widget("Speed limit actions", "speed_limit_actions", 282, 54, 174, 180),
      steering_wheel: widget("Steering wheel", "steering_wheel", 50, 50, 21, 176),
      driver_monitor: widget("Driver monitoring", "driver_monitor", 60, 60, 16, 10),
      torque_bar: { ...widget("Torque bar", "torque_bar", 260, 61, 116, 167), layer: "underlay" },
      model_confidence: { ...widget("Model confidence", "model_confidence", 60, 80, 476, 0), bounds: { x: 0, y: 0, width: 536, height: 240 } },
      conditional_mode: { ...widget("CEM and driving mode", "conditional_mode", 60, 80, 476, 80), bounds: { x: 0, y: 0, width: 536, height: 240 } },
      following_distance: { ...widget("Following distance", "following_distance", 60, 80, 476, 160), bounds: { x: 0, y: 0, width: 536, height: 240 } },
    } },
  }, paletteFields: [
    { id: "cardFill", label: "Card fill", default: "#000000A6" },
    { id: "cardBorder", label: "Card border", default: "#C4CDD0B4" },
    { id: "text", label: "Text", default: "#FFFFFFFF" },
  ] }
  const frame = { cardFill: "#00000000", cardBorder: "#00000000" }
  const actions = { cardFill: "#0C1820EB", cardBorder: "#A6DFBEFF", text: "#FFFFFFFF" }
  const colors = {
    large: { current_speed: { text: null }, cruise_limits: { cardFill: null, cardBorder: null, text: null },
      speed_limit_actions: actions, steering_wheel: { cardFill: null, cardBorder: "#00000000" }, driver_monitor: frame },
    compact: { max_speed: { text: null }, speed_limit_actions: actions, driver_monitor: frame, model_confidence: frame,
      conditional_mode: frame, following_distance: { ...frame, text: "#FFFFFFFF" } },
  }
  for (const [profile, data] of Object.entries(metadata.profiles))
    for (const [id, widget] of Object.entries(data.widgets)) widget.colors = { ...colors[profile][id] }
  metadata.profiles.large.widgets.steering_wheel.resizable = { min: 144, default: 192, max: 240 }
  // As the server sends them: anchors for the sidebar-hidden road, and positions that may use its extra width.
  const shifts = { steering_wheel: 1, current_speed: .5, torque_bar: .5 }
  for (const [id, shift] of Object.entries(shifts)) metadata.profiles.large.widgets[id].anchorShift = shift
  for (const id of Object.keys(metadata.profiles.large.widgets))
    Object.assign(metadata.profiles.large.widgets[id], { expandable: true, bounds: { x: 30 - 300 * (shifts[id] || 0), y: 30, width: 2100, height: 1020 } })
  metadata.profiles.compact.widgets.steering_wheel.resizable = { min: 40, default: 50, max: 70 }
  metadata.roadColorFields = [{ id: 'path', label: 'Path', default: '#30FF9CFF' },
    { id: 'pathEdge', label: 'Path edges', default: '#00FF40FF' }, { id: 'laneLines', label: 'Lane lines', default: '#FFFFFFFF' }]
  metadata.profiles.compact.widgets.speed_limit_actions.visualInsetTop = 32
  const document = { version: 11, roadColors: { large: {}, compact: {} }, widgetColors: { large: {}, compact: {} }, palette: Object.fromEntries(metadata.paletteFields.map((field) => [field.id, field.default])),
    layouts: Object.fromEntries(Object.entries(metadata.profiles).map(([id, profile]) => [id,
      Object.fromEntries(Object.entries(profile.widgets).map(([key, definition]) => [key,
        { ...definition.default, ...(definition.resizable ? { size: definition.resizable.default } : {}) }]))])) }
  return { document, defaults: copy(document), metadata, revision: "saved-revision", editable: true, valid: true, activeProfile: null }
}

// The editor keeps its public API while sharing the pure document helpers.
for (const name of ["layoutDocument", "previewDocument", "withDisplay", "orderedWidgetIds", "widgetPalette", "withAlpha", "widgetSize", "placementLimits", "clampBox", "clampPlacement", "overlapsReserved", "previewPoint", "validDocument", "validSnapshot"])
  assert.equal(layoutModule[name], documentHelpers[name], name)

const data = snapshot()
const restoredCompact = copy(data)
for (const [id, width, height, x, y] of [["max_speed", 162, 162, 0, 0], ["speed_limit", 118, 132, 330, 20], ["speed_limit_actions", 348, 54, 128, 186]]) {
  Object.assign(restoredCompact.metadata.profiles.compact.widgets[id], { width, height, default: {x, y, enabled:true} })
  restoredCompact.document.layouts.compact[id] = {x, y, enabled:true}
}
delete restoredCompact.metadata.profiles.compact.widgets.speed_limit_actions.visualInsetTop
restoredCompact.defaults = copy(restoredCompact.document)
assert.equal(validSnapshot(restoredCompact), true, "Restored small-UI defaults are editable")
assert.equal(clampPlacement(restoredCompact.metadata.profiles.compact, "max_speed", 174, 25, restoredCompact.document.layouts.compact), null)
assert.deepEqual(clampPlacement(restoredCompact.metadata.profiles.compact, "speed_limit", 330, 20, restoredCompact.document.layouts.compact), {x:330, y:20})
assert.deepEqual(restoredCompact.document.layouts.large, data.document.layouts.large)
const withMode = copy(data)
const modeId = "driving_mode_descriptions"
withMode.metadata.profiles.compact.widgets[modeId] = {
  ...widget("Driving Mode Descriptions", modeId, 168, 60, 162, 22), colors: { text: "#FFFFFFFF" },
}
withMode.document.layouts.compact[modeId] = { x: 162, y: 22, enabled: false }
withMode.document.layouts.compact.speed_limit_actions.y = 45
withMode.defaults = copy(withMode.document)
assert.equal(validSnapshot(withMode), true)
assert.equal(clampPlacement(withMode.metadata.profiles.compact, modeId, 162, 22, withMode.document.layouts.compact), null)
withMode.document.layouts.compact[modeId].enabled = true
assert.equal(validDocument(withMode.document, withMode.metadata), false)
withMode.document.layouts.compact[modeId].y = 110
assert.equal(validDocument(withMode.document, withMode.metadata), true)

// Hidden widgets free their space, but adding them back still requires a valid placement.
{
  const sample = copy(data), layout = sample.document.layouts.compact, profile = sample.metadata.profiles.compact
  layout.max_speed.enabled = false
  assert.deepEqual(clampPlacement(profile, "speed_limit_actions", 80, 80, layout), { x: 80, y: 80 })
  Object.assign(layout.speed_limit_actions, { x: 80, y: 80 })
  assert.equal(validSnapshot(sample), true)
  assert.equal(clampPlacement(profile, "max_speed", 0, 0, layout), null)
  layout.max_speed.enabled = true
  assert.equal(validDocument(sample.document, sample.metadata), false)
  assert.equal(clampPlacement(profile, "speed_limit_actions", 80, 80, layout), null)
  layout.max_speed.enabled = false
  layout.max_speed.x = -1
  assert.equal(validDocument(sample.document, sample.metadata), false)
  layout.max_speed.x = 0
  layout.max_speed.enabled = "false"
  assert.equal(validDocument(sample.document, sample.metadata), false)
}

assert.equal(withAlpha("#AABBCC80", 200 / 255), "#AABBCC64")
assert.equal(withAlpha("#AABBCCFF", .9), "#AABBCCe5")
assert.equal(validSnapshot(data), true)
assert.deepEqual(clampPlacement(data.metadata.profiles.compact, "torque_bar", 174, 179), { x: 174, y: 179 })
const registeredUnderlay = copy(data.metadata.profiles.compact)
registeredUnderlay.widgets.max_speed.layer = "underlay"
assert.deepEqual(clampPlacement(registeredUnderlay, "max_speed", 174, 19), { x: 174, y: 19 })
const alternateUnderlayKind = copy(data.metadata.profiles.compact)
alternateUnderlayKind.widgets.torque_bar.kind = "max_speed"
assert.deepEqual(clampPlacement(alternateUnderlayKind, "torque_bar", 174, 179), { x: 174, y: 179 })

for (const mutate of [
  (value) => { value.document.layouts.large.fake = { x: 0, y: 0, enabled: true } },
  (value) => { value.document.layouts.compact.max_speed.y = 79 },
  (value) => { value.document.palette.text = "red" },
  (value) => { value.document.layouts.large.current_speed.x = Infinity },
  (value) => { value.document.layouts.compact.steering_wheel.size = 71 },
  (value) => { value.document.layouts.compact.speed_limit_actions.size = 70 },
  (value) => { delete value.document.layouts.compact },
  (value) => { value.metadata.profiles.compact.bounds.width = 9999 },
  (value) => { value.metadata.paletteFields[1].id = "text" },
  (value) => { value.editable = "true" },
]) {
  const invalid = copy(data); mutate(invalid); assert.equal(validSnapshot(invalid), false)
}
assert.equal(validDocument({ ...data.document, arbitrary: 1 }, data.metadata), false)
assert.equal(clampPlacement(data.metadata.profiles.compact, "max_speed", 999, 999), null)
assert.deepEqual(clampPlacement(data.metadata.profiles.compact, "max_speed", 999, 18), { x: 314, y: 18 })
assert.equal(clampPlacement(data.metadata.profiles.compact, "max_speed", 999, 19), null)
const movedActions = copy(data.document.layouts.compact)
movedActions.speed_limit_actions.x = 100
assert.deepEqual(clampPlacement(data.metadata.profiles.compact, "speed_limit_actions", 100, 180, data.document.layouts.compact), { x: 100, y: 180 })
assert.equal(clampPlacement(data.metadata.profiles.compact, "speed_limit_actions", 10, 180, data.document.layouts.compact), null)
assert.equal(clampPlacement(data.metadata.profiles.compact, "max_speed", 100, 19, movedActions), null)
assert.deepEqual(clampPlacement(data.metadata.profiles.compact, "speed_limit", 330, 20, movedActions), { x: 330, y: 20 })
assert.equal(validDocument({ ...data.document, layouts: { ...data.document.layouts, compact: movedActions } }, data.metadata), true)
const resizedWheel = copy(data.document)
resizedWheel.layouts.compact.steering_wheel.size = 70
resizedWheel.layouts.compact.steering_wheel.y = 160
assert.equal(validDocument(resizedWheel, data.metadata), true)
assert.equal(clampPlacement(data.metadata.profiles.compact, "steering_wheel", 150, 170, resizedWheel.layouts.compact), null)
assert.deepEqual(clampPlacement(data.metadata.profiles.compact, "steering_wheel", 21, 176, resizedWheel.layouts.compact), { x: 21, y: 170 })
assert.deepEqual(clampPlacement(data.metadata.profiles.large, "current_speed", -999, -99), { x: -120, y: 30 }, "the sidebar-hidden screen is reachable")
assert.deepEqual(clampPlacement(data.metadata.profiles.large, "cruise_limits", 9999, 30), { x: 2130 - 480, y: 30 })
assert.deepEqual(clampPlacement(data.metadata.profiles.large, "torque_bar", -999, 759), { x: -120, y: 759 }, "the torque bar reaches the sidebar-hidden screen")
assert.equal(clampPlacement(data.metadata.profiles.large, "fake", 30, 30), null)
assert.equal(clampPlacement(data.metadata.profiles.large, "current_speed", NaN, 30), null)
assert.deepEqual(previewPoint({ clientX: 316, clientY: 200 }, { left: 100, top: 92, width: 432, height: 216 }, data.metadata.profiles.large), { x: 1080, y: 540 })

function editor() {
  const emitted = [], calls = [], backdropCalls = [], captures = new Set()
  const target = { setPointerCapture(id) { captures.add(id) }, hasPointerCapture(id) { return captures.has(id) }, releasePointerCapture(id) { captures.delete(id) } }
  const vm = { ...OnroadLayoutPage.setup({ mode: "local", unauthorized() {} }), $emit: (...args) => emitted.push(args),
    $refs: { preview: { ...target, getBoundingClientRect: () => ({ left: 100, top: 100, width: 1080, height: 540 }) } } }
  for (const [key, getter] of Object.entries(OnroadLayoutPage.computed)) Object.defineProperty(vm, key, { get: () => getter.call(vm) })
  for (const [key, method] of Object.entries(OnroadLayoutPage.methods)) vm[key] = method.bind(vm)
  // This editor fixture exercises interaction; native request/race behavior has its own actual feed tests.
  vm.artFeed.start = () => { vm.artFeed.active = true }
  vm.artFeed.update = () => {}
  vm.artFeed.stop = () => { vm.artFeed.active = false }
  vm.artFeed.pause = () => { vm.artFeed.active = false }
  vm.backdropFeed.start = () => { vm.backdropFeed.active = true; backdropCalls.push("start") }
  vm.backdropFeed.update = (...args) => backdropCalls.push(["update", ...args])
  vm.backdropFeed.stop = () => { vm.backdropFeed.active = false; backdropCalls.push("stop") }
  vm.state.data = snapshot(); vm.state.data.supportedProfiles = ['large', 'compact']
  // These geometry fixtures represent a previously saved On choice.
  vm.state.data.document.display = vm.state.data.defaults.display = { DeveloperSidebar: "On" }
  vm.state.draft = copy(vm.state.data.document); vm.state.status = "ready"; vm.state.selected = "current_speed"
  const publish = vm.feed.publish
  vm.feed = { load: () => calls.push("load"), save: (document) => calls.push(copy(document)) }
  const event = (x, y, pointerId = 1) => ({ clientX: x, clientY: y, pointerId, button: 0, currentTarget: { setPointerCapture() { throw new Error("Capture must belong to the stable preview") } }, preventDefault() {} })
  return { vm, event, emitted, calls, backdropCalls, captures, publish }
}

// Snapping changes pointer placement only; a drag remains one history entry.
const snappedDrag = editor(), snapVm = snappedDrag.vm
const largePoint = (x, y, options = {}) => ({ ...snappedDrag.event(100 + x / 2, 100 + y / 2), pointerType: "mouse", ...options })
snapVm.startDrag("current_speed", largePoint(640, 30))
snapVm.moveDrag(largePoint(34, 430))
assert.equal(snapVm.layout.current_speed.x, 30)
assert.ok(snapVm.state.snapGuides.some(guide => guide.axis === "x" && guide.kind === "bounds"))
assert.equal(snapVm.snapSummary, "At the edge")
const cachedTargets = snapVm.state.drag.targets
snapVm.moveDrag(largePoint(35, 430))
assert.equal(snapVm.state.drag.targets, cachedTargets)
assert.equal(snapVm.snapInBar, false, "a mouse drag keeps the alignment chip on the preview")
snapVm.endDrag(largePoint(34, 430))
snapVm.startDrag("current_speed", largePoint(34, 430, { pointerType: "touch" }))
snapVm.moveDrag(largePoint(34, 300, { pointerType: "touch" }))  // past the finger slop
snapVm.moveDrag(largePoint(35, 430, { pointerType: "touch" }))
assert.equal(snapVm.snapInBar, true, "a finger drag shows the alignment in the save bar instead")
snapVm.endDrag(largePoint(35, 430, { pointerType: "touch" }))
assert.equal(snapVm.snapInBar, false)
assert.deepEqual(snapVm.state.snapGuides, [])
assert.equal(snapVm.state.history.undo.length, 1)
snapVm.undo()
assert.deepEqual(snapVm.layout.current_speed, snapshot().document.layouts.large.current_speed)
for (const bypass of ["alt", "disabled"]) {
  const free = editor().vm
  if (bypass === "disabled") free.state.snapEnabled = false
  free.startDrag("current_speed", largePoint(640, 30))
  free.endDrag(largePoint(34, 430, { altKey: bypass === "alt" }))
  assert.equal(free.layout.current_speed.x, 34)
  assert.deepEqual(free.state.snapGuides, [])
}
snapVm.startDrag("current_speed", largePoint(640, 30))
snapVm.moveDrag(largePoint(34, 430))
assert.ok(snapVm.state.snapGuides.length)
snapVm.cancelDrag()
assert.deepEqual(snapVm.layout.current_speed, snapshot().document.layouts.large.current_speed)
assert.deepEqual(snapVm.state.snapGuides, [])
snapVm.changePosition("current_speed", 34, 430)
snapVm.onKey("current_speed", { key: "ArrowLeft", preventDefault() {} })
assert.equal(snapVm.layout.current_speed.x, 33)
snapVm.onKey("current_speed", { key: "ArrowRight", shiftKey: true, preventDefault() {} })
assert.equal(snapVm.layout.current_speed.x, 43)
assert.deepEqual(snapVm.state.snapGuides, [])

for (const pointerType of ["mouse", "touch"]) {
  const phone = editor().vm
  phone.$refs.preview.getBoundingClientRect = () => ({ left: 0, top: 0, width: 360, height: 180 })
  for (const id of Object.keys(phone.layout)) if (id !== "current_speed") phone.state.hiddenPreviewWidgets.large[id] = true
  const phonePoint = (x, y) => ({ ...snappedDrag.event(x / 6, y / 6), pointerType })
  phone.startDrag("current_speed", phonePoint(640, 30))
  phone.endDrag(phonePoint(75, 430))
  assert.equal(phone.layout.current_speed.x, pointerType === "touch" ? 30 : 75)
}
const targetVisibility = editor().vm
targetVisibility.profile.widgets.pip = { ...widget("Side camera", "pip_camera", 60, 60, 400, 300), layer: "underlay" }
targetVisibility.layout.pip = { x: 400, y: 300, enabled: true }
targetVisibility.startDrag("current_speed", largePoint(640, 30))
targetVisibility.moveDrag(largePoint(464, 430))
assert.equal(targetVisibility.layout.current_speed.x, 460)
assert.ok(targetVisibility.state.snapGuides.some(guide => guide.label === "Side camera"))
targetVisibility.cancelDrag()
targetVisibility.togglePreviewVisibility("pip")
targetVisibility.startDrag("current_speed", largePoint(640, 30))
targetVisibility.moveDrag(largePoint(464, 430))
assert.equal(targetVisibility.layout.current_speed.x, 464)
targetVisibility.cancelDrag()
targetVisibility.startDrag("current_speed", largePoint(640, 30))
targetVisibility.moveDrag(largePoint(443, 430))
assert.equal(targetVisibility.layout.current_speed.x, 443, "torque arc footprint must not attract other widgets")
targetVisibility.cancelDrag()

const invalidSnap = editor().vm
invalidSnap.selectProfile("compact")
invalidSnap.$refs.preview.getBoundingClientRect = () => ({ left: 0, top: 0, width: 536, height: 240 })
invalidSnap.state.hiddenPreviewWidgets.compact.speed_limit_actions = true
const smallPoint = (x, y) => ({ ...snappedDrag.event(x, y), pointerType: "mouse" })
invalidSnap.startDrag("max_speed", smallPoint(0, 0))
invalidSnap.moveDrag(smallPoint(100, 18)) // Speed limit's top attracts to y=20, overlapping protected actions.
assert.equal(clampPlacement(invalidSnap.profile, "max_speed", 100, 20, invalidSnap.layout), null)
assert.deepEqual(invalidSnap.layout.max_speed, { x: 100, y: 18, enabled: true })
assert.deepEqual(invalidSnap.state.snapGuides, [])
assert.equal(invalidSnap.state.placementError, "")
invalidSnap.moveDrag(smallPoint(100, 100))
assert.deepEqual(invalidSnap.layout.max_speed, { x: 100, y: 18, enabled: true })
assert.ok(invalidSnap.state.placementError)
invalidSnap.cancelDrag()

const traySnap = editor().vm
traySnap.layout.current_speed.enabled = false
traySnap.startDrag("current_speed", largePoint(2400, 0), true)
traySnap.moveDrag(largePoint(324, 550)) // Widget center follows the tray pointer.
assert.equal(traySnap.layout.current_speed.x, 30)
assert.equal(traySnap.layout.current_speed.enabled, true)
assert.ok(traySnap.state.snapGuides.length)
traySnap.moveDrag(largePoint(2400, 550))
assert.equal(traySnap.layout.current_speed.enabled, false)
assert.deepEqual(traySnap.state.snapGuides, [])
traySnap.cancelDrag()
assert.equal(traySnap.layout.current_speed.enabled, false)

const clampedSnap = editor().vm
Object.assign(clampedSnap.profile.widgets.driver_monitor, { expandable: false, bounds: { x: 30, y: 30, width: 300, height: 1020 } })
clampedSnap.layout.current_speed.x = 184
clampedSnap.startDrag("driver_monitor", largePoint(88, 808))
clampedSnap.moveDrag(largePoint(187, 400)) // Current speed at 184 is beyond the mover's own bounds.
assert.equal(clampedSnap.layout.driver_monitor.x, 138)
assert.deepEqual(clampedSnap.state.snapGuides.filter(guide => guide.axis === "x"),
  [{ axis: "x", value: 330, kind: "bounds", label: "the edge" }], "a clamped widget reports the edge it rests on, not the target it missed")
clampedSnap.moveDrag(largePoint(400, 400)) // Pushed far past the edge, it still reports resting on it.
assert.equal(clampedSnap.layout.driver_monitor.x, 138)
assert.ok(clampedSnap.state.snapGuides.some(guide => guide.axis === "x" && guide.value === 330 && guide.kind === "bounds"))
clampedSnap.cancelDrag()

const resizeSnap = editor().vm
resizeSnap.profile.widgets.map = { ...widget("Map", "nav_map", 200, 200, 300, 300),
  box: { minWidth: 100, maxWidth: 1000, minHeight: 100, maxHeight: 1000 } }
resizeSnap.layout.map = { x: 300, y: 300, width: 200, height: 200, enabled: true }
const resizePoint = (x, y, options = {}) => ({ ...largePoint(x, y, options), stopPropagation() {} })
resizeSnap.startResize("map", resizePoint(500, 500))
resizeSnap.moveDrag(resizePoint(564, 671))
assert.deepEqual([resizeSnap.layout.map.width, resizeSnap.layout.map.height], [268, 375])
assert.deepEqual([resizeSnap.layout.map.x, resizeSnap.layout.map.y], [300, 300])
assert.equal(resizeSnap.state.snapGuides.length, 2)
resizeSnap.endDrag(resizePoint(564, 671))
assert.deepEqual(resizeSnap.state.snapGuides, [])
assert.equal(resizeSnap.state.history.undo.length, 1)
resizeSnap.undo()
resizeSnap.startResize("map", resizePoint(500, 500))
resizeSnap.moveDrag(resizePoint(564, 671, { altKey: true }))
assert.deepEqual([resizeSnap.layout.map.width, resizeSnap.layout.map.height], [264, 371])
assert.deepEqual(resizeSnap.state.snapGuides, [])
resizeSnap.cancelDrag()
resizeSnap.profile.widgets.map.box.maxWidth = 250
resizeSnap.startResize("map", resizePoint(500, 500))
resizeSnap.moveDrag(resizePoint(564, 671))
assert.equal(resizeSnap.layout.map.width, 250)
assert.deepEqual(resizeSnap.state.snapGuides, [])
resizeSnap.cancelDrag()

const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage"), preferences = new Map()
try {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: key => preferences.get(key) ?? null, setItem: (key, value) => preferences.set(key, value)
  } })
  const preference = editor().vm
  assert.equal(preference.state.snapEnabled, true)
  preference.toggleSnap()
  assert.equal(preferences.get("gx-layout-snap-v1"), "0")
  assert.equal(editor().vm.state.snapEnabled, false)
  assert.equal(OnroadLayoutPage.setup({ projection: true }).state.snapEnabled, false)
  preference.toggleSnap()
  assert.equal(preferences.get("gx-layout-snap-v1"), "1")
  assert.equal(editor().vm.state.snapEnabled, true)
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("Storage blocked") } })
  const blocked = editor().vm
  assert.equal(blocked.state.snapEnabled, true)
  blocked.toggleSnap()
  assert.equal(blocked.state.snapEnabled, false)
} finally {
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage)
  else delete globalThis.localStorage
}
for (const [guides, expected] of [
  [[{ axis: "x", kind: "center" }, { axis: "y", kind: "center" }], "Centered on the screen"],
  [[{ axis: "x", kind: "center" }], "Centered across"],
  [[{ axis: "y", kind: "center" }], "Centered top to bottom"],
  [[{ label: "Home" }, { label: "Home" }, { label: "Work" }], "Lined up with Home and Work"],
  [[{ label: "the edge" }, { label: "Home" }], "At the edge, lined up with Home"],
  [[{ axis: "x", kind: "center", label: "the center" }, { axis: "y", kind: "bounds", label: "the edge" }], "Centered across, at the edge"],
  [[{ axis: "y", kind: "center", label: "the center" }, { axis: "x", kind: "widget", label: "Home" }, { axis: "y", kind: "widget", label: "Work" }],
    "Centered top to bottom, lined up with Home"],
]) {
  snapVm.state.snapGuides = guides
  assert.equal(snapVm.snapSummary, expected)
}
for (const clear of [() => snapVm.selectProfile("compact")]) {
  snapVm.state.snapGuides = [{ axis: "x", value: 30, kind: "bounds", label: "the edge" }]
  clear()
  assert.deepEqual(snapVm.state.snapGuides, [])
}
console.log("Layout snapping integration: drag, touch, targets, fallback, resize, history, preference and keyboard passed")

// Layer visibility is local to the arrangement preview, including required widgets.
for (const projection of [false, true]) {
  const visibility = editor().vm
  visibility.projection = projection
  const original = copy(visibility.state.draft)
  const order = visibility.layerWidgets.map(widget => widget.id)
  visibility.profile.widgets.current_speed.required = true
  visibility.togglePreviewVisibility("current_speed")
  assert.equal(visibility.previewHidden("current_speed"), true)
  assert.equal(visibility.renderWidgets.some(widget => widget.id === "current_speed"), false)
  assert.deepEqual(visibility.layerWidgets.map(widget => widget.id), order)
  assert.deepEqual(visibility.state.draft, original)
  assert.equal(visibility.dirty, false)
  assert.equal(visibility.canUndo, false)
  visibility.state.profile = "compact"
  assert.equal(visibility.previewHidden("current_speed"), false)
  visibility.state.profile = "large"
  visibility.togglePreviewVisibility("current_speed")
  assert.equal(visibility.renderWidgets.some(widget => widget.id === "current_speed"), true)
  assert.deepEqual(visibility.state.draft, original)
}

const dmEditor = editor().vm
dmEditor.changePosition("driver_monitor", 700, 600)
dmEditor.selectProfile("compact")
dmEditor.changePosition("driver_monitor", 90, 70)
assert.deepEqual(dmEditor.state.draft.layouts.large.driver_monitor, { x: 700, y: 600, enabled: true })
assert.deepEqual(dmEditor.layout.driver_monitor, { x: 90, y: 70, enabled: true })
dmEditor.changePosition("driver_monitor", 200, 190)
assert.deepEqual(dmEditor.layout.driver_monitor, { x: 90, y: 70, enabled: true })
dmEditor.remove("driver_monitor")
assert.equal(dmEditor.layout.driver_monitor.enabled, false)
dmEditor.add("driver_monitor")
assert.equal(dmEditor.layout.driver_monitor.enabled, true)
dmEditor.resetLayout()
assert.deepEqual(dmEditor.layout.driver_monitor, { x: 16, y: 10, enabled: true })
assert.equal(dmEditor.state.draft.layouts.large.driver_monitor.x, 700)

const actionEditor = editor().vm
actionEditor.changePosition("speed_limit_actions", 388, 600)
assert.deepEqual(actionEditor.layout.speed_limit_actions, { x: 388, y: 600, enabled: true })
actionEditor.selectProfile("compact")
actionEditor.changePosition("speed_limit_actions", 100, 180)
assert.deepEqual(actionEditor.layout.speed_limit_actions, { x: 100, y: 180, enabled: true })
actionEditor.changePosition("speed_limit_actions", 10, 180)
assert.equal(actionEditor.layout.speed_limit_actions.x, 100)
assert.equal(validDocument(actionEditor.state.draft, actionEditor.state.data.metadata), true)

const wheelEditor = editor().vm
wheelEditor.state.selected = "steering_wheel"
wheelEditor.resizeSelected({ target: { value: "240" } })
assert.equal(wheelEditor.layout.steering_wheel.size, 240)
assert.equal(wheelEditor.renderWidgets.find((widget) => widget.id === "steering_wheel").width, 240)
wheelEditor.undo()
assert.equal(wheelEditor.layout.steering_wheel.size, 192)
wheelEditor.redo()
assert.equal(wheelEditor.layout.steering_wheel.size, 240)
wheelEditor.selectProfile("compact")
wheelEditor.state.selected = "steering_wheel"
wheelEditor.resizeSelected({ target: { value: "70" } })
assert.deepEqual(wheelEditor.layout.steering_wheel, { x: 21, y: 170, enabled: true, size: 70 })
assert.equal(wheelEditor.state.draft.layouts.large.steering_wheel.size, 240)
wheelEditor.resetLayout()
assert.equal(wheelEditor.layout.steering_wheel.size, 50)
assert.equal(wheelEditor.state.draft.layouts.large.steering_wheel.size, 240)
const dragWheel = editor()
dragWheel.vm.state.selected = "steering_wheel"
dragWheel.vm.resizeSelected({ target: { value: "240" } })
dragWheel.vm.startDrag("steering_wheel", dragWheel.event(954, 197.5))
dragWheel.vm.moveDrag(dragWheel.event(964, 197.5))
dragWheel.vm.endDrag(dragWheel.event(964, 197.5))
assert.deepEqual(dragWheel.vm.layout.steering_wheel, { x: 1590, y: 75, enabled: true, size: 240 })
assert.equal(dragWheel.captures.size, 0)


const torqueEditor = editor().vm
torqueEditor.changePosition("torque_bar", 500, 650)
torqueEditor.selectProfile("compact")
torqueEditor.changePosition("torque_bar", 174, 179)
assert.deepEqual(torqueEditor.renderWidgets.map(widget => widget.id), torqueEditor.layerWidgets.map(widget => widget.id).reverse())
assert.equal(torqueEditor.state.draft.layouts.large.torque_bar.x, 500)
assert.equal(torqueEditor.layout.torque_bar.y, 179)
torqueEditor.remove("torque_bar")
assert.equal(torqueEditor.layout.torque_bar.enabled, false)
torqueEditor.add("torque_bar")
assert.equal(torqueEditor.layout.torque_bar.enabled, true)
const { vm, event, emitted, calls, backdropCalls, captures } = editor()
// The comma's real screen loads behind the arrangement with the editor.
assert.deepEqual(backdropCalls.map((call) => Array.isArray(call) ? call[0] : call).slice(-2), ["start", "update"])
assert.deepEqual(backdropCalls.at(-1).slice(2, 5), ["large", "general", false])
assert.equal(vm.dirty, false)
vm.changePosition("current_speed", 700, 200)
vm.selectProfile("compact")
vm.changePosition("max_speed", 200, 18)
vm.state.selected = "driver_monitor"
vm.color("cardFill", "#112233aa")
vm.resetLayout()
assert.equal(vm.layout.max_speed.x, 0)
assert.equal(vm.state.draft.layouts.large.current_speed.x, 700)
assert.equal(vm.selectedColors.cardFill, "#112233AA")
vm.changePosition("max_speed", 200, 18)
vm.resetColors()
assert.equal(vm.selectedColors.cardFill, "#00000000")
assert.equal(vm.layout.max_speed.x, 200)
vm.changePosition("max_speed", 200, 65)
assert.equal(vm.layout.max_speed.y, 18)
assert.match(vm.state.placementError, /last valid position/)
const compactPoint = (x, y) => event(100 + x * 1080 / 536, 100 + y * 540 / 240)
vm.startDrag("speed_limit", compactPoint(350, 40))
vm.moveDrag(compactPoint(350, 100))
assert.equal(vm.layout.speed_limit.y, 80) // Intentional sign/action overlap survives dragging.
vm.endDrag(compactPoint(350, 100))
assert.equal(vm.layout.speed_limit.y, 80)
assert.equal(vm.state.drag, null)
vm.selectProfile("large")
assert.equal(vm.layout.current_speed.x, 700)
vm.onKey("current_speed", { key: "ArrowRight", shiftKey: true, preventDefault() {} })
assert.equal(vm.layout.current_speed.x, 710)
vm.onKey("current_speed", { key: "Delete", preventDefault() {} })
assert.equal(vm.state.removeConfirm, "current_speed")
assert.equal(vm.layout.current_speed.enabled, true)
vm.confirmRemove()
assert.equal(vm.layout.current_speed.enabled, false)
assert.equal(vm.inactiveWidgets.length, 1)
vm.startDrag("current_speed", event(1300, 100), true)
vm.moveDrag(event(500, 300))
assert.equal(vm.layout.current_speed.enabled, true)
assert.equal(vm.inactiveWidgets.length, 1) // Keep the captured tray element mounted during the drag.
assert.equal(vm.state.drag.fromTray, true)
assert.deepEqual({ ...vm.layout.current_speed }, { x: 510, y: 250, enabled: true })
assert.equal(captures.has(1), true)
vm.endDrag(event(500, 300))
assert.equal(captures.size, 0)
assert.equal(vm.inactiveWidgets.length, 0)
vm.startDrag("current_speed", event(400, 250))
vm.moveDrag(event(2000, 2000))
assert.deepEqual({ ...vm.layout.current_speed }, { x: 1250, y: 750, enabled: true })
vm.cancelDrag()
assert.deepEqual({ ...vm.layout.current_speed }, { x: 510, y: 250, enabled: true })
vm.remove("current_speed")
vm.startDrag("current_speed", event(1300, 100), true)
vm.moveDrag(event(500, 300, 2)) // A second pointer cannot change this drag.
assert.equal(vm.layout.current_speed.enabled, false)
vm.endDrag(event(1300, 100))
assert.equal(vm.layout.current_speed.enabled, false)
vm.add("current_speed")
assert.deepEqual({ ...vm.layout.current_speed }, { x: 510, y: 250, enabled: true })
vm.color("text", "invalid")
assert.match(vm.state.colorError, /eight hex digits/)
assert.equal(vm.selectedColors.text, "#FFFFFFFF")
vm.colorRgb("text", { target: { value: "#abcdef" } })
vm.colorAlpha("text", { target: { value: "128" } })
assert.equal(vm.selectedColors.text, "#ABCDEF80")
// Driving scenes do not leave a CEM editing placeholder behind.
assert.doesNotMatch(OnroadLayoutPage.template, /data-preview-source="cem-hint"/)
// The Small screen shows the tap-areas chip under the layout instead of over it.
assert.match(OnroadLayoutPage.template, /state\.profile !== 'compact'" class="gx-layout__stage-chip" aria-hidden="true"><i class="bi bi-grid-3x3-gap"><\/i>Tap areas/)
assert.match(OnroadLayoutPage.template, /state\.profile === 'compact'" class="gx-layout__stage-chip gx-layout__stage-chip--below"/)
// One scene dropdown, grouped, with every scene exactly once.
assert.deepEqual(vm.sceneGroups.map(group => group.label), ["Editing", "Driving", "Conditional experimental", "Speed limit"])
assert.deepEqual(vm.sceneGroups.flatMap(group => group.scenes.map(scene => scene.id)).sort(), vm.scenes.map(scene => scene.id).sort())
// The Small screen only offers scenes that look different there; a hidden pick falls back to General edit.
vm.state.scene = "slc_display"
vm.selectProfile("compact")
assert.equal(vm.state.scene, "general")
const smallScenes = vm.sceneGroups.flatMap(group => group.scenes.map(scene => scene.id))
for (const id of ["slc_pedal", "slc_display"]) assert.ok(!smallScenes.includes(id), id)
assert.ok(smallScenes.includes('slc_retained'))  // The override X is visible on the Small screen too.
assert.ok(smallScenes.includes("slc_unavailable") && smallScenes.includes("slc_disengaged"))
vm.selectProfile("large")
assert.equal(vm.sceneGroups.flatMap(group => group.scenes).length, vm.scenes.length)
assert.match(OnroadLayoutPage.template, /<GalaxySelect class="gx-field gx-field--full" aria-label="Preview scene"/)
// The widget open in Edit glows; the sources panel is a layout setting, not an on-screen toggle.
assert.match(OnroadLayoutPage.template, /'is-editing': state\.showEditingAids && state\.inspectorPanel === 'edit' && state\.selected === widget\.id/)
// Sources sit inside the cruise card (or a card right under it), so its box grows and actions right below follow.
vm.state.data.metadata.profiles.large.widgets.cruise_limits.sourcesExtent = { inline: 76, card: 84 }
vm.state.selected = "cruise_limits"
const sourcesCard = vm.layout.cruise_limits, sourcesActions = vm.layout.speed_limit_actions
const actionsBefore = { ...sourcesActions }
const sourceDx = vm.profile.widgets.speed_limit_actions.default.x - vm.profile.widgets.cruise_limits.default.x
const sourceGap = vm.profile.widgets.speed_limit_actions.default.y - vm.profile.widgets.cruise_limits.default.y - 522
Object.assign(sourcesActions, { x: sourcesCard.x + sourceDx, y: sourcesCard.y + 522 + sourceGap })
vm.setSpeedSources("speedSources", true)
assert.equal(vm.state.draft.speedSources, true)
assert.equal(vm.profile.widgets.cruise_limits.height, 522 + 76)
assert.equal(sourcesActions.y, sourcesCard.y + 522 + 76 + sourceGap)
vm.setSpeedSources("speedSourcesCard", true)
assert.equal(vm.profile.widgets.cruise_limits.height, 522 + 84)
assert.equal(sourcesActions.y, sourcesCard.y + 522 + 84 + sourceGap)
vm.undo(); vm.undo()
assert.notEqual(vm.state.draft.speedSources, true)
assert.equal(vm.profile.widgets.cruise_limits.height, 522)
assert.equal(vm.layout.speed_limit_actions.y, vm.layout.cruise_limits.y + 522 + sourceGap)
Object.assign(vm.layout.speed_limit_actions, actionsBefore)
vm.state.selected = "current_speed"
vm.setSpeedSources("speedSources", true)
assert.notEqual(vm.state.draft.speedSources, true) // Only from the cruise widget's own panel.
vm.state.selected = "cruise_limits"
assert.doesNotMatch(OnroadLayoutPage.template, /speedAlign|Text alignment/)
assert.equal(OnroadLayoutPage.methods.setSpeedAlign, undefined)
vm.setSpeedSources("palette", true)
assert.equal(vm.state.draft.palette === true, false) // Only the two sources settings.
// Moving or removing widgets keeps the same backdrop; scene and size changes replace it.
const backdropCount = backdropCalls.length
vm.changePosition("current_speed", 720, 220)
vm.changePosition("current_speed", 510, 250)
vm.remove("current_speed")
vm.add("current_speed")
assert.equal(backdropCalls.length, backdropCount)
vm.selectProfile("compact")
assert.equal(backdropCalls.at(-1)[2], "compact")
vm.state.scene = "braking"
assert.equal(backdropCalls.at(-1)[3], "braking")
vm.selectProfile("large")
vm.state.scene = "engaged"
vm.color("text", "#ABCDEF81")
assert.equal(backdropCalls.length, backdropCount + 5) // Color edits change the real screen too.
vm.color("text", "#ABCDEF80")
const draggingCount = backdropCalls.length
vm.startDrag("current_speed", event(500, 200))
vm.moveDrag(event(700, 250))
vm.cancelDrag()
assert.equal(backdropCalls.length, draggingCount)
vm.state.data.editable = false
assert.equal(backdropCalls.at(-1), "stop")
vm.state.data.editable = true
vm.requestLeave("reload")
assert.equal(vm.state.discard, "reload")
assert.equal(calls.length, 0)
assert.equal(vm.editable, false)
vm.state.discard = null
vm.save()
assert.equal(calls.length, 1)
assert.equal(calls[0].layouts.compact.max_speed.x, 200)
vm.requestLeave("back")
assert.equal(emitted.length, 0)
vm.leave("back")
assert.deepEqual(emitted, [["close"]])
vm.state.data.editable = false
const parkedDraft = copy(vm.state.draft)
vm.changePosition("current_speed", 99, 99); vm.remove(); vm.resetLayout(); vm.resetColors(); vm.color("text", "#00000000"); vm.save()
assert.deepEqual(copy(vm.state.draft), parkedDraft)
assert.equal(calls.length, 1)

const historyEditor = editor()
const history = historyEditor.vm
assert.equal(history.canUndo, false)
assert.equal(history.stockChanged, false)
history.resetToStock()
assert.equal(history.state.history.undo.length, 0)
history.remove("current_speed")
history.changePosition("current_speed", 700, 200) // Disabled widgets keep editable positions.
assert.equal(history.state.history.undo.length, 2)
history.undo()
assert.deepEqual(copy(history.layout.current_speed), { x: 640, y: 30, enabled: false })
history.undo()
assert.deepEqual(copy(history.layout.current_speed), { x: 640, y: 30, enabled: true })
history.redo()
history.redo()
assert.deepEqual(copy(history.layout.current_speed), { x: 700, y: 200, enabled: false })
history.selectProfile("compact")
history.changePosition("max_speed", 200, 18)
history.undo()
assert.equal(history.layout.max_speed.x, 0)
assert.equal(history.state.draft.layouts.large.current_speed.x, 700)
history.redo()
assert.equal(history.layout.max_speed.x, 200)

const dragEditor = editor()
const dragged = dragEditor.vm
const beforeDrag = copy(dragged.state.draft)
dragged.startDrag("current_speed", dragEditor.event(500, 150))
dragged.moveDrag(dragEditor.event(600, 200))
dragged.moveDrag(dragEditor.event(700, 250))
assert.equal(dragged.state.history.undo.length, 0)
dragged.undo() // History cannot replace a draft during an active gesture.
assert.equal(dragged.state.drag.id, "current_speed")
dragged.endDrag(dragEditor.event(700, 250))
assert.equal(dragged.state.history.undo.length, 1)
const afterDrag = copy(dragged.state.draft)
dragged.undo()
assert.deepEqual(copy(dragged.state.draft), beforeDrag)
dragged.redo()
assert.deepEqual(copy(dragged.state.draft), afterDrag)
dragged.startDrag("current_speed", dragEditor.event(700, 250))
dragged.moveDrag(dragEditor.event(800, 300))
dragged.cancelDrag()
assert.deepEqual(copy(dragged.state.draft), afterDrag)
assert.equal(dragged.state.history.undo.length, 1)
assert.equal(dragged.state.history.redo.length, 0)
dragged.remove("current_speed")
const beforeTray = copy(dragged.state.draft)
const historySize = dragged.state.history.undo.length
dragged.startDrag("current_speed", dragEditor.event(1300, 100), true)
dragged.moveDrag(dragEditor.event(500, 300))
dragged.endDrag(dragEditor.event(500, 300))
assert.equal(dragged.state.history.undo.length, historySize + 1)
dragged.undo()
assert.deepEqual(copy(dragged.state.draft), beforeTray)

const colorEditor = editor().vm
colorEditor.colorAlpha("text", { target: { value: "200" } })
colorEditor.colorAlpha("text", { target: { value: "180" } })
colorEditor.colorAlpha("text", { target: { value: "160" } })
assert.equal(colorEditor.state.history.undo.length, 1)
colorEditor.undo()
assert.equal(colorEditor.selectedColors.text, "#FFFFFFFF")
colorEditor.redo()
assert.equal(colorEditor.selectedColors.text, "#FFFFFFA0")
colorEditor.finishColorEdit()
colorEditor.colorRgb("text", { target: { value: "#abcdef" } })
colorEditor.colorRgb("text", { target: { value: "#123456" } })
assert.equal(colorEditor.state.history.undo.length, 2)
colorEditor.undo()
assert.equal(colorEditor.selectedColors.text, "#FFFFFFA0")
colorEditor.colorText("text", { target: { value: "#01020304" } })
assert.equal(colorEditor.canRedo, false) // A fresh edit replaces the redo branch.

const stockFixture = editor()
const stockEditor = stockFixture.vm
stockEditor.changePosition("current_speed", 700, 200)
stockEditor.remove("driver_monitor")
stockEditor.changePosition("driver_monitor", 100, 700)
stockEditor.selectProfile("compact")
stockEditor.changePosition("max_speed", 200, 18)
stockEditor.state.selected = "driver_monitor"
stockEditor.color("cardFill", "#11223344")
const beforeStock = copy(stockEditor.state.draft)
const beforeStockHistory = stockEditor.state.history.undo.length
stockEditor.resetToStock()
assert.deepEqual(copy(stockEditor.state.draft), stockEditor.state.data.defaults)
assert.equal(stockEditor.state.history.undo.length, beforeStockHistory + 1)
assert.match(stockEditor.state.notice, /Save to apply/)
stockEditor.undo()
assert.deepEqual(copy(stockEditor.state.draft), beforeStock)
stockEditor.redo()
assert.deepEqual(copy(stockEditor.state.draft), stockEditor.state.data.defaults)
stockFixture.publish({ status: "ready", data: { ...snapshot(), document: copy(stockEditor.state.draft) },
  draft: copy(stockEditor.state.draft), notice: "Colors and both layouts saved." })
assert.equal(stockEditor.canUndo, false)
assert.equal(stockEditor.canRedo, false)
stockEditor.changePosition("max_speed", 100, 18)
assert.equal(stockEditor.canUndo, true)
stockFixture.publish({ status: "ready", error: "Reload failed." })
assert.equal(stockEditor.canUndo, true) // Failed reads keep the current draft and history.
stockFixture.publish({ status: "ready", data: snapshot(), draft: copy(snapshot().document) })
assert.equal(stockEditor.canUndo, false)
assert.equal(stockEditor.canRedo, false)
stockEditor.state.data.editable = false
stockEditor.changePosition("max_speed", 200, 18)
stockEditor.undo()
stockEditor.resetToStock()
assert.deepEqual(copy(stockEditor.state.draft), snapshot().document)
assert.match(OnroadLayoutPage.template, /Reset to stock StarPilot/)
assert.match(OnroadLayoutPage.template, /@click="undo"/)
assert.match(OnroadLayoutPage.template, /@click="redo"/)
assert.match(OnroadLayoutPage.template, /class="gx-layout__remove gx-icon-btn"/)
assert.match(OnroadLayoutPage.template, /Remove widget/)
assert.doesNotMatch(OnroadLayoutPage.template, />Remove from layout</)
assert.match(OnroadLayoutPage.template, /:clock24-hour="!!state\.draft\.clock24Hour"/)
assert.match(LayoutWidgetPreview.template, /clock24Hour \? '18:00' : '12:00 PM'/)

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }
function feedFixture() {
  const requests = [], updates = [], timers = new Map(); let nextTimer = 0, unauthorized = 0
  const feed = new OnroadLayoutFeed({ publish: (value) => updates.push(value), unauthorized: () => { unauthorized++ },
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })),
    later: (fn, ms) => { fn.delay = ms; timers.set(++nextTimer, fn); return nextTimer }, cancelTimer: (id) => timers.delete(id) })
  async function reply(index, body = snapshot(), status = 200) {
    requests[index].resolve({ ok: status === 200, status, json: async () => body }); await flush()
  }
  function fire(ms) {
    const entry = [...timers.entries()].find(([, fn]) => fn.delay === ms)
    if (!entry) return false
    timers.delete(entry[0]); entry[1](); return true
  }
  return { feed, requests, updates, timers, reply, fire, get unauthorized() { return unauthorized } }
}
const normal = feedFixture()
normal.feed.start()
assert.equal(normal.requests[0].url, "./api/ui/layout")
const legacyAlignment = snapshot()
legacyAlignment.document.speedAlign = "right"
await normal.reply(0, legacyAlignment)
const draft = copy(normal.feed.data.document); draft.layouts.compact.max_speed.x = 80
normal.feed.save(draft)
normal.feed.load(); normal.feed.save(draft)
assert.equal(normal.requests.length, 2) // No load or second save may race a dispatched save.
assert.deepEqual(JSON.parse(normal.requests[1].options.body), { revision: "saved-revision", document: draft })
assert.equal(normal.requests[1].options.credentials, "same-origin")
await normal.reply(1, { ...snapshot(), document: draft, revision: "new-revision" })
assert.equal(normal.updates.at(-1).draft.layouts.compact.max_speed.x, 80)
assert.equal(normal.updates.at(-1).draft.speedAlign, "right") // Unrelated edits preserve legacy layout fields.
assert.equal(normal.feed.data.revision, "new-revision")

for (const status of [409, 503, 400]) {
  const conflict = feedFixture(); conflict.feed.start(); await conflict.reply(0)
  conflict.feed.save(draft); await conflict.reply(1, {}, status)
  assert.equal(conflict.feed.needsReload, true)
  assert.equal(conflict.updates.at(-1).draft, undefined) // Failure never replaces the unsaved document.
  conflict.feed.save(draft); assert.equal(conflict.requests.length, 2)
  conflict.feed.load(); await conflict.reply(2)
  assert.equal(conflict.feed.needsReload, false)
}
const timeout = feedFixture(); timeout.feed.start(); await timeout.reply(0); timeout.feed.save(draft)
const expire = timeout.timers.get(timeout.feed.timer); expire()
assert.match(timeout.updates.at(-1).error, /result is unknown/)
assert.equal(timeout.requests[1].options.signal.aborted, true)
await timeout.reply(1, { ...snapshot(), document: draft })
assert.equal(timeout.feed.data.document.layouts.compact.max_speed.x, 0)
const late = feedFixture(); late.feed.start(); late.feed.stop(); await late.reply(0)
assert.equal(late.feed.data, null)
const invalid = feedFixture(); invalid.feed.start(); await invalid.reply(0, { ...snapshot(), editable: "yes" })
assert.equal(invalid.feed.data, null)
assert.equal(invalid.updates.at(-1).status, "unavailable")
const unauthorized = feedFixture(); unauthorized.feed.start(); await unauthorized.reply(0, {}, 401)
assert.equal(unauthorized.unauthorized, 1)

const settling = feedFixture()
settling.feed.start(); await settling.reply(0, { ...snapshot(), editable: false })
assert.equal(settling.updates.at(-1).data.editable, false)
assert.equal(settling.fire(5000), true)
assert.equal(settling.requests.length, 2)
await settling.reply(1, { ...snapshot(), editable: true })
assert.equal(settling.updates.at(-1).data.editable, true)
assert.equal([...settling.timers.values()].some((fn) => fn.delay === 5000), true)

const stoppedRetry = feedFixture()
stoppedRetry.feed.start(); await stoppedRetry.reply(0, { ...snapshot(), editable: false })
const pendingRetry = [...stoppedRetry.timers.values()].find((fn) => fn.delay === 5000)
stoppedRetry.feed.stop(); pendingRetry()
assert.equal(stoppedRetry.requests.length, 1)

const manualRetry = feedFixture()
manualRetry.feed.start(); await manualRetry.reply(0, { ...snapshot(), editable: false })
const cancelledRetry = [...manualRetry.timers.values()].find((fn) => fn.delay === 5000)
manualRetry.feed.load()
cancelledRetry()
assert.equal(manualRetry.requests.length, 2)
await manualRetry.reply(1, snapshot())
assert.equal(manualRetry.updates.at(-1).data.editable, true)

const boundedRetry = feedFixture()
boundedRetry.feed.start()
for (let index = 0; index < 4; index++) {
  await boundedRetry.reply(index, { ...snapshot(), editable: false })
  boundedRetry.fire(5000)
}
assert.equal(boundedRetry.requests.length, 5)
boundedRetry.feed.stop()
assert.equal([...boundedRetry.timers.values()].some((fn) => fn.delay === 5000), false)

const visual = SETTINGS_SECTIONS.find(({ id }) => id === "visual")
const rows = SettingsPage.computed.visibleRows.call({ initialPage: "hub", activeSection: visual,
  state: { data: { page: "hub" }, query: "" } })
assert.equal(rows.filter(({ row }) => row.page === "ui_layout").length, 1)
const navigated = [], host = { initialPage: "hub", state: {}, feed: { stop: () => navigated.push("stop"), load: () => assert.fail("Layout is not a generic settings endpoint"), start: (page) => navigated.push(page) } }
SettingsPage.methods.open.call(host, "ui_layout")
assert.equal(host.state.layoutOpen, true)
SettingsPage.methods.closeLayout.call(host)
assert.equal(host.state.section, "visual")
assert.deepEqual(navigated, ["stop", "hub"])
host.initialPage = "appearance"
SettingsPage.methods.open.call(host, "ui_layout")
SettingsPage.methods.closeLayout.call(host)
assert.deepEqual(navigated.slice(-2), ["stop", "appearance"])
const appSource = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
const editorSource = readFileSync(new URL("../web/js/onroad-layout.js", import.meta.url), "utf8")
const catalog = JSON.parse(readFileSync(new URL("../web/data/catalog.json", import.meta.url), "utf8"))
assert.ok(appSource.includes("['/theme_maker', '/theme_maker/starport', '/appearance'].includes(route.path)"))
assert.ok(appSource.includes(':key="route.path" :projection="route.path ==='))
assert.ok(appSource.includes('@target="go($event ==='))
assert.match(appSource, /goFromMenu\(path\)/)
assert.match(appSource, /requestLeave\(proceed, presentation\)/)
assert.match(appSource, /@click="goFromMenu\(item\.path\)"/)
assert.equal(OnroadLayoutPage.template, ONROAD_LAYOUT_TEMPLATE, "the page uses the extracted template")
assert.equal(OnroadLayoutPage.components.LayoutWidgetPreview, LayoutWidgetPreview, "the template preview uses the actual component")
assert.match(OnroadLayoutPage.template, /<LayoutWidgetPreview :widget="widget"/)
assert.doesNotMatch(OnroadLayoutPage.template, /<LayoutWidgetPreview v-if="!state\.preview\.url"/)
assert.match(OnroadLayoutPage.template, /<image v-if="nativeBackdropReady" :href="state\.backdrop\.url"/)
assert.doesNotMatch(editorSource, /devicePreviewOpen|gx-layout__device|selectPreview/)
assert.doesNotMatch(OnroadLayoutPage.template, /devicePreviewOpen|gx-layout__device|selectPreview/)
const loadedCatalog = await loadCatalog({ fetcher: async (url) => ({ ok: true,
  json: async () => url.endsWith("catalog.json") ? catalog : { schemaVersion: 1, monitor: "local" },
}) })
assert.ok(loadedCatalog.tools.some(tool => tool.path === "/theme_maker"))
assert.equal(catalog.tools.find((tool) => tool.path === "/theme_maker")?.availability, "local-only")
console.log("Colors and layout: stable editor, device backdrop, placement, drag/cancel, resets, save races, errors, auth and Visual navigation passed")

const railEditor = editor().vm
railEditor.selectProfile("compact")
railEditor.changePosition("conditional_mode", 260, 90)
assert.deepEqual(railEditor.layout.conditional_mode, { x: 260, y: 90, enabled: true })
railEditor.remove("conditional_mode")
assert(railEditor.inactiveWidgets.some(widget => widget.id === "conditional_mode"))
railEditor.add("conditional_mode")
assert(railEditor.activeWidgets.some(widget => widget.id === "conditional_mode"))
railEditor.undo()
assert.equal(railEditor.layout.conditional_mode.enabled, false)
railEditor.resetToStock()
assert.deepEqual(railEditor.layout.conditional_mode, { x: 476, y: 80, enabled: true })
const badRail = snapshot()
badRail.metadata.profiles.compact.widgets.model_confidence.bounds.width = 900
assert.equal(validSnapshot(badRail), false)
assert.equal(clampPlacement(data.metadata.profiles.compact, "conditional_mode", 260, 101), null)

const scoped = editor().vm
const legacyPalette = copy(scoped.state.draft.palette)
scoped.selectProfile('compact')
scoped.state.selected = 'conditional_mode'
assert.deepEqual(scoped.colorFields.map(({ id }) => id), ['cardFill', 'cardBorder'])
scoped.colorRgb('cardFill', { target: { value: '#224466' } })
assert.equal(scoped.selectedColors.cardFill, '#224466A6') // Picking a color reveals a transparent frame.
assert.deepEqual(copy(scoped.state.draft.palette), legacyPalette)
assert.equal(widgetPalette(scoped.state.draft, scoped.state.data.metadata, 'compact', 'model_confidence').cardFill, '#00000000')
scoped.state.selected = 'following_distance'
scoped.color('text', '#FEDCBAFF')
assert.equal(scoped.selectedColors.text, '#FEDCBAFF')
scoped.undo()
assert.equal(scoped.selectedColors.text, '#FFFFFFFF')
assert.equal(scoped.state.draft.widgetColors.compact.conditional_mode.cardFill, '#224466A6')
scoped.redo()
scoped.resetColors()
assert.equal(scoped.selectedColors.text, '#FFFFFFFF')
assert.equal(scoped.state.draft.widgetColors.compact.conditional_mode.cardFill, '#224466A6')
scoped.selectProfile('large')
scoped.state.selected = 'driver_monitor'
assert.equal(scoped.selectedColors.cardFill, '#00000000')
scoped.color('cardFill', '#11223344')
assert.equal(scoped.state.draft.widgetColors.compact.conditional_mode.cardFill, '#224466A6')
scoped.state.selected = 'torque_bar'
assert.equal(scoped.colorFields.length, 0)
const beforeUnsupported = copy(scoped.state.draft)
scoped.color('text', '#11223344')
assert.deepEqual(copy(scoped.state.draft), beforeUnsupported)
assert.equal(validDocument(scoped.state.draft, scoped.state.data.metadata), true)
for (const mutate of [
  doc => { doc.widgetColors.large.current_speed = { cardFill: '#FFFFFFFF' } },
  doc => { doc.widgetColors.compact.speed_limit = { text: '#FFFFFFFF' } },
  doc => { doc.widgetColors.large.unknown = {} },
  doc => { doc.widgetColors.compact = [] },
  doc => { doc.widgetColors.compact.driver_monitor = { cardFill: true } },
]) {
  const invalid = copy(scoped.state.draft); mutate(invalid)
  assert.equal(validDocument(invalid, scoped.state.data.metadata), false)
}
const migrated = editor().vm
migrated.state.draft.palette.text = '#FF0000FF'
migrated.state.selected = 'current_speed'
assert.equal(migrated.selectedColors.text, '#FF0000FF')
migrated.resetColors()
assert.equal(migrated.selectedColors.text, '#FFFFFFFF')
assert.equal(widgetPalette(migrated.state.draft, migrated.state.data.metadata, 'large', 'cruise_limits').text, '#FF0000FF')
console.log('Widget colors: real fields, visible picks, profile/widget isolation, undo/reset, legacy inheritance and invalid fields passed')
const savedSelection = editor()
savedSelection.vm.selectProfile('compact')
savedSelection.vm.state.selected = 'speed_limit_actions'
savedSelection.vm.color('cardFill', '#12345680')
const savedData = snapshot()
savedData.document = copy(savedSelection.vm.state.draft)
savedSelection.publish({ status: 'ready', data: savedData, draft: copy(savedData.document) })
assert.equal(savedSelection.vm.state.selected, 'speed_limit_actions')
assert.equal(savedSelection.vm.selectedColors.cardFill, '#12345680')

const roadEditor = editor().vm
roadEditor.roadRgb('path', { target: { value: '#FF1122' } })
assert.equal(roadEditor.roadMode, 'color')
assert.equal(roadEditor.roadColors.path, '#FF1122FF')
roadEditor.roadAlpha('path', { target: { value: '128' } })
assert.equal(roadEditor.roadGradient[0].alpha, 128 / 255)
assert.equal(roadEditor.roadLabel('pathEdge'), 'Path border')
roadEditor.selectProfile('compact')
assert.equal(roadEditor.roadMode, 'acceleration')
assert.equal(roadEditor.roadLabel('pathEdge'), 'Closest lane markings')
roadEditor.setRoadMode('rainbow')
assert.equal(roadEditor.roadGradient.length, 12)
roadEditor.undo()
assert.equal(roadEditor.roadMode, 'acceleration')
roadEditor.redo()
assert.equal(roadEditor.roadMode, 'rainbow')
roadEditor.resetRoad()
assert.deepEqual(copy(roadEditor.state.draft.roadColors.compact), {})
assert.equal(roadEditor.state.draft.roadColors.large.path, '#FF112280')
roadEditor.roadColor('pathEdge', '#AABBCCFF')
assert.equal(validDocument(roadEditor.state.draft, roadEditor.state.data.metadata), true)
for (const mutate of [
  doc => { doc.roadColors.large.pathMode = 'garbage' },
  doc => { doc.roadColors.compact.path = 1 },
  doc => { doc.roadColors.large = [] },
  doc => { doc.roadColors.compact.fake = '#123456FF' },
]) {
  const invalid = copy(roadEditor.state.draft); mutate(invalid)
  assert.equal(validDocument(invalid, roadEditor.state.data.metadata), false)
}
roadEditor.resetToStock()
assert.deepEqual(copy(roadEditor.state.draft.roadColors), { large: {}, compact: {} })
console.log('Road colors: original defaults, custom/rainbow modes, alpha, profile separation, undo/reset and validation passed')

// Both drag directions permit the sign/action pair and persist the whole draft.
const overlap = editor().vm
const bigBefore = copy(overlap.state.draft.layouts.large)
overlap.selectProfile('compact')
overlap.changePosition('speed_limit', 330, 108)
assert.equal(overlap.layout.speed_limit.y, 108)
overlap.changePosition('speed_limit_actions', 174, 100)
assert.equal(overlap.layout.speed_limit_actions.y, 100)
assert.equal(validDocument(overlap.state.draft, overlap.state.data.metadata), true)
assert.deepEqual(copy(overlap.state.draft.layouts.large), bigBefore)
const originalOrder = overlap.renderWidgets.map(widget => widget.id)
overlap.state.selected = 'speed_limit'
assert.deepEqual(overlap.renderWidgets.map(widget => widget.id), originalOrder)
overlap.state.selected = 'speed_limit_actions'
assert.deepEqual(overlap.renderWidgets.map(widget => widget.id), originalOrder)

overlap.changePosition('speed_limit_actions', 174, 0)
assert.equal(overlap.layout.speed_limit_actions.y, 0)
assert.equal(validDocument(overlap.state.draft, overlap.state.data.metadata), true)
assert.equal(overlap.renderWidgets.find(widget => widget.id === "speed_limit_actions").visualHeaderY, 62)
assert.equal(overlap.renderWidgets.find(widget => widget.id === "speed_limit_actions").visualInsetTop, 0)
assert.equal(overlap.renderWidgets.find(widget => widget.id === "speed_limit_actions").visualInsetBottom, 32)

const synchronizedDrag = editor()
const syncVm = synchronizedDrag.vm
const dragDraft = syncVm.state.draft
syncVm.startDrag("current_speed", synchronizedDrag.event(500, 150))
syncVm.moveDrag(synchronizedDrag.event(600, 200))
const remote = snapshot()
remote.revision = "f".repeat(64)
synchronizedDrag.publish({ status: "ready", data: remote, draft: copy(remote.document) })
assert.equal(syncVm.state.draft, dragDraft)
assert.equal(syncVm.state.drag.id, "current_speed")
assert.equal(synchronizedDrag.captures.has(1), true)
syncVm.endDrag(synchronizedDrag.event(600, 200))
await Promise.resolve()
assert.equal(syncVm.state.draft, dragDraft)
assert.equal(syncVm.state.needsReload, true)
assert.equal(syncVm.state.history.undo.length, 1)

// Persisted documents hold both layouts; connected hardware exposes its supported target only.
const compactHardware = editor().vm
compactHardware.state.data.supportedProfiles = ['compact']
compactHardware.state.profile = 'compact'
compactHardware.selectProfile('large')
assert.equal(compactHardware.state.profile, 'compact')
const largeHardware = editor().vm
largeHardware.state.data.supportedProfiles = ['large']
largeHardware.selectProfile('compact')
assert.equal(largeHardware.state.profile, 'large')

// Layer movement changes only stacking, supports undo/reset, and rejects corrupt orders.
const layers = editor().vm
const positionsBefore = copy(layers.state.draft.layouts)
const initialLayers = layers.layerWidgets.map(widget => widget.id)
layers.reorderLayer(initialLayers.at(-1), initialLayers[0])
assert.equal(layers.layerWidgets[0].id, initialLayers.at(-1))
assert.equal(layers.renderWidgets.at(-1).id, initialLayers.at(-1))
assert.deepEqual(layers.state.draft.layouts, positionsBefore)
assert.equal(validDocument(layers.state.draft, layers.state.data.metadata), true)
const savedLayers = copy(layers.state.draft.widgetOrder)
layers.undo()
assert.equal(layers.state.draft.widgetOrder, undefined)
layers.redo()
assert.deepEqual(layers.state.draft.widgetOrder, savedLayers)
layers.selectProfile('compact')
layers.reorderLayer('max_speed', 'torque_bar')
assert.deepEqual(layers.state.draft.widgetOrder.large, savedLayers.large)
layers.resetLayout()
assert.deepEqual(layers.state.draft.widgetOrder, savedLayers)
for (const order of [[], ['unknown'], [...savedLayers.large, savedLayers.large[0]], savedLayers.large.map(() => savedLayers.large[0])]) {
  const invalid = copy(layers.state.draft)
  invalid.widgetOrder.large = order
  assert.equal(validDocument(invalid, layers.state.data.metadata), false)
}
layers.selectProfile('large')
layers.resetLayout()
assert.equal(layers.state.draft.widgetOrder, undefined)
console.log('Widget layer order: movement, selection, profile isolation, validation, undo and reset passed')


// Display preferences travel beside the layout and join the draft.
const displayRows = [
  { key: "DeveloperSidebar", label: "Developer Sidebar", group: "Big layout", choices: ["Off", "On"], available: true, reason: "", requires: "" },
  { key: "ShowBrakeStatus", label: "Wheel brake and acceleration colors", group: "widget", choices: ["Off", "On"], available: true, reason: "", requires: "" },
  { key: "HideLeadMarker", label: "Lead Indicator", group: "Lead car", choices: ["Off", "On"], available: true, reason: "", requires: "" },
  { key: "LeadInfo", label: "Lead Info", group: "Lead car", choices: ["Off", "Distance", "Speed"], available: true, reason: "", requires: "HideLeadMarker" },
  { key: "SignalMetrics", label: "C4 amber signal border", group: "Alerts and borders", choices: ["Off", "On"], available: true, reason: "", requires: "" },
]
const withDisplayData = () => ({ ...snapshot(), display: { rows: copy(displayRows),
  values: { DeveloperSidebar: "On", ShowBrakeStatus: "Off", HideLeadMarker: "Off", LeadInfo: "Off", SignalMetrics: "Off" },
  defaults: { DeveloperSidebar: "Off", ShowBrakeStatus: "Off", HideLeadMarker: "Off", LeadInfo: "Off", SignalMetrics: "Off" } } })
const merged = withDisplay(withDisplayData())
assert.equal(validSnapshot(merged), true)
assert.deepEqual(merged.document.display, { ...merged.defaults.display, DeveloperSidebar: "On" })
assert.equal(previewDocument(snapshot().document).developerSidebar, false)
assert.equal(merged.display.length, 5)
assert.deepEqual(layoutDocument(merged.document), snapshot().document)
assert.equal(validDocument({ ...merged.document, display: { SignalMetrics: true } }, merged.metadata), false)
assert.equal(validDocument({ ...merged.document, showMaxSpeed: "no" }, merged.metadata), false)

const display = editor()
display.vm.state.data = merged; display.vm.state.draft = copy(merged.document)
assert.deepEqual(display.vm.displayGroups.map(group => [group.name, group.rows.map(row => row.key)]),
  [["Big layout", ["DeveloperSidebar"]], ["Lead car", ["HideLeadMarker", "LeadInfo"]], ["Alerts and borders", ["SignalMetrics"]]])
display.vm.setDisplay("DeveloperSidebar", "Off")
assert.equal(display.vm.displaySettingRow(display.vm.displayRow("DeveloperSidebar")).value, "Off")
assert.equal(display.vm.dirty, true)
assert.equal(previewDocument(display.vm.state.draft).developerSidebar, false)
assert.equal(display.backdropCalls.at(-1)[1].developerSidebar, false, "the draft choice reaches the native backdrop")
assert.deepEqual(layoutDocument(display.vm.state.draft), snapshot().document, "preview changes never rewrite widget positions")
assert.equal(display.vm.roadBounds.width, 2100)
assert.equal(display.vm.previewX("steering_wheel"), 1888)
assert.equal(display.vm.previewX("current_speed"), 790)
assert.equal(display.vm.previewX("cruise_limits"), 88)
assert.ok(display.vm.previewSnapTargets("steering_wheel").some(target =>
  target.axis === "x" && target.label === "Current speed" && target.line === "center" && target.value === 780))
const wheelPoint = x => ({ ...display.event(100 + x / 2, 150), pointerType: "mouse", altKey: true })
display.vm.startDrag("steering_wheel", wheelPoint(1900))
display.vm.moveDrag(wheelPoint(1920))
assert.equal(display.vm.layout.steering_wheel.x, 1608, "dragging saves the movement without adding the runtime anchor offset")
display.vm.endDrag(wheelPoint(1920))
display.vm.undo()
const wide = editor()
wide.vm.state.data = merged; wide.vm.state.draft = copy(merged.document)
wide.vm.setDisplay("DeveloperSidebar", "Off")
// With the sidebar hidden every widget reaches the whole screen; with it showing, only the road beside it.
wide.vm.changePosition("cruise_limits", 5000, 100)
assert.equal(wide.vm.layout.cruise_limits.x, 2130 - 480)
assert.equal(wide.vm.previewX("cruise_limits"), 2130 - 480)
wide.vm.changePosition("steering_wheel", -999, 75)
assert.equal(wide.vm.layout.steering_wheel.x, -270)
assert.equal(wide.vm.previewX("steering_wheel"), 30, "a right-anchored widget reaches the left edge")
assert.equal(validDocument(wide.vm.state.draft, wide.vm.state.data.metadata), true)
wide.vm.setDisplay("DeveloperSidebar", "On")
assert.equal(wide.vm.limitProfile.widgets.steering_wheel.bounds, undefined)
assert.equal(wide.vm.previewX("cruise_limits"), 1830 - 480, "a position from the wider screen is shown inside the road")
assert.equal(wide.vm.previewX("steering_wheel"), 30)
wide.vm.changePosition("cruise_limits", 5000, 100)
assert.equal(wide.vm.layout.cruise_limits.x, 1830 - 480)
assert.equal(display.vm.previewX("steering_wheel"), 1888)
display.vm.undo()
assert.equal(display.vm.displayValue("DeveloperSidebar"), "On")
assert.equal(display.vm.roadBounds.width, 1800)
assert.equal(display.vm.previewX("steering_wheel"), 1588)
assert.equal(previewDocument(display.vm.state.draft).developerSidebar, true)
for (const sidebar of ["Off", "On"]) {
  const allWidgets = editor().vm
  allWidgets.state.data = copy(merged); allWidgets.state.draft = copy(merged.document)
  for (const id of ["pip_left", "pip_right"]) {
    allWidgets.profile.widgets[id] = { ...widget(id, "pip_camera", 600, 600, 54, 426), layer: "underlay", colors: {},
      expandable: true, bounds: { x: 30, y: 30, width: 2100, height: 1020 } }
    allWidgets.layout[id] = { x: 54, y: 426, enabled: true }
  }
  allWidgets.setDisplay("DeveloperSidebar", sidebar)
  for (const [id, widget] of Object.entries(allWidgets.profile.widgets)) {
    const [width, height] = documentHelpers.widgetSize(widget, allWidgets.layout[id])
    for (const [x, y] of [[-9999, -9999], [-9999, 9999], [9999, -9999], [9999, 9999]]) {
      allWidgets.changePosition(id, x, y)
      assert.equal(allWidgets.previewX(id), x < 0 ? 30 : (sidebar === "Off" ? 2130 : 1830) - width, id)
      assert.equal(allWidgets.layout[id].y, y < 0 ? 30 : 1050 - height, id)
      assert.equal(validDocument(allWidgets.state.draft, allWidgets.state.data.metadata), true, id)
    }
  }
}

const shiftedTray = editor(), shifted = shiftedTray.vm
shifted.state.data = { ...merged, supportedProfiles: ["large", "compact"] }; shifted.state.draft = copy(merged.document)
shifted.setDisplay("DeveloperSidebar", "Off")
shifted.layout.steering_wheel.enabled = false
const trayPoint = x => ({ ...shiftedTray.event(100 + x / 2, 200), pointerType: "mouse", altKey: true })
shifted.startDrag("steering_wheel", trayPoint(1900), true)
shifted.moveDrag(trayPoint(1940))
assert.equal(shifted.previewX("steering_wheel") + 96, 1940, "a tray drop stays centered under the pointer")
shifted.endDrag(trayPoint(1940))
shifted.selectProfile("compact")
assert.equal(shifted.expandedRoad, false)
assert.equal(shifted.roadBounds.width, 476)
const leadInfo = display.vm.displayRow("LeadInfo")
assert.equal(display.vm.displaySettingRow(leadInfo).reason, "Turn on Lead Indicator first")
display.vm.setDisplay("LeadInfo", "Distance")
assert.equal(display.vm.displayValue("LeadInfo"), "Off", "blocked until the draft shows the Lead indicator")
display.vm.setDisplay("HideLeadMarker", "On")
display.vm.setDisplay("LeadInfo", "Distance")
assert.equal(display.vm.displayValue("LeadInfo"), "Distance")
assert.equal(display.vm.dirty, true)
display.vm.undo(); display.vm.undo()
assert.equal(display.vm.dirty, false)
display.vm.state.selected = "cruise_limits"
display.vm.setShowMaxSpeed({ target: { checked: false } })
assert.equal(display.vm.state.draft.showMaxSpeed, false)
assert.equal(validDocument(display.vm.state.draft, display.vm.state.data.metadata), true)
display.vm.resetToStock()
assert.deepEqual(display.vm.state.draft, merged.defaults)
assert.match(OnroadLayoutPage.template, /state\.inspectorPanel = 'display'">Options<\/button>/)
assert.doesNotMatch(OnroadLayoutPage.template, /Follow Appearance Settings/)
assert.equal(OnroadLayoutPage.setup({ mode: "local", unauthorized() {}, initialPanel: "display" }).state.inspectorPanel, "display")
assert.equal(OnroadLayoutPage.setup({ mode: "local", unauthorized() {}, initialPanel: "display", projection: true }).state.inspectorPanel, "widgets")

// Target switches must not be swallowed while a layout loads or a drag flag lingers.
const switching = editor()
switching.vm.state.status = "loading"
let proceeded = 0
switching.vm.requestLeave(() => { proceeded++ })
assert.equal(proceeded, 1)
switching.vm.state.status = "ready"
switching.vm.state.layerDrag = { id: "current_speed", pointerId: 9, rows: [], before: copy(switching.vm.state.draft) }
switching.vm.requestLeave(() => { proceeded++ })
assert.equal(switching.vm.state.layerDrag, null, "a stray layer drag is cancelled")
assert.equal(proceeded, 2)
switching.vm.state.status = "saving"
switching.vm.requestLeave(() => { proceeded++ })
assert.equal(proceeded, 2, "an in-flight save finishes first")
assert.match(OnroadLayoutPage.template, /<section class="gx-settings gx-layout" aria-label="Colors and layout" :aria-busy/)
assert.match(OnroadLayoutPage.template, /<div class="gx-layout__workspace" :inert="busy">/)

// An explicit reload replaces a background poll instead of being dropped.
const reloading = feedFixture()
reloading.feed.start(); await reloading.reply(0, withDisplayData())
assert.equal(reloading.feed.data.document.display.SignalMetrics, "Off")
reloading.feed.load(true)
assert.equal(reloading.requests.length, 2)
reloading.feed.load(true)
assert.equal(reloading.requests.length, 2, "polls never stack")
reloading.feed.load()
assert.equal(reloading.requests.length, 3)
assert.equal(reloading.updates.at(-1).status, "loading")
await reloading.reply(2, withDisplayData())
assert.equal(reloading.updates.at(-1).notice, "Reloaded the saved layout.")
assert.ok(reloading.updates.at(-1).draft)
const settled = reloading.updates.length
await reloading.reply(1, withDisplayData())
assert.equal(reloading.updates.length, settled, "the replaced poll is ignored")
const failedSave = feedFixture()
failedSave.feed.start(); await failedSave.reply(0, withDisplayData())
failedSave.feed.save(failedSave.feed.data.document)
await failedSave.reply(1, { ...withDisplayData(), failed: ["Lead Info"] })
assert.match(failedSave.updates.at(-1).notice, /did not change: Lead Info/)
console.log('Display options, target switching and reload during polling passed')

// A closed developer sidebar makes the torque arc taller than its narrow-road box.
{
  const sample = snapshot(), profile = sample.metadata.profiles.large
  profile.widgets.cruise_limits.height = 168
  profile.widgets.cruise_limits.confirmation = { x: 172, y: -12, width: 419, height: 204 }
  profile.widgets.torque_bar.confirmationObstacle = { x: 0, y: -48, width: 1250, height: 289 }
  assert.equal(documentHelpers.overlapsReserved(profile, 'cruise_limits', 88, 550, sample.document.layouts.large), true)
  delete profile.widgets.torque_bar.confirmationObstacle
  assert.equal(documentHelpers.overlapsReserved(profile, 'cruise_limits', 88, 550, sample.document.layouts.large), false)
  profile.widgets.cruise_limits.height = 276
  assert.equal(documentHelpers.overlapsReserved(profile, 'cruise_limits', 88, 500, sample.document.layouts.large), true)
}


// Ford-only catalog visibility keeps the saved placement available after switching cars.
const fog = snapshot()
for (const [profile, x, y, width, height] of [["large", 165, 294, 112, 70], ["compact", 220, 12, 45, 28]]) {
  const definition = { ...widget("Rear fog indicator", "rear_fog", width, height, x, y), colors: {}, hidden: false }
  fog.metadata.profiles[profile].widgets.rear_fog = definition
  fog.document.layouts[profile].rear_fog = { ...definition.default }
}
fog.defaults = copy(fog.document)
assert.equal(validSnapshot(fog), true)
const context = { profile: fog.metadata.profiles.compact, layout: fog.document.layouts.compact }
assert.ok(OnroadLayoutPage.computed.widgets.call(context).some(widget => widget.id === "rear_fog"))
context.profile.widgets.rear_fog.hidden = true
assert.ok(!OnroadLayoutPage.computed.widgets.call(context).some(widget => widget.id === "rear_fog"))
assert.equal(context.layout.rear_fog.enabled, true)
assert.equal(validSnapshot(fog), true)
context.profile.widgets.rear_fog.hidden = "yes"
assert.equal(validSnapshot(fog), false)
context.profile.widgets.rear_fog.hidden = false
context.profile.widgets.max_speed.hidden = true
assert.equal(validSnapshot(fog), false)
console.log("Ford rear fog: make visibility, saved placement and strict metadata passed")
