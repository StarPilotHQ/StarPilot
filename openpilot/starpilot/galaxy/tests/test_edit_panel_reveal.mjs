import assert from "node:assert/strict"
import { OnroadLayoutPage } from "../web/js/onroad-layout.js"
import { ONROAD_LAYOUT_TEMPLATE } from "../web/js/onroad-layout-template.js"

const { openEditPanel, revealEditPanel, selectWidget } = OnroadLayoutPage.methods
const scrolls = []
let scrolled = 0
globalThis.window = { scrollBy: (x, y) => { scrolls.push(y); scrolled += y } }
globalThis.getComputedStyle = (element) => ({ position: element.position })
const realTimeout = globalThis.setTimeout
globalThis.setTimeout = (fn) => fn()

function editor(panelTop, position = "sticky") {
  scrolled = 0
  return {
    state: { inspectorPanel: "widgets", selected: null, touchArmed: null },
    $refs: { sections: { position, getBoundingClientRect: () => ({ bottom: 100 }) },
             editPanel: { getBoundingClientRect: () => ({ top: panelTop - scrolled }) } },
    $nextTick: (fn) => fn(), openEditPanel, revealEditPanel,
  }
}

for (const [top, expected] of [[40, -68], [500, 392], [108, null]]) {
  scrolls.length = 0
  const page = editor(top)
  openEditPanel.call(page)
  assert.equal(page.state.inspectorPanel, "edit")
  assert.deepEqual(scrolls, expected == null ? [] : [expected], `edit options at ${top}px land under the tabs`)
}

scrolls.length = 0
const picked = editor(700)
selectWidget.call(picked, "current_speed")
assert.equal(picked.state.selected, "current_speed")
assert.deepEqual(scrolls, [592])

scrolls.length = 0
openEditPanel.call(editor(700, "static"))
assert.deepEqual(scrolls, [], "wide screens without pinned tabs never scroll")

const unselected = editor(108)
unselected.activeWidgets = [{ id: "current_speed" }]
openEditPanel.call(unselected)
assert.equal(unselected.state.selected, "current_speed", "Edit restores a widget after deselection")

let delayedAlign
globalThis.setTimeout = (fn) => { delayedAlign = fn }
const switched = editor(500)
openEditPanel.call(switched)
switched.state.inspectorPanel = "widgets"
scrolled = 0
scrolls.length = 0
delayedAlign()
assert.deepEqual(scrolls, [], "a delayed alignment leaves the Widgets scroll position alone")

assert.match(ONROAD_LAYOUT_TEMPLATE, /@click="state\.inspectorPanel === 'lead' \? revealEditPanel\(\) : openEditPanel\(\)"><span>Edit/)
globalThis.setTimeout = realTimeout
