import assert from "node:assert/strict"
import { snapshot } from "./test_onroad_layout.mjs"
import { editorSnapshot, projectionPayload } from "../web/js/projection-layout.js"
import { clampBox, validDocument, validSnapshot, widgetSize } from "../web/js/onroad-layout.js"

const copy = (v) => JSON.parse(JSON.stringify(v))
const native = snapshot()
const metadata = copy(native.metadata.profiles.large)
for (const widget of Object.values(metadata.widgets)) for (const field of ["bounds", "anchorShift", "expandable"]) delete widget[field]
metadata.width = 1860
metadata.bounds.width = 1800
metadata.widgets.car_exit = { label: "Exit to car", kind: "car_exit", width: 96, height: 96, colors: {}, required: true, frontmost: true,
  default: { x: 30, y: 954, enabled: true } }
// Mirrors projection_layout.layout_metadata_for_viewport((1860, 1080))['widgets']['birds_eye'].
metadata.widgets.birds_eye = { label: "Birds-eye view", kind: "birds_eye", width: 520, height: 600, colors: {}, layer: "underlay",
  note: "Top-down view.", box: { minWidth: 240, maxWidth: 1800, minHeight: 240, maxHeight: 1020 },
  opacity: { min: 20, max: 100, default: 90 }, displayModes: ["overlay", "full"],
  default: { x: 1310, y: 290, width: 520, height: 600, enabled: true, opacity: 90, display: "overlay" } }
metadata.widgetOrder = ["birds_eye", ...(metadata.widgetOrder || Object.keys(native.metadata.profiles.large.widgets)), "car_exit"]
const widgets = Object.fromEntries(Object.entries(metadata.widgets).map(([id, widget]) => [id,
  { ...widget.default, ...(widget.resizable ? { size: widget.resizable.default } : {}) }]))
const doc = { version: 5, clock24Hour: false, canvas: { width: 1860, height: 1080 }, widgets }
const raw = { version: 1, document: doc, defaults: copy(doc), metadata,
  screen: { width: 1280, height: 720, margin_width: 0, margin_height: 0 },
  revision: "aa-birds-eye", editable: true, valid: true,
  colors: { palette: native.document.palette, widgetColors: native.document.widgetColors.large, roadColors: {} } }
const data = editorSnapshot(raw)
const profile = data.metadata.profiles.large
assert.equal(validSnapshot(data), true, "the birds-eye widget is accepted as a resizable, translucent projection widget")

const layout = data.document.layouts.large
assert.deepEqual(widgetSize(profile.widgets.birds_eye, layout.birds_eye), [520, 600])
assert.deepEqual(clampBox(profile, "birds_eye", layout.birds_eye, 9000, 100), { width: 520, height: 240 })

const full = copy(data.document)
full.layouts.large.birds_eye.display = "full"
assert.equal(validDocument(full, data.metadata), true, "full screen is a valid display choice")
assert.equal(projectionPayload({ revision: "r", document: full }, data.metadata).document.widgets.birds_eye.display, "full")
for (const bad of ["words", "icons", undefined]) {
  const broken = copy(data.document)
  if (bad === undefined) delete broken.layouts.large.birds_eye.display
  else broken.layouts.large.birds_eye.display = bad
  assert.equal(validDocument(broken, data.metadata), false, `display ${bad} is rejected`)
}

// displayModes belong to the birds-eye projection widget only.
const stray = copy(raw)
stray.metadata.widgets.car_exit.displayModes = ["overlay", "full"]
assert.equal(validSnapshot(editorSnapshot(stray)), false)
console.log("projection birds-eye widget ok")
