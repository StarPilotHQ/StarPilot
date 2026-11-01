import assert from "node:assert/strict"
import { StarPortFeed, StarPortPage } from "../web/js/starport.js"
import { guidedState, connectionStages, handshakeSteps, StarPortGuidedSetup, FIRST_RUN_NOTICE, EXHAUSTED_NOTICE } from "../web/js/starport-guided.js"

const setup = { identity: { installed: true } }
const labels = { builtin: "Built-in wireless", adapter: "Wireless adapter", usb: "Wired AA", app: "App" }
const ladder = (over = {}) => ({ steps: [{ id: "standard", label: "Standard handshake", state: "current" }, { id: "other_reply", label: "The other version reply", state: "waiting" }],
  learned: false, passes: 0, max_passes: 3, exhausted: false, successes: 0, ...over })
const running = (state, over = {}) => ({ running: true, state, connection: "wireless", setup_ladder: ladder(), ...over })

// Before anything is known the card stays out of the way.
assert.equal(guidedState({}).show, false)
assert.equal(guidedState({ setup: null, runtime: running("rfcomm") }).show, false)

// Without the Android Auto package the first thing to do is that step, nothing else.
{
  const state = guidedState({ setup: { identity: { installed: false } }, runtime: null, labels })
  assert.equal(state.phase, "package")
  assert.equal(state.show, true)
  assert.deepEqual(state.choices, [])
}

// First run: one question, four answers, the current one marked, and only the steps for that answer.
{
  const state = guidedState({ setup, runtime: { running: false, state: "idle" }, carType: "builtin", labels })
  assert.equal(state.phase, "setup")
  assert.deepEqual(state.choices.map((choice) => choice.key), ["builtin", "adapter", "usb", "app"])
  assert.deepEqual(state.choices.map((choice) => choice.selected), [true, false, false, false])
  assert.equal(state.choices[1].label, "Wireless adapter")
  assert.match(state.detail, /pairing screen/)
  assert.match(guidedState({ setup, runtime: null, carType: "adapter", labels }).detail, /adapter in pairing mode/)
  assert.match(guidedState({ setup, runtime: null, carType: "usb", labels }).detail, /USB data port/)
  assert.match(guidedState({ setup, runtime: null, carType: "app", labels }).detail, /tablet/)
}

// Cars to choose between: paired, offering Android Auto, de-duplicated, never the selected one, not while pairing.
{
  const runtime = { running: false, state: "idle", car_candidates: [{ address: "AA:BB:CC:DD:EE:01", name: "Civic" }, { address: "AA:BB:CC:DD:EE:02", name: "Truck" }] }
  const receivers = [{ address: "AA:BB:CC:DD:EE:02", name: "Truck" }, { address: "AA:BB:CC:DD:EE:03", name: "Wagon" }, null, { address: 5, name: "x" }]
  const names = (state) => state.candidates.map((car) => car.name)
  assert.deepEqual(names(guidedState({ setup, runtime, receivers, carType: "builtin" })), ["Civic", "Truck", "Wagon"])
  assert.deepEqual(names(guidedState({ setup, runtime, receivers, carType: "builtin", selected: { address: "AA:BB:CC:DD:EE:01", name: "Civic" } })), ["Truck", "Wagon"])
  assert.deepEqual(names(guidedState({ setup, runtime, receivers, carType: "builtin", pairingActive: true })), [])
  assert.deepEqual(names(guidedState({ setup, runtime, receivers, carType: "usb" })), [])  // a cable has no cars to pair
}

// A finished setup shows nothing unless a connection is in progress.
{
  assert.equal(guidedState({ setup, runtime: { running: false, state: "idle" }, setupDone: true }).show, false)
  assert.equal(guidedState({ setup, runtime: running("streaming"), setupDone: true }).show, false)
  const state = guidedState({ setup, runtime: running("wifi_start"), setupDone: true, carType: "builtin" })
  assert.equal(state.show, true)
  assert.equal(state.phase, "connecting")
}

// The checklist follows the stage the daemon reports.
{
  const states = (runtime) => connectionStages(runtime).map((step) => step.state)
  assert.deepEqual(states(running("connecting_bluetooth")), ["active", "waiting", "waiting", "waiting"])
  assert.deepEqual(states(running("joining_wifi")), ["done", "active", "waiting", "waiting"])
  assert.deepEqual(states(running("authenticating")), ["done", "done", "active", "waiting"])
  assert.deepEqual(states(running("negotiating")), ["done", "done", "done", "active"])
  assert.deepEqual(states(running("streaming")), ["done", "done", "done", "done"])
  assert.deepEqual(states(running("backoff", { last_stage: "wifi_start" })), ["done", "retry", "waiting", "waiting"])
  assert.deepEqual(states(running("backoff", {})), ["retry", "waiting", "waiting", "waiting"])
  assert.deepEqual(connectionStages(running("waiting_for_usb", { connection: "wired" })).map((step) => step.id), ["cable", "secure", "dashboard"])
  assert.deepEqual(connectionStages(running("waiting_for_ipad", { connection: "headunitpad" })).map((step) => step.id), ["app", "secure", "dashboard"])
  assert.deepEqual(connectionStages(null).map((step) => step.state), ["active", "waiting", "waiting", "waiting"])
}

// The first-connection warning appears only to a wireless car with nothing learned, and not once it has worked.
{
  const first = guidedState({ setup, runtime: running("rfcomm"), carType: "builtin" })
  assert.equal(first.notice, FIRST_RUN_NOTICE)
  assert.equal(guidedState({ setup, runtime: running("rfcomm", { setup_ladder: ladder({ learned: true }) }) }).notice, "")
  assert.equal(guidedState({ setup, runtime: running("rfcomm", { setup_ladder: ladder({ successes: 2 }) }) }).notice, "")
  assert.equal(guidedState({ setup, runtime: running("waiting_for_usb", { connection: "wired" }) }).notice, "")
  const exhausted = guidedState({ setup, runtime: running("backoff", { setup_ladder: ladder({ exhausted: true }) }) })
  assert.equal(exhausted.notice, EXHAUSTED_NOTICE)
  assert.equal(exhausted.exhausted, true)
}

// What is being tried is listed only once something was tried and failed, or one option is remembered.
{
  assert.deepEqual(handshakeSteps(ladder()), [])
  assert.deepEqual(handshakeSteps(null), [])
  const tried = ladder({ steps: [{ id: "standard", label: "Standard handshake", state: "failed" }, { id: "other_reply", label: "The other version reply", state: "current" }] })
  assert.deepEqual(handshakeSteps(tried).map((step) => step.state), ["failed", "current"])
  assert.equal(guidedState({ setup, runtime: running("wifi_start", { setup_ladder: tried }) }).handshake.length, 2)
  assert.equal(handshakeSteps(ladder({ learned: true })).length, 2)
  assert.deepEqual(handshakeSteps({ steps: [null, { label: 3 }, { id: "a", label: "A", state: "worked" }] }).map((step) => step.id), ["a"])
}

// The component emits the page's actions and never invents state.
{
  assert.deepEqual(StarPortGuidedSetup.emits, ["choose-type", "use-car", "classic"])
  const template = StarPortGuidedSetup.template
  assert.match(template, /\$emit\('choose-type', choice\.key\)/)
  assert.match(template, /\$emit\('use-car', car\.address\)/)
  assert.match(template, /\$emit\('classic'\)/)
  assert.match(template, /Use classic setup/)
  assert.doesNotMatch(template, /v-html/)
}
// The way back to the classic screens posts one narrow action, even when the StarPort service is off.
{
  const calls = []
  const reply = (body, code = 200) => ({ status: code, ok: code < 300, json: async () => structuredClone(body) })
  const feed = new StarPortFeed({ publish: () => {}, fetcher: async (path, options) => {
    calls.push([path, options])
    return reply(path.endsWith("/setup") ? { enabled: false, bluetoothEnabled: false, parked: true, installReady: true, serviceReady: false,
      identity: { installed: true }, import: {}, maxUploadBytes: 1000, newSetup: true } : { newSetup: false })
  }, later: () => 1, cancelTimer: () => {} })
  assert.equal(await feed.setNewSetup(false), false)  // not started yet
  feed.active = true
  assert.equal(await feed.setNewSetup("no"), false)
  assert.equal(await feed.setNewSetup(false), true)
  const [path, options] = calls.find(([target]) => target.endsWith("/control"))
  assert.equal(path, "./api/starport/control")
  assert.deepEqual(JSON.parse(options.body), { action: "new_setup", enabled: false })
  assert.equal(options.method, "POST")
}

// The page uses the guided card by default and only when the setup response does not say newSetup is off.
{
  const template = StarPortPage.template
  assert.match(template, /<StarPortGuidedSetup v-if="setup && newSetup && guided\.show"/)
  assert.match(template, /@choose-type="chooseType" @use-car="useCar" @classic="useClassic"/)
  assert.match(template, /Try the guided setup/)
  const vm = { setup: { newSetup: false } }
  assert.equal(StarPortPage.computed.newSetup.call(vm), false)
  assert.equal(StarPortPage.computed.newSetup.call({ setup: {} }), true)        // default on, including an older server
  assert.equal(StarPortPage.computed.newSetup.call({ setup: null }), true)
}
console.log("StarPort guided setup: question, steps, candidates, live checklist, first-run notice and handshake list passed")

// The adapter's "connect your car in Bluetooth first" refusal becomes a step with a link, not a dead end.
{
  const { BLUETOOTH_HINT } = await import("../web/js/starport-guided.js")
  const refused = { running: false, state: "error", error: "Connect your paired car in Galaxy Bluetooth first to select it for this Android Auto adapter" }
  const state = guidedState({ setup, runtime: refused, carType: "adapter", labels })
  assert.equal(state.phase, "setup")      // the usual question and steps stay, with the action first
  assert.deepEqual(state.hint, BLUETOOTH_HINT)
  assert.equal(BLUETOOTH_HINT.href, "#/bluetooth")
  const done = guidedState({ setup, runtime: refused, setupDone: true })
  assert.equal(done.phase, "bluetooth")   // even a finished setup shows the thing to fix
  assert.equal(guidedState({ setup, runtime: { running: false, state: "error", error: "something else" }, carType: "adapter", labels }).hint, null)
  assert.match(StarPortGuidedSetup.template, /state\.hint\.href/)
}
console.log("StarPort guided setup: Bluetooth-first hint passed")
