import assert from "node:assert/strict"
import { compile } from "../web/vendor/vue/vue.esm-browser.js"
import { snapshot } from "./test_onroad_layout.mjs"
import { editorSnapshot, projectionPayload } from "../web/js/projection-layout.js"
import { LayoutWidgetPreview, OnroadLayoutPage, clampBox, clampPlacement, orderedWidgetIds, placementLimits, validDocument, validSnapshot, widgetSize } from "../web/js/onroad-layout.js"

const copy = (v) => JSON.parse(JSON.stringify(v))
const native = snapshot()
const metadata = copy(native.metadata.profiles.large)
// The Android Auto metadata has its own canvas, so the Big screen's widened bounds and anchors do not apply.
for (const widget of Object.values(metadata.widgets)) for (const field of ["bounds", "anchorShift", "expandable"]) delete widget[field]
metadata.width = 2880
metadata.bounds.width = 2820
metadata.widgets.current_speed.default.x += 510
metadata.widgets.steering_wheel.default.x += 1020
// Mirrors projection_layout.layout_metadata_for_viewport((2880, 1080)).
metadata.widgets.nav_card = { label: "Turn-by-turn", kind: "nav_card", width: 560, height: 195, colors: {},
  default: { x: 2250, y: 415, enabled: true } }
metadata.widgets.nav_map = { label: "Map overlay", kind: "nav_map", width: 860, height: 410, colors: {},
  box: { minWidth: 280, maxWidth: 2820, minHeight: 200, maxHeight: 1020 }, opacity: { min: 15, max: 100, default: 50 },
  default: { x: 1950, y: 625, enabled: false, width: 860, height: 410, opacity: 50 } }
for (const [index, label] of ["Home", "Work"].entries()) {
  const key = `nav_${label.toLowerCase()}`
  metadata.widgets[key] = { label, kind: key, width: 320, height: 110, iconSize: 110, colors: {},
    default: { x: 2195 + index * 335, y: 280, enabled: false, display: 'words' } }
}
metadata.widgets.car_exit = { label: "Exit to car", kind: "car_exit", width: 96, height: 96, colors: {}, required: true, frontmost: true,
  note: "Returns to the car's own screen without disconnecting Android Auto. Always enabled and above other widgets.",
  default: { x: 30, y: 954, enabled: true } }
metadata.widgets.bookmark = { label: 'Bookmark', kind: 'bookmark', width: 320, height: 110, iconSize: 110, colors: {},
  default: {x: 2530, y: 155, enabled: false, display: 'words'} }
for (const key of ['nav_home', 'nav_work', 'bookmark', 'car_exit']) {
  Object.assign(metadata.widgets[key], {opacity: {min: 0, max: 100, default: 100}, backgroundToggle: true})
  Object.assign(metadata.widgets[key].default, {opacity: 100, background: true})
}
for (const [index, key] of ["pip_left", "pip_right"].entries())
  metadata.widgets[key] = { label: index ? "Right camera" : "Left camera", kind: "pip_camera", width: 300, height: 300, colors: {},
    default: { x: 30 + index * 2400, y: 300, enabled: false } }
metadata.widgetOrder = ["nav_map", "pip_left", "pip_right", ...(metadata.widgetOrder || Object.keys(native.metadata.profiles.large.widgets)).filter(id => !id.startsWith("pip_")), "nav_card", "nav_home", "nav_work", "bookmark", "car_exit"]
const widgets = Object.fromEntries(Object.entries(metadata.widgets).map(([id, widget]) => [id,
  { ...widget.default, ...(widget.resizable ? { size: widget.resizable.default } : {}) }]))
const doc = { version: 5, clock24Hour: false, canvas: { width: 2880, height: 1080 }, widgets }
const raw = { version: 1, document: doc, defaults: copy(doc), metadata,
  screen: { width: 1280, height: 720, margin_width: 0, margin_height: 240 },
  revision: "aa-map", editable: true, valid: true,
  colors: { palette: native.document.palette, widgetColors: native.document.widgetColors.large, roadColors: {} } }
const data = editorSnapshot(raw)
const profile = data.metadata.profiles.large
assert.equal(validSnapshot(data), true)

// Size follows the placement for box widgets, the size field for the wheel, and the metadata otherwise.
const layout = data.document.layouts.large
assert.deepEqual(widgetSize(profile.widgets.nav_map, layout.nav_map), [860, 410])
assert.deepEqual(widgetSize(profile.widgets.steering_wheel, { ...layout.steering_wheel, size: 200 }), [200, 200])
assert.deepEqual(widgetSize(profile.widgets.nav_card, layout.nav_card), [560, 195])
layout.nav_map.width = 1200
assert.equal(placementLimits(profile, "nav_map", layout).maxX, 30 + 2820 - 1200)
assert.equal(clampPlacement(profile, "nav_map", 5000, 5000, layout).x, 1650)
layout.nav_map.width = 860

// Resizing stays inside the box limits and the screen from the current corner.
assert.deepEqual(clampBox(profile, "nav_map", { x: 1950, y: 625 }, 5000, 5000), { width: 900, height: 425 })
assert.deepEqual(clampBox(profile, "nav_map", { x: 30, y: 30 }, 10, 10), { width: 280, height: 200 })
assert.equal(clampBox(profile, "nav_card", { x: 30, y: 30 }, 600, 300), null, "the turn card has a fixed size")

// Documents: width, height and opacity are required for the map and range-checked.
const good = copy(data.document)
assert.equal(validDocument(good, data.metadata), true)
for (const change of [{ opacity: 101 }, { opacity: 14 }, { opacity: 50.5 }, { width: 279 }, { height: 1021 }, { width: 860.5 }]) {
  const bad = copy(good)
  Object.assign(bad.layouts.large.nav_map, change)
  assert.equal(validDocument(bad, data.metadata), false, JSON.stringify(change))
}
const missing = copy(good)
delete missing.layouts.large.nav_map.opacity
assert.equal(validDocument(missing, data.metadata), false)
const offscreen = copy(good)
Object.assign(offscreen.layouts.large.nav_map, { x: 2000, width: 900 })
assert.equal(validDocument(offscreen, data.metadata), false, "a wide map cannot hang off the right edge")

// Box metadata is only for the map, on either screen.
const comma = snapshot()
comma.metadata.profiles.large.widgets.current_speed.box = { minWidth: 100, maxWidth: 600, minHeight: 100, maxHeight: 300 }
comma.metadata.profiles.large.widgets.current_speed.opacity = { min: 10, max: 100, default: 50 }
assert.equal(validSnapshot(comma), false)
const wrongKind = copy(data)
wrongKind.metadata.profiles.large.widgets.nav_card.box = { minWidth: 100, maxWidth: 600, minHeight: 100, maxHeight: 300 }
assert.equal(validSnapshot(wrongKind), false)

// The payload carries the map's size and opacity through untouched.
const draft = copy(data.document)
Object.assign(draft.layouts.large.nav_map, { enabled: true, width: 640, height: 480, opacity: 45 })
assert.deepEqual(projectionPayload({ revision: "r", document: draft }, data.metadata).document.widgets.nav_map,
  { x: 1950, y: 625, enabled: true, width: 640, height: 480, opacity: 45 })

// Editor methods: drag the corner handle, type a size, slide the opacity; each is one undo step.
const vm = {
  state: { drag: null, profile: "large", selected: "nav_map", draft: copy(data.document), history: { undo: [], redo: [], group: null },
    placementError: "", notice: "", colorError: "" },
  editable: true, $refs: { preview: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 2880, height: 1080 }),
    setPointerCapture() {}, hasPointerCapture: () => false, releasePointerCapture() {} } },
  finishColorEdit() { this.state.history.group = null },
  sceneHidden() { return false },
}
Object.defineProperties(vm, {
  canUndo: { get: () => vm.state.history.undo.length > 0 },
  canRedo: { get: () => vm.state.history.redo.length > 0 },
  profile: { get: () => profile },
  limitProfile: { get: () => profile },
  layout: { get: () => vm.state.draft.layouts.large },
  selectedWidget: { get: () => profile.widgets[vm.state.selected] },
  selectedPosition: { get: () => vm.state.draft.layouts.large[vm.state.selected] },
  ...Object.fromEntries(["cameraIds", "camerasOnTop", "camerasLocked"].map(name => [name, { get: () => OnroadLayoutPage.computed[name].call(vm) }])),
})
for (const name of ["watchRelease", "unwatchRelease", "recordChange", "resizeBox", "boxInput", "opacityInput", "setWidgetBackground", "undo", "redo", "startResize", "moveDrag", "endDrag", "releaseDrag", "changePosition", "add", "remove", "requestRemove", "reorderLayer", "startLayerDrag", "setFavoriteDisplay", "layerFixed", "toggleCameraLock", "confirmCameraUnlock", "cancelCameraUnlock"])
  vm[name] = OnroadLayoutPage.methods[name].bind(vm)
const pointer = (x, y) => ({ clientX: x, clientY: y, pointerId: 7, button: 0, preventDefault() {}, stopPropagation() {} })
vm.startResize("nav_map", pointer(1950 + 860, 625 + 410))
assert.equal(vm.state.drag.resize, true)
vm.moveDrag(pointer(1950 + 700, 625 + 300))
vm.endDrag(pointer(1950 + 700, 625 + 300))
assert.deepEqual([vm.layout.nav_map.width, vm.layout.nav_map.height], [700, 300])
assert.equal(vm.state.history.undo.length, 1)
vm.boxInput("width", { target: { value: "99999" } })
assert.equal(vm.layout.nav_map.width, 900, "typed sizes clamp to the room from the current corner")
vm.opacityInput({ target: { value: "40" } })
vm.opacityInput({ target: { value: "35" } })
assert.equal(vm.layout.nav_map.opacity, 35)
assert.equal(vm.state.history.undo.length, 3, "one slide is one undo step")
vm.opacityInput({ target: { value: "5" } })
assert.equal(vm.layout.nav_map.opacity, 35, "below the minimum is ignored")
assert.equal(validDocument(vm.state.draft, data.metadata), true)

// Home and Work use the same add, place, layer-order, remove and save controls.
vm.state.data = data
for (const key of ["nav_home", "nav_work", "bookmark"]) {
  assert.equal(vm.layout[key].enabled, false)
  vm.add(key)
  assert.equal(vm.layout[key].enabled, true)
  vm.changePosition(key, 400, 700)
  assert.deepEqual([vm.layout[key].x, vm.layout[key].y], [400, 700])
  vm.reorderLayer(key, "nav_map")
  const order = orderedWidgetIds(vm.state.draft, data.metadata, "large")
  assert.ok(order.indexOf(key) < order.indexOf("nav_map"))
  vm.remove(key)
  assert.equal(vm.layout[key].enabled, false)
}
const savedFavorites = projectionPayload({ revision: "favorites", document: vm.state.draft }, data.metadata).document
assert.equal(savedFavorites.widgets.nav_home.enabled, false)
assert.equal(savedFavorites.widgets.nav_work.enabled, false)
assert.deepEqual(savedFavorites.widgetOrder, vm.state.draft.widgetOrder.large)
assert.equal(validDocument(vm.state.draft, data.metadata), true)

// Independent icon choices shrink the preview footprint and survive saving.
vm.state.selected = 'nav_home'
for (const key of ['nav_home', 'nav_work']) {
  vm.state.selected = key
  const center = vm.layout[key].x + 160
  vm.setFavoriteDisplay('icons')
  assert.equal(vm.layout[key].x + 55, center, key + ' keeps its center when changing to an icon')
  vm.setFavoriteDisplay('words')
  assert.equal(vm.layout[key].x + 160, center, key + ' keeps its center when changing back to words')
  vm.undo()
  vm.undo()
}
vm.state.selected = 'nav_home'
const undoBeforeDisplay = vm.state.history.undo.length
vm.setFavoriteDisplay('icons')
assert.deepEqual(widgetSize(profile.widgets.nav_home, vm.layout.nav_home), [110, 110])
assert.equal(vm.layout.nav_work.display, 'words')
assert.equal(vm.state.history.undo.length, undoBeforeDisplay + 1)
vm.changePosition('nav_home', 2740, 700)
assert.equal(vm.layout.nav_home.x, 2740, 'compact icons can sit close to the right edge')
vm.setFavoriteDisplay('words')
assert.equal(vm.layout.nav_home.x, 2530, 'switching back to words keeps the whole card on screen')
assert.deepEqual(widgetSize(profile.widgets.nav_home, vm.layout.nav_home), [320, 110])
vm.setFavoriteDisplay('icons')
assert.equal(projectionPayload({revision:'display', document:vm.state.draft}, data.metadata).document.widgets.nav_home.display, 'icons')
assert.equal(validDocument(vm.state.draft, data.metadata), true)
for (const display of ['emoji', null, true, 1]) {
  const invalid = copy(vm.state.draft)
  invalid.layouts.large.nav_home.display = display
  assert.equal(validDocument(invalid, data.metadata), false)
}

// Each action has independent appearance, undo/redo, strict validation and saved preview values.
vm.editAvailable = true
for (const [index, key] of ['nav_home', 'nav_work', 'bookmark', 'car_exit'].entries()) {
  vm.state.selected = key
  vm.finishColorEdit()
  const before = copy(vm.state.draft)
  vm.opacityInput({target: {value: String(25 + index * 10)}})
  vm.setWidgetBackground('Off')
  assert.equal(vm.layout[key].opacity, 25 + index * 10)
  assert.equal(vm.layout[key].background, false)
  vm.undo()
  assert.equal(vm.layout[key].background, true)
  vm.undo()
  assert.deepEqual(vm.state.draft, before)
  vm.redo()
  vm.redo()
  const payload = projectionPayload({revision: 'appearance', document: vm.state.draft}, data.metadata)
  assert.equal(payload.document.widgets[key].opacity, 25 + index * 10)
  assert.equal(payload.document.widgets[key].background, false)
  const preview = OnroadLayoutPage.computed.renderWidgets.call({layerWidgets: [{id: key, ...profile.widgets[key]}], layout: vm.layout,
    previewHidden: () => false})[0]
  assert.equal(preview.opacity, 25 + index * 10)
  assert.equal(preview.background, false)
  assert.equal(validDocument(vm.state.draft, data.metadata), true)
  for (const change of [{opacity: -1}, {opacity: 101}, {opacity: 50.5}, {opacity: true}, {background: 0}, {background: 'false'}]) {
    const invalid = copy(vm.state.draft)
    Object.assign(invalid.layouts.large[key], change)
    assert.equal(validDocument(invalid, data.metadata), false)
  }
  for (const blocked of [{editable: false}, {state: {...vm.state, drag: {}}}, {state: {...vm.state, layerDrag: {}}}]) {
    OnroadLayoutPage.methods.opacityInput.call({...vm, ...blocked}, {target: {value: '90'}})
    OnroadLayoutPage.methods.setWidgetBackground.call({...vm, ...blocked}, 'On')
    assert.equal(vm.layout[key].opacity, 25 + index * 10)
    assert.equal(vm.layout[key].background, false)
  }
}
vm.state.selected = 'bookmark'
vm.add('bookmark')
vm.setFavoriteDisplay('icons')
assert.deepEqual(widgetSize(profile.widgets.bookmark, vm.layout.bookmark), [110, 110])
assert.equal(projectionPayload({document: vm.state.draft}, data.metadata).document.widgets.bookmark.display, 'icons')
assert.equal(vm.layout.bookmark.opacity, 45)
assert.equal(vm.layout.bookmark.background, false)
vm.setFavoriteDisplay('words')
assert.deepEqual(widgetSize(profile.widgets.bookmark, vm.layout.bookmark), [320, 110])
for (const change of [{opacity: {min: 0, max: 101, default: 100}}, {backgroundToggle: false}]) {
  const invalid = copy(data)
  Object.assign(invalid.metadata.profiles.large.widgets.car_exit, change)
  assert.equal(validSnapshot(invalid), false)
}

// The escape control moves normally but cannot be disabled, removed, or placed below another widget.
vm.state.selected = 'car_exit'
assert.equal(profile.widgets.car_exit.label, 'Exit to car')
assert.deepEqual(widgetSize(profile.widgets.car_exit, vm.layout.car_exit), [96, 96])
assert.deepEqual([vm.layout.car_exit.x, vm.layout.car_exit.y], [30, 954])
assert.equal(vm.layout.car_exit.enabled, true)
vm.changePosition('car_exit', 200, 800)
assert.deepEqual([vm.layout.car_exit.x, vm.layout.car_exit.y], [200, 800])
vm.remove('car_exit')
assert.equal(vm.layout.car_exit.enabled, true)
vm.requestRemove('car_exit')
assert.equal(vm.state.removeConfirm, undefined)
const pinnedOrder = [...orderedWidgetIds(vm.state.draft, data.metadata, 'large')]
vm.startLayerDrag({ button: 0 }, 'car_exit')
assert.equal(vm.state.layerDrag, undefined)
vm.reorderLayer('car_exit', 'nav_map')
vm.reorderLayer('nav_map', 'car_exit')
assert.deepEqual(orderedWidgetIds(vm.state.draft, data.metadata, 'large'), pinnedOrder)

// Side cameras lock just under Exit to car; unlocking needs a confirmation.
assert.equal(vm.camerasLocked, false, "an order with widgets above the cameras opens unlocked")
vm.toggleCameraLock()
assert.equal(vm.state.pipUnlockConfirm, undefined, "locking needs no warning")
const lockedOrder = [...orderedWidgetIds(vm.state.draft, data.metadata, 'large')]
assert.deepEqual(lockedOrder.slice(-3), ['pip_left', 'pip_right', 'car_exit'])
assert.equal(vm.camerasLocked, true)
vm.reorderLayer('nav_map', 'pip_left')
vm.reorderLayer('pip_left', 'nav_map')
vm.startLayerDrag({ button: 0 }, 'pip_right')
assert.equal(vm.state.layerDrag, undefined)
assert.deepEqual(orderedWidgetIds(vm.state.draft, data.metadata, 'large'), lockedOrder)
vm.toggleCameraLock()
assert.equal(vm.state.pipUnlockConfirm, true)
assert.equal(vm.camerasLocked, true, "the lock holds until the warning is confirmed")
vm.cancelCameraUnlock()
vm.toggleCameraLock()
vm.confirmCameraUnlock()
assert.equal(vm.camerasLocked, false)
vm.reorderLayer('nav_map', 'pip_right')
assert.deepEqual(orderedWidgetIds(vm.state.draft, data.metadata, 'large').slice(-3), ['pip_right', 'nav_map', 'car_exit'])
assert.equal(validDocument(vm.state.draft, data.metadata), true)
vm.toggleCameraLock()
assert.equal(vm.camerasLocked, true, "locking moves the cameras back under Exit to car")
assert.deepEqual(orderedWidgetIds(vm.state.draft, data.metadata, 'large').slice(-3), ['pip_left', 'pip_right', 'car_exit'])
vm.undo()
vm.undo()
assert.deepEqual(orderedWidgetIds(vm.state.draft, data.metadata, 'large'), lockedOrder)
vm.undo()
assert.deepEqual(orderedWidgetIds(vm.state.draft, data.metadata, 'large'), pinnedOrder)
vm.state.pipUnlocked = false
const disabledExit = copy(vm.state.draft)
disabledExit.layouts.large.car_exit.enabled = false
assert.equal(validDocument(disabledExit, data.metadata), false)
const buriedExit = copy(vm.state.draft)
buriedExit.widgetOrder ||= {}
buriedExit.widgetOrder.large = ['car_exit', ...pinnedOrder.filter(id => id !== 'car_exit')]
assert.equal(validDocument(buriedExit, data.metadata), false)
assert.match(OnroadLayoutPage.template, /v-if="!widget.required"/)
assert.match(OnroadLayoutPage.template, />Required</)
assert.match(LayoutWidgetPreview.template, /data-preview-icon="door-exit"/)

compile(OnroadLayoutPage.template, { decodeEntities: value => value.replaceAll("&amp;", "&") })
compile(LayoutWidgetPreview.template, { decodeEntities: value => value.replaceAll("&amp;", "&") })
console.log("Projection widgets: map resizing/opacity, Home/Work/Bookmark add/move/order/remove, appearance, strict documents and payload passed")
