import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { SettingsPage, SettingsFeed } from "../web/js/settings.js"
import { GalaxySettingRow, settingControl } from "../web/js/galaxy-setting-row.js"
import { VehicleControlsPage, VehicleSelectionFeed, validVehiclePage } from "../web/js/vehicle-controls.js"

const page = { version: 1, parked: true, readable: true, valid: true, selected: null, selectedLabel: "Auto detection",
  reported: null, view: "source-view", choices: [{ platform: "KIA_CEED", make: "Kia", label: "Ceed" }] }
assert.ok(validVehiclePage(page))
assert.ok(!validVehiclePage({ ...page, choices: [{ ...page.choices[0], platform: "MOCK" }] }))
assert.ok(!validVehiclePage({ ...page, selected: "FAKE" }))

const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body })
function fixture() {
  const requests = [], states = [], timers = new Map()
  let nextTimer = 0, unauthorized = 0
  const feed = new VehicleSelectionFeed({ publish: (state) => states.push(state), unauthorized: () => { unauthorized++ },
    later: (fn, ms) => { assert.ok([2000, 8000].includes(ms)); const id = ++nextTimer; timers.set(id, { fn, ms }); return id },
    cancelTimer: (id) => timers.delete(id),
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })) })
  async function reply(index, body, status = 200) {
    requests[index].resolve(response(body, status))
    for (let i = 0; i < 12; i++) await Promise.resolve()
  }
  function fire(ms) {
    const entry = [...timers.entries()].find(([, item]) => item.ms === ms)
    assert.ok(entry, `missing ${ms} ms timer`)
    timers.delete(entry[0]); entry[1].fn()
  }
  return { feed, requests, states, timers, reply, fire, get unauthorized() { return unauthorized } }
}

const flow = fixture()
flow.feed.start()
assert.equal(flow.requests[0].url, "./api/vehicle-selection")
await flow.reply(0, page)
assert.equal(flow.feed.data.selectedLabel, "Auto detection")
flow.feed.preview("KIA_CEED")
assert.deepEqual(JSON.parse(flow.requests[1].options.body), { view: "source-view", platform: "KIA_CEED" })
await flow.reply(1, { intent: "once", question: "Save Ceed for the next start?" })
assert.equal(flow.feed.pending.intent, "once")
flow.feed.confirm()
assert.deepEqual(JSON.parse(flow.requests[2].options.body), { intent: "once", confirmed: true })
await flow.reply(2, { saved: true })
assert.equal(flow.requests[3].url, "./api/vehicle-selection")
await flow.reply(3, { ...page, selected: "KIA_CEED", selectedLabel: "Ceed", view: "new-source" })
assert.equal(flow.feed.data.selected, "KIA_CEED")
assert.equal(flow.feed.pending, null)

const stale = fixture()
stale.feed.start()
await stale.reply(0, page)
stale.feed.preview("KIA_CEED")
await stale.reply(1, { error: "Changed" }, 409)
assert.equal(stale.feed.data, null)
assert.equal(stale.states.at(-1).status, "unavailable")
assert.match(stale.states.at(-1).error, /Refresh/)
stale.feed.refresh()
await stale.reply(2, page)
assert.equal(stale.feed.data.view, "source-view")

const uncertain = fixture()
uncertain.feed.start()
await uncertain.reply(0, page)
uncertain.feed.preview("KIA_CEED")
await uncertain.reply(1, { error: "Unverified", code: "unverified" }, 409)
assert.match(uncertain.states.at(-1).error, /could not be confirmed/)
assert.equal(uncertain.feed.data, null)

const repair = fixture()
repair.feed.start()
await repair.reply(0, { ...page, valid: false, selectedLabel: "Needs review" })
await repair.feed.preview("KIA_CEED")
assert.equal(repair.requests.length, 1)
repair.feed.preview(null)
assert.equal(repair.requests.length, 2)
repair.feed.stop()
await repair.reply(1, { intent: "late", question: "Save Auto?" })
assert.equal(repair.feed.pending, null)

const revoked = fixture()
revoked.feed.start()
await revoked.reply(0, { code: "setup_required" }, 503)
assert.equal(revoked.unauthorized, 1)
assert.equal(revoked.feed.data, null)

// A page opened before fresh parked evidence arrives must recover on its own.
const awaitingParked = fixture()
awaitingParked.feed.start()
await awaitingParked.reply(0, { ...page, parked: false })
assert.equal(awaitingParked.feed.data.parked, false)
assert.equal(awaitingParked.timers.size, 1)
await awaitingParked.feed.preview("KIA_CEED")
assert.equal(awaitingParked.requests.length, 1)
awaitingParked.fire(2000)
assert.equal(awaitingParked.requests[1].url, "./api/vehicle-selection")
assert.equal(awaitingParked.states.at(-1).status, "ready") // Keep the visible catalog steady while rechecking.
await awaitingParked.reply(1, { ...page, parked: true, view: "parked-view" })
assert.equal(awaitingParked.feed.data.parked, true)
assert.equal(awaitingParked.timers.size, 1)
awaitingParked.feed.preview("KIA_CEED")
assert.deepEqual(JSON.parse(awaitingParked.requests[2].options.body), { view: "parked-view", platform: "KIA_CEED" })
awaitingParked.feed.stop()
await awaitingParked.reply(2, { intent: "late", question: "Save?" })
assert.equal(awaitingParked.feed.pending, null)

const leaveWhileParkedUnknown = fixture()
leaveWhileParkedUnknown.feed.start()
await leaveWhileParkedUnknown.reply(0, { ...page, parked: false })
leaveWhileParkedUnknown.feed.stop()
assert.equal(leaveWhileParkedUnknown.timers.size, 0)
assert.equal(leaveWhileParkedUnknown.requests.length, 1)

const staleParked = fixture()
staleParked.feed.start()
staleParked.feed.stop()
await staleParked.reply(0, { ...page, parked: false })
assert.equal(staleParked.feed.data, null)
assert.equal(staleParked.timers.size, 0)

assert.match(VehicleControlsPage.template, /Configured selection mode/)
assert.match(VehicleControlsPage.template, /Manually Selected/)
assert.match(VehicleControlsPage.template, /Last auto-detected vehicle/)
assert.match(VehicleControlsPage.template, /This runtime used a manual selection, not automatic detection/)
assert.match(VehicleControlsPage.template, /aria-label="Auto Detection"/)
assert.match(VehicleControlsPage.template, /:disabled="autoDetection \|\| !state\.data\.parked \|\| !state\.data\.readable"/)
const autoState = { state: { data: { valid: true, selected: null }, manualSelectionOpen: false }, choose: () => { throw Error("Unexpected save") } }
Object.defineProperty(autoState, "autoDetection", { get: () => VehicleControlsPage.computed.autoDetection.call(autoState) })
VehicleControlsPage.methods.changeAutoDetection.call(autoState, { target: { checked: false } })
assert.equal(autoState.state.manualSelectionOpen, true)
assert.equal(autoState.autoDetection, false)
VehicleControlsPage.methods.changeAutoDetection.call(autoState, { target: { checked: true } })
assert.equal(autoState.autoDetection, true)
autoState.state.data.selected = "KIA_CEED"
autoState.choose = platform => { assert.equal(platform, null) }
VehicleControlsPage.methods.changeAutoDetection.call(autoState, { target: { checked: true } })
assert.match(VehicleControlsPage.template, /next start/)
assert.match(VehicleControlsPage.template, /state\.pending\.question/)
const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
assert.match(app, /route\.path === '\/settings\/vehicle'/)


// Vehicle settings use the existing switch and exact-view automatic save gateway.
assert.equal(VehicleControlsPage.components.SettingsPage, SettingsPage)
assert.match(VehicleControlsPage.template, /initial-page="vehicle"/)
const toggle = { label: "Automatic Brake Hold", choices: ["Off", "On"], value: "Off", available: true, action: true }
assert.equal(settingControl(toggle), "switch")
assert.equal(GalaxySettingRow.computed.dimmed.call({ disabled: false, row: { ...toggle, available: false } }), true)
assert.equal(GalaxySettingRow.computed.locked.call({ busy: false, updating: false, dimmed: true }), true)
const settingRequests = [], settingStates = []
const settings = new SettingsFeed({ publish: (state) => settingStates.push(state), later: () => 1, cancelTimer: () => {},
  fetcher: (url, options) => new Promise((resolve) => settingRequests.push({ url, options, resolve })) })
async function settingReply(index, result, status = 200) {
  settingRequests[index].resolve(response(result, status))
  for (let i = 0; i < 18; i++) await Promise.resolve()
}
settings.start("vehicle")
assert.equal(settingRequests[0].url, "./api/settings/pages/vehicle")
await settingReply(0, { page: "vehicle", view: "vehicle-source", rows: [toggle], parked: true })
settings.previewValue(0, "On")
assert.deepEqual(JSON.parse(settingRequests[1].options.body), { view: "vehicle-source", row: 0, value: "On" })
await settingReply(1, { intent: "hold-on", question: "Save automatic brake hold as On for the next startup?" })
assert.equal(settingRequests[2].url, "./api/settings/confirm")
assert.deepEqual(JSON.parse(settingRequests[2].options.body), { intent: "hold-on", confirmed: true, page: "vehicle" })
await settingReply(2, { saved: true })
assert.equal(settingRequests[3].url, "./api/settings/pages/vehicle")
await settingReply(3, { page: "vehicle", view: "fresh-vehicle", rows: [{ ...toggle, value: "On" }], parked: true })
assert.equal(settings.data.rows[0].value, "On")
settings.previewValue(0, "Off")
await settingReply(4, { code: "changed" }, 409)
assert.equal(settings.data, null)
assert.equal(settingStates.at(-1).status, "unavailable")
assert.equal(settingRequests.length, 5)
settings.stop()

// Alpha speed-control choices share the existing vehicle SettingsPage gateway.
assert.match(VehicleControlsPage.template, /initial-page="vehicle"/)
const alpha = { ...toggle, label: "Alpha Longitudinal" }
assert.equal(settingControl(alpha), "switch")
assert.equal(settingControl({ ...alpha, value: "On" }), "switch")

const { SETTINGS_SECTIONS } = await import("../web/js/settings.js")
assert.equal(SETTINGS_SECTIONS.filter(section => section.id === "vehicle").length, 1)
assert.ok(!SETTINGS_SECTIONS.find(section => section.id === "lateral").pages.includes("vehicle"))
assert.equal(await SettingsPage.components.VehicleControlsPage.__asyncLoader(), VehicleControlsPage)
const catalog = JSON.parse(readFileSync(new URL('../web/data/catalog.json', import.meta.url), 'utf8'))
assert.ok(!catalog.tools.some(tool => tool.path === "/vehicle"))
const priorLocation = globalThis.location
globalThis.location = { hash: "#/vehicle" }
const { route } = await import("../web/js/router.js?vehicle-tab-test")
assert.equal(route.path, "/settings/vehicle")
globalThis.location = priorLocation
let leftVehicle = 0
const leave = () => leftVehicle++
VehicleControlsPage.methods.requestRouteLeave.call({ busy: true, $refs: {} }, leave)
assert.equal(leftVehicle, 0)
VehicleControlsPage.methods.requestRouteLeave.call({ busy: false, $refs: { vehicleSettings: { requestRouteLeave: proceed => proceed() } } }, leave)
assert.equal(leftVehicle, 1)
let cancelled = 0
assert.equal(VehicleControlsPage.methods.navigateBack.call({ state: { pending: {} }, feed: { cancel: () => cancelled++ } }), true)
assert.equal(cancelled, 1)
