import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { SettingsFeed } from "../web/js/settings.js"
import { PipPage } from "../web/js/pip.js"
import { displayPoint, maskDraft, sourcePoint } from "../web/js/pip-geometry.js"

const rect = { left: 10, top: 20, width: 390, height: 221 }
assert.deepEqual(sourcePoint(10, 20, rect, 1344, 760, false), [0, 0])
assert.deepEqual(sourcePoint(400, 241, rect, 1344, 760, false), [1344, 760])
assert.deepEqual(sourcePoint(10, 20, rect, 1344, 760, true), [1344, 0])
assert.deepEqual(sourcePoint(400, 241, rect, 1344, 760, true), [0, 760])
assert.deepEqual(displayPoint([1100, 350], 1344, true), [244, 350])
assert.deepEqual(displayPoint([1100, 350], 1344, false), [1100, 350])
assert.equal(sourcePoint(0, 20, rect, 1344, 760, false), null)
assert.equal(sourcePoint(10, 20, rect, 1344, 760, null), null)
assert.deepEqual(maskDraft(1344, 760, 300, null, [1100, 350]), {
  width: 1344, height: 760, crop_size: 300, center_left: null, center_right: [1100, 350],
})
assert.equal(maskDraft(1344, 760, 300, null, null), null)
assert.equal(maskDraft(1344, 760, 300, null, [1200, 350]), null)
assert.equal(maskDraft(1920, 1080, 300, null, [1100, 350]), null)
assert.equal(maskDraft(1928, 1208, 580, [315, 548], [1571, 539])?.crop_size, 580)

const requests = [], states = []
const feed = new SettingsFeed({ publish: (state) => states.push(state),
  fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })),
  later: () => 1, cancelTimer: () => {} })
feed.start("pip")
requests[0].resolve({ ok: true, status: 200, json: async () => ({ page: "pip", view: "view1", rows: [] }) })
await new Promise((resolve) => setImmediate(resolve))
feed.preview(7, 0, maskDraft(1344, 760, 300, null, [1100, 350]))
assert.equal(requests[1].url, "./api/settings/preview")
assert.deepEqual(JSON.parse(requests[1].options.body), {
  view: "view1", row: 7, direction: 0,
  draft: { width: 1344, height: 760, crop_size: 300, center_left: null, center_right: [1100, 350] },
})
feed.stop()
requests[1].resolve({ ok: true, status: 200, json: async () => ({ intent: "late", question: "Save?" }) })
await new Promise((resolve) => setImmediate(resolve))
assert.equal(states.at(-1).status, "idle")
assert.equal(states.at(-1).pending, null)

let placed = null, note = ""
const editor = { canEdit: true, state: { width: 1344, height: 760, cropSize: 300,
  centerLeft: null, centerRight: [1100, 350], activeSide: "centerRight", invert: true,
  set localNote(value) { note = value }, get localNote() { return note } },
  $refs: { canvas: { getBoundingClientRect: () => rect } }, redraw() { placed = this.state.centerRight } }
PipPage.methods.place.call(editor, { clientX: 80, clientY: 120 })
assert.deepEqual(placed, [1103, 344])
PipPage.methods.place.call(editor, { clientX: 10, clientY: 20 })
assert.deepEqual(placed, [1103, 344])
assert.match(note, /far enough/)
const disabled = { mode: "local", state: { data: { parked: true, editorRow: 1,
  rows: [{}, { available: true }] }, status: "ready", pending: null, reviewing: false,
  invert: null, width: 1344, height: 760 } }
for (const key of ["busy", "editable"]) Object.defineProperty(disabled, key, { get: () => PipPage.computed[key].call(disabled) })
assert.equal(PipPage.computed.canEdit.call(disabled), false)
disabled.state.invert = false
assert.equal(PipPage.computed.canEdit.call(disabled), false)
disabled.state.imageName = "Live cabin camera"
assert.equal(PipPage.computed.canEdit.call(disabled), true)
disabled.state.status = "saving"
assert.equal(PipPage.computed.canEdit.call(disabled), false)

assert.doesNotMatch(PipPage.template, /type="file"/)
assert.match(PipPage.template, /Camera Crop/)
assert.doesNotMatch(PipPage.template, /Horizontal position|Vertical position/)
assert.match(PipPage.template, /Camera crop size/)
assert.match(PipPage.template, /Crop size/)
assert.doesNotMatch(PipPage.template, /Live selected crop preview/)
assert.doesNotMatch(PipPage.template, /gx-pip__preview/)
assert.match(PipPage.template, /@pointerdown="pointerDown"/)
assert.match(PipPage.template, /Warming up the camera/)
assert.match(PipPage.template, /state\.pending\.question/)
assert.doesNotMatch(readFileSync(new URL("../web/js/pip.js", import.meta.url), "utf8"), /getPipSnapshot|FormData|fetch\(/)
assert.match(readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8"), /<PipPage v-else-if="route\.path === '\/cameras\/pip'"/)

const pointer = (x, y) => ({ pointerId: 1, clientX: rect.left + (1344 - x) / 1344 * rect.width, clientY: rect.top + y / 760 * rect.height })
editor.$refs.canvas.setPointerCapture = () => {}
for (const method of ['place', 'pointerMove', 'resizeCrop']) editor[method] = event => PipPage.methods[method].call(editor, event)
PipPage.methods.pointerDown.call(editor, pointer(1103, 344))
PipPage.methods.pointerMove.call(editor, pointer(900, 500))
assert.deepEqual(placed, [900, 500], 'dragging moves the selected circle in mirrored coordinates')
PipPage.methods.pointerUp.call(editor)
assert.equal(editor._gesture, null)
PipPage.methods.pointerDown.call(editor, pointer(1050, 500))
PipPage.methods.pointerMove.call(editor, pointer(1070, 500))
assert.equal(editor.state.cropSize, 340, 'dragging the edge changes the shared crop size')
PipPage.methods.pointerUp.call(editor)
let stopped = 0
const life = { liveCamera: { stop() { stopped++ } }, feed: { stop() { stopped++ } } }
PipPage.beforeUnmount.call(life)
assert.equal(stopped, 2)

assert.equal((PipPage.template.match(/type="range"/g) || []).length, 1)

// The side buttons name the vehicle side, with the color of that side's outline.
assert.match(PipPage.template, /gx-side-btn" :style="\{ '--side': side\.color \}"/)
assert.match(PipPage.template, /gx-side-dot" aria-hidden="true"><\/span>\{\{ side\.label \}\}/)
assert.match(PipPage.template, /<h2>Blind Spot Camera<\/h2>/)
