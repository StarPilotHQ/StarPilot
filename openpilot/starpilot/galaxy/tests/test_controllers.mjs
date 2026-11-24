import assert from "node:assert/strict"
import { BluetoothPage } from "../web/js/bluetooth.js"
import { ControllersFeed, ControllersPage, validControllersStatus } from "../web/js/controllers.js"

const status = () => ({ version: 1, available: true, editable: true, enabled: false, valid: true,
  revision: "a".repeat(64), devices: [{ id: "hardware-1", name: "Steering remote", bus: 3 }],
  slots: Array.from({ length: 13 }, (_, index) => ({ index, label: index < 3 ? `Quick Select ${index + 1}` : `Button ${index - 2}`,
    key: null, available: index < 3 })),
  options: [{ key: "brightness", label: "Brightness", section: "Display" }], bindings: [],
  learning: null, testing: false, lastPress: null })
assert.equal(validControllersStatus(status()), true)
assert.equal(validControllersStatus({ ...status(), devices: [{ id: "bad", name: "Remote", bus: 9 }] }), false)
assert.equal(validControllersStatus({ ...status(), slots: status().slots.slice(1) }), false)
assert.equal(validControllersStatus({ ...status(), options: [{ key: "x\n", label: "Bad", section: "Display" }] }), false)
assert.equal(validControllersStatus({ ...status(), lastPress: { deviceId: "hardware-1", code: 65551,
  slot: null, executed: false, message: "Test press detected" } }), true)
assert.doesNotMatch(BluetoothPage.template, /<ControllersPage/)
assert.match(BluetoothPage.template, /<BluetoothTabs/)
assert.match(ControllersPage.template, /href="#\/bluetooth"/)
assert.match(ControllersPage.template, /Test buttons \(20s\)/)
assert.match(ControllersPage.template, /Press a button for/)
assert.match(ControllersPage.template, /class="gx-switch"/)
assert.match(ControllersPage.template, /class="gx-switch__track"/)
assert.match(ControllersPage.template, /slot\.key \? 'Available when its control is ready' : 'Not assigned'/)
assert.match(ControllersPage.template, /gx-controllers__selector/)

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fixture() {
  const requests = [], updates = [], timers = new Map(); let next = 0, unauthorized = 0
  const feed = new ControllersFeed({ publish: (value) => updates.push(value), unauthorized: () => unauthorized++,
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })),
    later: (fn, ms) => { timers.set(++next, { fn, ms }); return next }, cancelTimer: (id) => timers.delete(id) })
  async function reply(index, data = status(), code = 200) {
    requests[index].resolve({ ok: code === 200, status: code, json: async () => data }); await flush()
  }
  function fire(ms) {
    const item = [...timers.entries()].find(([, value]) => value.ms === ms)
    assert.ok(item)
    timers.delete(item[0]); item[1].fn()
  }
  return { feed, requests, updates, timers, reply, fire, get unauthorized() { return unauthorized } }
}

const normal = fixture()
normal.feed.start(); await normal.reply(0)
assert.equal(normal.requests[0].url, "./api/controllers/status")
assert.equal(normal.requests[0].options.credentials, "same-origin")
normal.fire(2000); await normal.reply(1)
assert.equal(normal.updates.at(-1).busy, false)
const saved = normal.feed.action({ operation: "save", revision: "a".repeat(64), enabled: true,
  slots: ["brightness", null, null, null, null, null, null, null, null, null] })
assert.equal(normal.requests[2].url, "./api/controllers/action")
assert.equal(normal.requests[2].options.method, "POST")
assert.deepEqual(JSON.parse(normal.requests[2].options.body), { operation: "save", revision: "a".repeat(64), enabled: true,
  slots: ["brightness", null, null, null, null, null, null, null, null, null] })
await normal.reply(2); await saved
assert.deepEqual(normal.updates.at(-1).draft, { revision: status().revision, enabled: false, slots: Array(10).fill(null) })
assert.equal(normal.updates.at(-1).busy, false)

const racing = fixture()
racing.feed.start(); await racing.reply(0)
racing.fire(2000)
assert.equal(racing.updates.at(-1).busy, false, "background status reads do not lock drafts")
const racingSave = racing.feed.action({ operation: "save", enabled: true })
assert.equal(racing.requests[1].options.signal.aborted, true)
assert.equal(racing.requests[2].url, "./api/controllers/action")
assert.equal(racing.updates.at(-1).busy, true)
await racing.reply(2, { ...status(), enabled: true })
await racingSave
await racing.reply(1)
assert.equal(racing.updates.at(-1).status.enabled, true, "a late poll cannot undo a saved draft")

const learning = fixture()
learning.feed.start(); await learning.reply(0, { ...status(), learning: { slot: 4, expiresIn: 20 } })
learning.feed.stop()
assert.equal(learning.requests[1].url, "./api/controllers/action")
assert.deepEqual(JSON.parse(learning.requests[1].options.body), { operation: "cancel" })
assert.equal(learning.requests[1].options.keepalive, true)
assert.equal(learning.updates.at(-1).status, null)

const testMode = fixture()
testMode.feed.start(); await testMode.reply(0, { ...status(), testing: true })
testMode.feed.stop()
assert.deepEqual(JSON.parse(testMode.requests[1].options.body), { operation: "cancel" })

const stale = fixture()
stale.feed.start(); stale.feed.stop(); await stale.reply(0)
assert.equal(stale.updates.at(-1).status, null)
const denied = fixture()
denied.feed.start(); await denied.reply(0, {}, 401)
assert.equal(denied.unauthorized, 1)
const parked = fixture()
parked.feed.start(); await parked.reply(0, status())
parked.feed.action({ operation: "learn", revision: "a".repeat(64), slot: 1 }); await parked.reply(1, {}, 403)
assert.match(parked.updates.at(-1).error, /Turn off the vehicle/)

const component = ControllersPage.setup({ unauthorized() {} })
component.feed.publish({ status: status(), busy: false, error: "" })
assert.deepEqual(component.state.draft.slots, Array(10).fill(null))
component.state.draft.slots[0] = "brightness"
component.feed.publish({ status: { ...status(), revision: "b".repeat(64) }, busy: false, error: "" })
assert.equal(component.state.draft.revision, "a".repeat(64)) // A polling update cannot replace unsaved edits.
const page = { ...component, get canEdit() { return ControllersPage.computed.canEdit.call(this) },
  get editable() { return ControllersPage.computed.editable.call(this) },
  get dirty() { return ControllersPage.computed.dirty.call(this) },
  get changed() { return ControllersPage.computed.changed.call(this) } }
let calls = 0
component.feed.action = async () => { calls++; return status() }
ControllersPage.methods.learn.call(page, 4)
ControllersPage.methods.remove.call(page, { deviceId: "hardware-1", code: 1 })
assert.equal(calls, 0)
assert.match(ControllersPage.template, /Save these changes before learning or removing buttons/)

let finishReload
component.feed.refresh = (replaceDraft) => {
  assert.equal(replaceDraft, true)
  return new Promise((resolve) => { finishReload = resolve })
}
const reloading = ControllersPage.methods.reload.call(page)
assert.equal(component.state.draft.slots[0], "brightness") // The template still has a draft while reloading.
const afterReload = { ...status(), revision: "c".repeat(64) }
component.feed.publish({ status: afterReload, busy: false, error: "",
    draft: { revision: afterReload.revision, enabled: afterReload.enabled, slots: afterReload.slots.slice(3).map(slot => slot.key) } })
  assert.equal(page.changed, false, "confirmed status and draft publish together")
finishReload(afterReload)
await reloading
assert.equal(component.state.draft.revision, "c".repeat(64))
assert.equal(component.state.draft.slots[0], null)

component.state.draft.enabled = true
const afterSave = { ...status(), revision: "d".repeat(64), enabled: true }
component.feed.action = async (payload) => {
  assert.equal(payload.operation, "save")
  component.feed.publish({ status: afterSave, busy: false, error: "",
    draft: { revision: afterSave.revision, enabled: afterSave.enabled, slots: afterSave.slots.slice(3).map(slot => slot.key) } })
  assert.equal(page.changed, false, "confirmed status and draft publish together")
  return afterSave
}
await ControllersPage.methods.save.call(page)
assert.equal(page.changed, false)
assert.equal(page.dirty, false)
assert.equal(component.state.draft.revision, afterSave.revision)
component.feed.stop()
assert.equal(component.state.draft, null)

const earlierDocument = globalThis.document
const earlierWindow = globalThis.window
globalThis.window = new EventTarget()
const listeners = new Map(), lifecycle = []
globalThis.document = { hidden: false, addEventListener(name, fn) { listeners.set(name, fn) },
  removeEventListener(name) { listeners.delete(name) } }
const mounted = { mode: "local", feed: { start() { lifecycle.push("start") }, stop() { lifecycle.push("stop") } } }
ControllersPage.mounted.call(mounted)
assert.deepEqual(lifecycle, ["start"])
globalThis.document.hidden = true
listeners.get("visibilitychange")()
mounted.mode = "remote"
globalThis.document.hidden = false
ControllersPage.watch.mode.call(mounted)
ControllersPage.beforeUnmount.call(mounted)
assert.deepEqual(lifecycle, ["start", "stop", "stop", "stop"])
assert.equal(listeners.size, 0)
globalThis.document = earlierDocument
globalThis.window = earlierWindow
console.log("Controller buttons: source status validation, draft preservation, polling, auth, parked denial and capture cleanup passed")
