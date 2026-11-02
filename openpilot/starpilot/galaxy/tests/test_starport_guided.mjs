import assert from "node:assert/strict"
import { StarPortFeed, StarPortPage } from "../web/js/starport.js"
import { guidedState, connectionStages, handshakeSteps, StarPortGuidedSetup, FIRST_RUN_NOTICE, EXHAUSTED_NOTICE } from "../web/js/starport-guided.js"

const setup = { identity: { installed: true } }
const adapterModels = { aawireless: "AAWireless", default: "Motorola MA1", unofficial: "Unofficial dongle" }
const appModels = { headunitpad: "HeadunitPad", openheadunit: "Open Headunit" }
const ladder = (over = {}) => ({ steps: [{ id: "standard", label: "Standard handshake", state: "current" }, { id: "other_reply", label: "The other version reply", state: "waiting" }],
  learned: false, passes: 0, max_passes: 3, exhausted: false, successes: 0, ...over })
const running = (state, over = {}) => ({ running: true, state, connection: "wireless", setup_ladder: ladder(), ...over })

// Before anything is known the card stays out of the way.
assert.equal(guidedState({}).show, false)
assert.equal(guidedState({ setup: null, runtime: running("rfcomm") }).show, false)

// Without the Android Auto package the first thing to do is that step, nothing else.
{
  const state = guidedState({ setup: { identity: { installed: false } }, runtime: null })
  assert.equal(state.phase, "package")
  assert.equal(state.show, true)
  assert.deepEqual(state.choices, [])
}

// First run: one question, four answers, the current one marked, and only the steps for that answer.
{
  const state = guidedState({ setup, runtime: { running: false, state: "idle" }, carType: "builtin" })
  assert.equal(state.phase, "setup")
  assert.deepEqual(state.choices.map((choice) => choice.key), ["builtin", "adapter", "usb", "app"])
  assert.deepEqual(state.choices.map((choice) => choice.selected), [true, false, false, false])
  assert.deepEqual(state.choices.map((choice) => choice.title), ["Built-in wireless", "Wireless adapter", "USB cable", "Tablet app"])
  assert.equal(state.title, "Set up your car")
  assert.match(state.detail, /pairing screen/)
  assert.match(guidedState({ setup, runtime: null, carType: "adapter" }).detail, /pairing mode/)
  assert.match(guidedState({ setup, runtime: null, carType: "usb" }).detail, /USB data port/)
  assert.match(guidedState({ setup, runtime: null, carType: "app" }).detail, /tablet/)
}

// Cars to choose between: paired and offering Android Auto but missing from the page's own paired list; never the selected one,
// never twice, not while pairing. (The page lists the rest itself in the pairing step.)
{
  const runtime = { running: false, state: "idle", car_candidates: [{ address: "AA:BB:CC:DD:EE:01", name: "Civic" }, { address: "AA:BB:CC:DD:EE:02", name: "Truck" }] }
  const receivers = [{ address: "AA:BB:CC:DD:EE:02", name: "Truck" }, { address: "AA:BB:CC:DD:EE:03", name: "Wagon" }, null, { address: 5, name: "x" }]
  const names = (state) => state.candidates.map((car) => car.name)
  assert.deepEqual(names(guidedState({ setup, runtime, receivers, carType: "builtin" })), ["Civic"])
  assert.deepEqual(names(guidedState({ setup, runtime, receivers: [], carType: "builtin" })), ["Civic", "Truck"])
  assert.deepEqual(names(guidedState({ setup, runtime, receivers, carType: "builtin", selected: { address: "AA:BB:CC:DD:EE:01", name: "Civic" } })), [])
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
  assert.deepEqual(StarPortGuidedSetup.emits, ["choose-type", "choose-adapter", "choose-app", "use-car", "classic", "done"])
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
  assert.match(template, /@choose-type="chooseType" @choose-adapter="chooseAdapter" @choose-app="chooseApp" @use-car="useCar" @classic="useClassic"/)
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
  const state = guidedState({ setup, runtime: refused, carType: "adapter" })
  assert.equal(state.phase, "setup")      // the usual question and steps stay, with the action first
  assert.deepEqual(state.hint, BLUETOOTH_HINT)
  assert.equal(BLUETOOTH_HINT.href, "#/bluetooth")
  const done = guidedState({ setup, runtime: refused, setupDone: true })
  assert.equal(done.phase, "bluetooth")   // even a finished setup shows the thing to fix
  assert.equal(guidedState({ setup, runtime: { running: false, state: "error", error: "something else" }, carType: "adapter" }).hint, null)
  assert.match(StarPortGuidedSetup.template, /state\.hint\.href/)
}
console.log("StarPort guided setup: Bluetooth-first hint passed")

// The three steps are always shown, and follow the setup along.
{
  const states = (input) => guidedState({ setup, ...input }).steps.map((step) => step.state)
  const labels = (input) => guidedState({ setup, ...input }).steps.map((step) => step.label)
  assert.deepEqual(guidedState({ setup: { identity: { installed: false } } }).steps.map((step) => step.state), ["current", "todo", "todo"])
  assert.deepEqual(states({ runtime: null }), ["done", "current", "todo"])
  assert.deepEqual(states({ runtime: null, setupDone: true, editing: true }), ["done", "done", "current"])
  assert.deepEqual(states({ runtime: running("wifi_start"), setupDone: true }), ["done", "done", "current"])
  assert.deepEqual(states({ runtime: running("streaming"), setupDone: true }), ["done", "done", "done"])
  assert.deepEqual(labels({ carType: "builtin" }), ["Support", "Car", "Connect"])
  assert.deepEqual(labels({ carType: "adapter" }), ["Support", "Adapter", "Connect"])
  assert.equal(labels({ carType: "usb" })[1], "Cable")
  assert.equal(labels({ carType: "app" })[1], "App")
  // Every phase carries them, including the Bluetooth-first refusal and a connection in progress.
  for (const input of [{ runtime: running("rfcomm") }, { runtime: null }, { runtime: { running: false, error: "Connect your paired car in Galaxy Bluetooth first" }, setupDone: true }]) {
    assert.equal(guidedState({ setup, ...input }).steps.length, 3)
  }
}

// The adapter model and the app are picked in the card, not from a dropdown.
{
  const adapter = guidedState({ setup, runtime: null, carType: "adapter", adapterModels, adapterKey: "" })
  assert.deepEqual(adapter.adapter.options.map((option) => option.label), ["AAWireless", "Motorola MA1", "Unofficial dongle"])
  assert.equal(adapter.adapter.needsChoice, true)
  const chosen = guidedState({ setup, runtime: null, carType: "adapter", adapterModels, adapterKey: "aawireless" })
  assert.deepEqual(chosen.adapter.options.map((option) => option.selected), [true, false, false])
  assert.equal(chosen.adapter.needsChoice, false)
  assert.equal(guidedState({ setup, runtime: null, carType: "builtin", adapterModels }).adapter, null)
  const apps = guidedState({ setup, runtime: null, carType: "app", appModels, app: "HeadunitPad" })
  assert.deepEqual(apps.apps.options.map((option) => [option.key, option.selected]), [["headunitpad", true], ["openheadunit", false]])
  assert.equal(guidedState({ setup, runtime: null, carType: "app", appModels, app: "" }).apps.needsChoice, true)
  assert.equal(guidedState({ setup, runtime: null, carType: "usb", appModels }).apps, null)
}

// A finished setup can be reopened from the home card; Done closes it.
{
  const reopened = guidedState({ setup, runtime: { running: false, state: "idle" }, setupDone: true, editing: true })
  assert.equal(reopened.show, true)
  assert.equal(reopened.editing, true)
  assert.equal(reopened.title, "Change how your car connects")
  assert.equal(guidedState({ setup, runtime: { running: false, state: "idle" }, setupDone: true, editing: false }).show, false)
  assert.match(StarPortGuidedSetup.template, /\$emit\('done'\)/)
}

// Dropdowns belong to the classic screens only; the guided card replaces them and keeps pairing, steps and Connect.
{
  const template = StarPortPage.template
  const guidedBlock = template.slice(template.indexOf("<StarPortGuidedSetup"), template.indexOf("</StarPortGuidedSetup>"))
  for (const dropdown of [":row=\"carTypeRow\"", ":row=\"adapterRow\"", ":row=\"appRow\""]) assert.ok(!guidedBlock.includes(dropdown), dropdown)
  assert.match(guidedBlock, /<template #car>/)
  assert.match(guidedBlock, /beginPairing/)         // Find car / Find adapter
  assert.match(guidedBlock, /@click="connect"/)      // the Connect button
  assert.match(guidedBlock, /Stuck pairing\?/)       // the pairing help stays
  assert.match(template, /<ol v-if="setup && !newSetup" id="gx-starport-steps"/)   // the classic step 2, with its dropdown
  const home = template.slice(template.indexOf("gx-starport-home\" aria-labelledby"))
  assert.match(home, /<template v-if="!newSetup">\s*<GalaxySettingRow :row="carTypeRow"/)
  assert.match(StarPortGuidedSetup.template, /gx-guided__steps/)
  assert.match(StarPortGuidedSetup.template, /<slot name="car" \/>/)
  assert.match(StarPortGuidedSetup.template, /<slot name="connect" \/>/)
}
console.log("StarPort guided setup: steps, adapter and app choices, reopening and classic-only dropdowns passed")

// Choosing a cable or an app finishes the setup at once; a choice made in the card must not close the card.
{
  const calls = []
  const vm = { showSteps: false, carTypeLabels: { app: "App", usb: "Wired AA" },
    saveCarType: (index, label) => calls.push(["type", label]), saveAdapter: (index, label) => calls.push(["adapter", label]),
    saveApp: (index, label) => calls.push(["app", label]) }
  StarPortPage.methods.chooseType.call(vm, "app")
  assert.equal(vm.showSteps, true)
  vm.showSteps = false
  StarPortPage.methods.chooseApp.call(vm, "headunitpad")
  assert.equal(vm.showSteps, true)
  vm.showSteps = false
  StarPortPage.methods.chooseAdapter.call(vm, "aawireless")
  assert.equal(vm.showSteps, true)
  assert.deepEqual(calls, [["type", "App"], ["app", "HeadunitPad"], ["adapter", "AAWireless"]])
  // With the card held open by a choice, the finished setup is shown for editing with its Done button, not hidden.
  const held = guidedState({ setup, runtime: { running: false, state: "idle", connection: "headunitpad" }, setupDone: true, editing: true, carType: "app", appModels, app: "HeadunitPad" })
  assert.equal(held.show, true)
  assert.equal(held.editing, true)
  assert.equal(held.choices.find((choice) => choice.selected).key, "app")
  assert.equal(held.apps.options.find((option) => option.selected).key, "headunitpad")
}
console.log("StarPort guided setup: a choice keeps the card open passed")
