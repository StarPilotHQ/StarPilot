import assert from "node:assert/strict"
import { ModelStatusFeed, ModelManagerFeed, ModelsPage, modelActionAllowed, fitsModelProfile } from "../web/js/models.js"

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fixture() {
  const requests = [], states = [], timers = new Map()
  let now = 0, nextTimer = 0, unauthorized = 0
  const feed = new ModelStatusFeed({ publish: (state) => states.push(state), unauthorized: () => { unauthorized++ },
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, signal: options.signal, resolve })),
    later: (fn, ms) => { timers.set(++nextTimer, { fn, due: now + ms }); return nextTimer },
    cancel: (id) => timers.delete(id) })
  const reply = async (index, body, status = 200) => {
    requests[index].resolve({ ok: status === 200, status, json: async () => body })
    await flush()
  }
  const advance = (ms) => {
    const end = now + ms
    while (true) {
      const due = [...timers.entries()].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0]
      if (!due) break
      now = due[1].due
      timers.delete(due[0])
      due[1].fn()
    }
    now = end
  }
  return { feed, requests, states, timers, reply, advance, get unauthorized() { return unauthorized } }
}

const payload = { schemaVersion: 1, catalog: [{ id: "bundled-current", name: "Bundled driving model", selectable: true }],
  requestedId: "bundled-current", loadedId: "bundled-current", variant: "small", health: "active",
  artifactSha256: "a".repeat(64), fallbackReason: null, pendingNextStart: false }

const normal = fixture()
normal.feed.start()
assert.equal(normal.requests[0].url, "./api/models/status")
await normal.reply(0, payload)
assert.equal(normal.states.at(-1).data.health, "active")
normal.advance(1000)
assert.equal(normal.requests.length, 2)
assert.equal(normal.states.at(-1).data.health, "active")
normal.advance(500) // stalled next request cannot preserve old active status
assert.equal(normal.states.at(-1).status, "stale")
assert.equal(normal.states.at(-1).data, null)
normal.feed.stop()
await normal.reply(1, payload)
assert.equal(normal.states.at(-1).status, "idle")
assert.equal(normal.timers.size, 0)

const late = fixture()
late.feed.start()
late.feed.stop()
late.feed.start()
await late.reply(0, {}, 401)
assert.equal(late.unauthorized, 0)
await late.reply(1, payload)
assert.equal(late.states.at(-1).data.health, "active")

const body = fixture()
body.feed.start()
let finishBody
body.requests[0].resolve({ ok: true, status: 200, json: () => new Promise((resolve) => { finishBody = resolve }) })
await flush()
body.feed.stop()
body.feed.start()
finishBody(payload)
await flush()
assert.equal(body.states.at(-1).data, null)
await body.reply(1, { ...payload, loadedId: null, variant: null, artifactSha256: null, health: "identity-unavailable" })
assert.equal(body.states.at(-1).data.health, "identity-unavailable")

const malformed = fixture()
malformed.feed.start()
await malformed.reply(0, { ...payload, loadedId: null })
assert.equal(malformed.states.at(-1).status, "unavailable")
assert.equal(malformed.states.at(-1).data, null)

const priorDocument = globalThis.document
const listeners = new Map()
globalThis.document = { hidden: false,
  addEventListener: (name, fn) => listeners.set(name, fn),
  removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name) } }
const page = { mode: "local", finishDialog() {}, manager: { start() {}, stop() {} }, feed: { starts: 0, stops: 0, start() { this.starts++ }, stop() { this.stops++ } } }
ModelsPage.mounted.call(page)
assert.equal(page.feed.starts, 1)
document.hidden = true
listeners.get("visibilitychange")()
assert.equal(page.feed.stops, 1)
document.hidden = false
listeners.get("visibilitychange")()
assert.equal(page.feed.starts, 2)
ModelsPage.beforeUnmount.call(page)
assert.equal(page.feed.stops, 2)
assert.equal(listeners.size, 0)
globalThis.document = priorDocument

assert.match(ModelsPage.template, /offline preview/)
assert.match(ModelsPage.template, /Active Small/)
assert.match(ModelsPage.template, /\{\{ bigNoneLabel \}\}/)
// the manager words no Active Big; under Jetlink, the Jetlink app's choice
const noneLabel = (status) => ModelsPage.computed.bigNoneLabel.call({ status })
assert.equal(noneLabel({}), "None — always use Active Small")
assert.equal(noneLabel({ bigNoneLabel: "Jetlink app's choice" }), "Jetlink app's choice")
console.log("Model status client lifecycle checks passed")

const expanded = fixture()
expanded.feed.start()
await expanded.reply(0, { ...payload, catalog: [...payload.catalog, { id: "rdf43", name: "RDF V4", selectable: true }],
  requestedId: "rdf43", pendingNextStart: true, fallbackReason: "selected-load-failed" })
assert.equal(expanded.states.at(-1).status, "ready")
expanded.feed.load()
await expanded.reply(1, { ...payload, loadedId: "unknown" })
assert.equal(expanded.states.at(-1).status, "unavailable")
expanded.feed.stop()

const stalledRuntime = fixture()
stalledRuntime.feed.start()
await stalledRuntime.reply(0, { ...payload, fallbackReason: "chestnut-run-stalled" })
assert.equal(stalledRuntime.states.at(-1).status, "ready")
assert.equal(stalledRuntime.states.at(-1).data.fallbackReason, "chestnut-run-stalled")
stalledRuntime.feed.load()
await stalledRuntime.reply(1, { ...payload, fallbackReason: "invented-reason" })
assert.equal(stalledRuntime.states.at(-1).status, "unavailable")
stalledRuntime.feed.stop()

const small = { value: "small", label: "Small", requiresGpu: false, installed: true, selectable: true, userFavorite: false }
const big = { value: "big", label: "Big", requiresGpu: true, installed: true, selectable: true, userFavorite: true }
const pending = { value: "pending", label: "Pending rebuild", requiresGpu: false, installed: false, selectable: false, downloadAvailable: false }
const missing = { value: "missing", label: "Missing", requiresGpu: true, installed: false, selectable: false, downloadAvailable: true }
const managerPayload = { schemaVersion: 1, models: [small, big, pending, missing], isOnroad: false,
  currentModel: "small", activeSmallModel: "small", activeBigModel: "big", downloading: false,
  capabilities: { select: true, favorites: true, download: true, downloadAll: true, cancel: true, delete: true, refresh: true } }
assert.equal(modelActionAllowed(managerPayload, "select-small", small), true)
assert.equal(modelActionAllowed(managerPayload, "select-big", small), false)
assert.equal(modelActionAllowed(managerPayload, "select-small", { ...small, selectable: false }), false)
assert.equal(modelActionAllowed(managerPayload, "select-big"), true)
// what the manager marks selectable, downloaded or not: under Jetlink, a big model with an ONNX
assert.equal(modelActionAllowed(managerPayload, "select-big", { ...missing, selectable: true }), true)
assert.equal(modelActionAllowed(managerPayload, "select-big", { ...big, selectable: false }), false)
assert.equal(modelActionAllowed(managerPayload, "download", pending), false)
assert.equal(modelActionAllowed(managerPayload, "download", missing), true)
assert.equal(modelActionAllowed(managerPayload, "delete", big), false)
assert.equal(modelActionAllowed({ ...managerPayload, activeBigModel: "" }, "delete", big), true)
assert.equal(modelActionAllowed({ ...managerPayload, isOnroad: true }, "favorite", small), true)
assert.equal(modelActionAllowed({ ...managerPayload, isOnroad: true }, "select-small", small), true)
assert.equal(modelActionAllowed({ ...managerPayload, isOnroad: true }, "select-big", big), true)
assert.equal(modelActionAllowed({ ...managerPayload, isOnroad: true, jetlinkBig: true }, "select-big", big), false)
assert.equal(modelActionAllowed({ ...managerPayload, isOnroad: true }, "download", missing), false)
assert.equal(modelActionAllowed({ ...managerPayload, isOnroad: true, downloading: true }, "cancel"), true)
assert.equal(modelActionAllowed({ ...managerPayload, capabilities: {} }, "favorite", small), false)
assert.equal(fitsModelProfile({ ...small, profiles: ["small", "big"] }, "big"), true)

function managerFixture() {
  const requests = [], updates = [], timers = new Map()
  let timer = 0, unauthorized = 0
  const manager = new ModelManagerFeed({ publish: update => updates.push(update), unauthorized: () => unauthorized++,
    fetcher: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),
    later: (fn, ms) => { timers.set(++timer, { fn, ms }); return timer }, cancel: id => timers.delete(id) })
  const reply = async (i, body, status = 200) => { requests[i].resolve({ ok: status === 200, status, json: async () => body }); await flush() }
  return { manager, requests, updates, timers, reply, get unauthorized() { return unauthorized } }
}
const management = managerFixture()
management.manager.start()
await management.reply(0, managerPayload)
assert.equal(management.requests[0].url, "./api/models/manager")
management.manager.load()
const selection = management.manager.action("select-big")
assert.equal(management.requests[1].options.signal.aborted, true)
assert.deepEqual(JSON.parse(management.requests[2].options.body), { profile: "big", model: "" })
assert.equal(management.requests[2].options.method, "POST")
assert.equal(management.requests[2].url, "./api/models/active")
await management.reply(1, { ...managerPayload, activeBigModel: "old" })
assert.notEqual(management.manager.data?.activeBigModel, "old")
await management.reply(2, { message: "Selected" })
await management.reply(3, { ...managerPayload, activeBigModel: "" })
await selection
assert.equal(management.manager.data.activeBigModel, "")
const favorite = management.manager.action("favorite", small)
assert.deepEqual(JSON.parse(management.requests[4].options.body), { userFavorites: ["big", "small"] })
await management.reply(4, { error: "Favorites were not saved" }, 409)
await management.reply(5, managerPayload)
await favorite
assert.equal(management.updates.at(-1).error, "Favorites were not saved")
management.manager.data = { ...managerPayload, models: managerPayload.models.map(m => m.value === "missing" ? { ...m, downloadAvailable: false } : m) }
assert.equal(await management.manager.action("download", missing), null)
assert.equal(management.requests.length, 6)
management.manager.data = managerPayload
const download = management.manager.action("download", missing, { allowGpuWithoutGpu: true })
assert.deepEqual(JSON.parse(management.requests[6].options.body), { model: "missing", allowGpuWithoutGpu: true })
management.manager.stop()
await management.reply(6, {}, 401)
await download
assert.equal(management.unauthorized, 0)
assert.equal(management.timers.size, 0)

const access = managerFixture()
access.manager.start()
await access.reply(0, { code: "access_unavailable" }, 503)
assert.equal(access.unauthorized, 1)
assert.equal(access.timers.size, 0)

const expiry = managerFixture()
expiry.manager.start()
await expiry.reply(0, managerPayload)
const timedAction = expiry.manager.action("select-small", small)
;[...expiry.timers.values()].find(t => t.ms === 10000).fn()
assert.equal(expiry.manager.data, null)
assert.match(expiry.updates.at(-1).error, /result is unknown/)
await expiry.reply(1, { message: "late success" })
await flush()
if (expiry.requests[2]) await expiry.reply(2, managerPayload)
await timedAction
expiry.manager.stop()
console.log("Model manager guarded actions and source retirement checks passed")

const randomPayload = { ...managerPayload, randomizer: true, blacklistedModels: ['big'],
  capabilities: { ...managerPayload.capabilities, randomizer: true, exclusions: true } }
assert.equal(modelActionAllowed(randomPayload, 'select-small', small), false)
assert.equal(modelActionAllowed(randomPayload, 'select-big'), false)
assert.equal(modelActionAllowed(randomPayload, 'disable-randomizer'), true)
assert.equal(modelActionAllowed({ ...randomPayload, isOnroad: true }, 'exclude', small), false)
assert.equal(modelActionAllowed({ ...randomPayload, isOnroad: true }, 'enable-randomizer'), false)
assert.equal(modelActionAllowed({ ...randomPayload, isOnroad: true }, 'favorite', small), true)
assert.equal(modelActionAllowed(randomPayload, 'exclude', pending), true)
assert.match(ModelsPage.template, /Model Randomizer/)
assert.match(ModelsPage.template, /Include in Randomizer/)
assert.match(ModelsPage.template, /An external GPU is not detected/)
const randomManagement = managerFixture()
randomManagement.manager.start()
await randomManagement.reply(0, randomPayload)
const excluded = randomManagement.manager.action('exclude', small)
assert.equal(randomManagement.requests[1].url, './api/models/preferences')
assert.deepEqual(JSON.parse(randomManagement.requests[1].options.body), { blacklistedModels: ['big', 'small'] })
await randomManagement.reply(1, { message: 'Saved' })
await randomManagement.reply(2, { ...randomPayload, blacklistedModels: ['big', 'small'] })
await excluded
const disabledRandomizer = randomManagement.manager.action('disable-randomizer')
assert.deepEqual(JSON.parse(randomManagement.requests[3].options.body), { randomizer: false })
await randomManagement.reply(3, { message: 'Saved' })
await randomManagement.reply(4, { ...randomPayload, randomizer: false })
await disabledRandomizer
randomManagement.manager.stop()
console.log('Randomizer and exclusion interactions passed')

assert.match(ModelsPage.template, /Selections apply next onroad cycle/)
assert.doesNotMatch(ModelsPage.template, /Onroad: switching/)
assert.equal((ModelsPage.template.match(/class="gx-model-control"/g) || []).length, 2)
assert.equal(ModelsPage.data().hardwareFilter, 'both')
const hardware = { hardwareFilter: 'both', setHardwareFilter(value) { this.hardwareFilter = value } }
ModelsPage.methods.toggleHardware.call(hardware, 'gpu', false)
assert.equal(hardware.hardwareFilter, 'comma')
ModelsPage.methods.toggleHardware.call(hardware, 'comma', false)
assert.equal(hardware.hardwareFilter, 'none')
ModelsPage.methods.toggleHardware.call(hardware, 'gpu', true)
assert.equal(hardware.hardwareFilter, 'gpu')
ModelsPage.methods.toggleHardware.call(hardware, 'comma', true)
assert.equal(hardware.hardwareFilter, 'both')
const randomSwitch = { target: { checked: true } }
let randomAction = ''
ModelsPage.methods.toggleRandomizer.call({ status: { randomizer: false }, runAction(action) { randomAction = action } }, randomSwitch)
assert.equal(randomAction, 'enable-randomizer')
assert.equal(randomSwitch.target.checked, false)
const chargeSwitch = { target: { checked: true } }
let chargeChoice
ModelsPage.methods.toggleJetlinkCharge.call({ status: { jetlink: { mode: 'ios', chargePhone: false } },
  configureJetlink(mode, enabled) { chargeChoice = [mode, enabled] } }, chargeSwitch)
assert.deepEqual(chargeChoice, ['ios', true])
assert.equal(chargeSwitch.target.checked, false)


const feedbackPage = { ...ModelsPage.data(), unauthorized() {}, canAction() { return true } }
ModelsPage.created.call(feedbackPage)
const publishManager = feedbackPage.manager.publish
feedbackPage.manager.action = async () => {
  publishManager({ loading: false, error: '', data: { ...managerPayload, downloading: true, progress: 'Downloading 20%' } })
  return { message: 'Starting download' }
}
await ModelsPage.methods.runAction.call(feedbackPage, 'download', { ...missing, requiresGpu: false })
assert.equal(feedbackPage.message, 'Downloading 20%')
assert.equal(feedbackPage.trackingProgress, true)
publishManager({ loading: false, error: '', data: { ...managerPayload, downloading: false, progress: 'Downloaded!',
  summary: { installed: 2, missing: 98, total: 100 } } })
assert.equal(feedbackPage.message, 'Downloaded!')
assert.equal(feedbackPage.trackingProgress, false)
assert.equal(feedbackPage.summary.installed, 2)
console.log('Narrow model controls and completed operation feedback checks passed')

// Connection settings share the parked/authenticated action path, not catalog selection.
const jetlinkView = { isOnroad: false, capabilities: { jetlink: true }, jetlink: { mode: "off" } }
assert.equal(modelActionAllowed(jetlinkView, "jetlink"), true)
assert.equal(modelActionAllowed({ ...jetlinkView, isOnroad: true }, "jetlink"), false)
assert.equal(modelActionAllowed({ ...jetlinkView, capabilities: {} }, "jetlink"), false)
assert.match(ModelsPage.template, /Prepared means the remote model is built, not active/)
const linkManagement = managerFixture()
linkManagement.manager.start()
await linkManagement.reply(0, { ...managerPayload, ...jetlinkView })
const linkSelection = linkManagement.manager.action("jetlink", null, { mode: "ios", chargePhone: false })
assert.equal(linkManagement.requests[1].url, "./api/models/jetlink")
assert.deepEqual(JSON.parse(linkManagement.requests[1].options.body), { mode: "ios", chargePhone: false })
await linkManagement.reply(1, { message: "Saved" })
await linkManagement.reply(2, { ...managerPayload, ...jetlinkView, jetlink: { mode: "ios" } })
await linkSelection
assert.equal(linkManagement.manager.data.jetlink.mode, "ios")
linkManagement.manager.stop()
const liveLink = managerFixture()
liveLink.manager.start()
await liveLink.reply(0, { ...managerPayload, ...jetlinkView, jetlink: { mode: "usb", active: true, state: "active" } })
const executionExpiry = [...liveLink.timers.values()].find(timer => timer.ms === 1500)
assert.ok(executionExpiry)
executionExpiry.fn()
assert.equal(liveLink.manager.data.jetlink.active, false)
assert.equal(liveLink.manager.data.jetlink.state, "checking execution")
liveLink.manager.stop()

assert.ok(ModelsPage.template.includes('href="https://github.com/StarPilotHQ/StarPilot/blob/SecretGoodStarPilot/openpilot/starpilot/models/JETLINK.md"'), "Jetlink setup guide points at the branch that contains it")

const refreshingCatalog = { ...managerPayload, downloading: true, refreshing: true }
assert.equal(modelActionAllowed(refreshingCatalog, "select-small", small), true)
assert.equal(modelActionAllowed(refreshingCatalog, "select-big", big), true)
assert.equal(modelActionAllowed(refreshingCatalog, "select-big", missing), false)
assert.equal(modelActionAllowed({ ...refreshingCatalog, isOnroad: true }, "select-big", big), true)
assert.equal(modelActionAllowed({ ...refreshingCatalog, randomizer: true }, "select-big", big), false)
assert.equal(modelActionAllowed({ ...refreshingCatalog, refreshing: false }, "select-big", big), false)
assert.equal(modelActionAllowed(refreshingCatalog, "delete", small), false)

for (const series of ["Polaris", "Polaris Series", "  POLARIS SERIES  "]) {
  for (const value of ["spz_polaris_r3a", "future-model", undefined]) assert.equal(ModelsPage.methods.isPolaris({value, series}), true)
}
for (const series of ["OP Series", "SP Series", "Polaris Experimental", "", undefined, null, 123]) {
  assert.equal(ModelsPage.methods.isPolaris({value: "unrelated", label: "Polaris", series}), false)
}
for (const value of ["spz_polaris_r3a", "spz_polaris_r3b"]) assert.equal(ModelsPage.methods.isPolaris({value}), true)
assert.equal(ModelsPage.methods.isPolaris({value: "spz_polaris_r3a_copy"}), false)
assert.equal(ModelsPage.methods.isPolaris(null), false)
assert.match(ModelsPage.template, /gx-model-card--polaris.*isPolaris\(m\)/)
assert.match(ModelsPage.template, /v-if="isPolaris\(m\)" class="gx-chip gx-polaris-badge"/)
assert.match(ModelsPage.template, /aria-label="About Polaris models"/)

for (const label of ["Polaris R3 A ⚠️🧪", "Polaris R3 B ⚠️🧪", "Polaris R3 A", "Polaris R3 B 🚀"]) {
  const [text, emojis] = ModelsPage.methods.polarisTitleParts({label})
  assert.equal(text + emojis, label)
  assert.equal(/\p{Extended_Pictographic}/u.test(text), false)
}
assert.deepEqual(ModelsPage.methods.polarisTitleParts({value: "spz_polaris_r3a"}), ["spz_polaris_r3a", ""])

