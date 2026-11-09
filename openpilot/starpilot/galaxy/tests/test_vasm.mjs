import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { SettingsFeed } from "../web/js/settings.js"
import { VasmPage } from "../web/js/vasm.js"
import { annotationDraft, displaySide, sourcePoint, validPolygon } from "../web/js/vasm-geometry.js"

assert.equal(displaySide("cameraRight"), "Vehicle left (camera right)")
assert.equal(displaySide("cameraLeft"), "Vehicle right (camera left)")
const phoneRect = { left: 0, top: 0, width: 390, height: 245 }
assert.deepEqual(sourcePoint(0, 0, phoneRect, 1928, 1208), [1928, 0])
assert.deepEqual(sourcePoint(390, 245, phoneRect, 1928, 1208), [0, 1208])
assert.equal(sourcePoint(-1, 100, phoneRect, 1928, 1208), null)
const right = [[100, 200], [320, 200], [320, 400], [100, 400]]
assert.deepEqual(annotationDraft(1344, 760, [], right), {
  version: 1, width: 1344, height: 760, poly_left: [], poly_right: right,
})
assert.equal(annotationDraft(1344, 760, [], []), null)
assert.equal(annotationDraft(1920, 1080, [], right), null)
assert.equal(validPolygon([[100, 100], [300, 300], [100, 300], [300, 100]], 1344, 760), false)
assert.equal(validPolygon(Array.from({ length: 33 }, (_, i) => [i + 10, i + 20]), 1344, 760), false)
assert.equal(validPolygon([[100, 100], [300, 100], [300, 300]], 1344, 760), true)
const canceledDrag = { _dragIndex: 0, _dragSide: "cameraRight", canEdit: false,
  state: { cameraRight: [[100, 200]] }, point: () => [300, 400], redraw() {} }
VasmPage.methods.pointerMove.call(canceledDrag, { clientX: 30, clientY: 40 })
assert.deepEqual(canceledDrag.state.cameraRight, [[100, 200]])
assert.equal(canceledDrag._dragIndex, -1)

const requests = [], states = []
const feed = new SettingsFeed({ publish: (state) => states.push(state),
  fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })),
  later: () => 1, cancelTimer: () => {} })
feed.start("vasm")
requests[0].resolve({ ok: true, status: 200, json: async () => ({ page: "vasm", view: "view1", rows: [] }) })
await new Promise((resolve) => setImmediate(resolve))
feed.preview(3, 0, annotationDraft(1344, 760, [], right))
assert.equal(requests[1].url, "./api/settings/preview")
assert.deepEqual(JSON.parse(requests[1].options.body), {
  view: "view1", row: 3, direction: 0,
  draft: { version: 1, width: 1344, height: 760, poly_left: [], poly_right: right },
})
feed.stop()
requests[1].resolve({ ok: true, status: 200, json: async () => ({ intent: "late", question: "Save?" }) })
await new Promise((resolve) => setImmediate(resolve))
assert.equal(states.at(-1).status, "idle")
assert.equal(states.at(-1).pending, null)

assert.doesNotMatch(VasmPage.template, /type="file"|Camera frame size|Optional local still/)
assert.match(VasmPage.template, /@pointerdown="pointerDown"/)
assert.match(VasmPage.template, /Undo last/)
assert.match(VasmPage.template, /Save Regions/)
assert.match(VasmPage.template, /state\.pending\.question/)
assert.doesNotMatch(readFileSync(new URL("../web/js/vasm.js", import.meta.url), "utf8"), /getVasmSnapshot|FormData|fetch\(/)
const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
assert.match(app, /route\.path === '\/cameras\/vasm'/)

// The side buttons name the vehicle side, with the color of that side's outline.
assert.match(VasmPage.template, /'Vehicle left' : 'Vehicle right'/)
assert.doesNotMatch(VasmPage.template, />\{\{ side === 'cameraRight' \? 'Left' : 'Right' \}\}</)
assert.match(VasmPage.template, /<h2>Blind Spot Warnings<\/h2>/)
// Plain wording: buttons name the vehicle side, so the help no longer talks about camera left/right.
assert.doesNotMatch(VasmPage.template, /vehicle left is camera right/)
assert.match(VasmPage.template, /The image is mirrored, so vehicle left is on the left\./)
assert.doesNotMatch(VasmPage.template, /Cameras &amp; Monitoring/)
// The selected side button is filled and outlined in its side's color.
assert.match(VasmPage.template, /gx-side-btn" :style="\{ '--side': side === 'cameraRight' \? '#5ee5ee' : '#ffb865' \}" :aria-pressed="state\.activeSide === side"/)
