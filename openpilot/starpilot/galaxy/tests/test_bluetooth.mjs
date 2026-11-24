import { ConnectionLogs } from "../web/js/local-access.js"
import assert from "node:assert/strict"
import { BluetoothFeed, BluetoothPage, validBluetoothStatus, validPairInput } from "../web/js/bluetooth.js"

const status = { version: 1, available: true, parked: true, powered: true, discovering: false, errorCode: null, pairing: null,
  devices: [{ address: "AA:BB:CC:DD:EE:FF", name: "Speaker", paired: true, connected: false, trusted: true }] }
assert.equal(validBluetoothStatus(status), true)
assert.equal(validBluetoothStatus({ ...status, devices: [{ ...status.devices[0], address: "bad" }] }), false)
assert.match(BluetoothPage.template, /Cancel pairing/i)
assert.match(BluetoothPage.template, /@click="action\('pair', \{address:device.address\}\)"/)
assert.match(BluetoothPage.template, /mode !== 'local'/)
const prompt = { id: "a".repeat(32), kind: "confirmation", value: "123456", displayOnly: false }
assert.equal(validBluetoothStatus({ ...status, pairing: { address: "11:22:33:44:55:66", state: "pairing", prompt } }), true)
assert.equal(validBluetoothStatus({ ...status, pairing: { address: "11:22:33:44:55:66", state: "pairing", prompt: { ...prompt, id: "bad" } } }), false)
assert.equal(validPairInput({ kind: "pin" }, ""), false)
assert.equal(validPairInput({ kind: "pin" }, "1234"), true)
assert.equal(validPairInput({ kind: "pin" }, "\n"), false)
assert.equal(validPairInput({ kind: "passkey" }, "123456"), true)
assert.equal(validPairInput({ kind: "passkey" }, "1234567"), false)
assert.equal(validPairInput({ kind: "confirmation" }, ""), true)
assert.match(BluetoothPage.template, /!canAllowPair/)
// A phone running the StarPilot app is connected but not bonded: it lists under App Pairings, not Saved or Nearby devices.
const appPhone = { address: "11:22:33:44:55:66", name: "iPhone", paired: false, connected: true, trusted: false }
const listed = { status: { ...status, devices: [...status.devices, appPhone] } }
assert.deepEqual(BluetoothPage.computed.saved.call(listed).map((device) => device.name), ["Speaker"])
assert.deepEqual(BluetoothPage.computed.appLinks.call(listed).map((device) => device.name), ["iPhone"])
assert.deepEqual(BluetoothPage.computed.nearby.call(listed), [])
assert.match(BluetoothPage.template, /App Pairings/)

const calls = [], states = [], timers = []
const feed = new BluetoothFeed({ publish: (state) => states.push(state),
  fetcher: async (url, options) => {
    calls.push([url, options])
    return { ok: true, status: 200, json: async () => structuredClone(status) }
  }, later: (fn) => { timers.push(fn); return timers.length }, cancelTimer: () => {} })
await feed.start()
assert.equal(states.at(-1).status.devices[0].name, "Speaker")
await feed.action("connect", { address: "AA:BB:CC:DD:EE:FF" })
assert.deepEqual(calls.map(([url]) => url), ["./api/bluetooth/status", "./api/bluetooth/action", "./api/bluetooth/status"])
assert.deepEqual(JSON.parse(calls[1][1].body), { operation: "connect", address: "AA:BB:CC:DD:EE:FF" })
await feed.action("pair", { address: "11:22:33:44:55:66" })
assert.deepEqual(JSON.parse(calls[3][1].body), { operation: "pair", address: "11:22:33:44:55:66" })
feed.stop()
assert.equal(states.at(-1).status, null)

let settle
const late = new BluetoothFeed({ publish: () => {}, fetcher: () => new Promise((resolve) => { settle = resolve }),
  later: () => 1, cancelTimer: () => {} })
late.start()
late.stop()
settle({ ok: true, status: 200, json: async () => structuredClone(status) })
await Promise.resolve()
await Promise.resolve()
assert.equal(late.status, null)

let settleOld, fetchCount = 0
const freshTimers = []
const restarted = new BluetoothFeed({ publish: () => {}, fetcher: () => {
  fetchCount++
  if (fetchCount === 1) return new Promise((resolve) => { settleOld = resolve })
  return Promise.resolve({ ok: true, status: 200, json: async () => structuredClone(status) })
}, later: (fn, delay) => { if (delay === 2000) freshTimers.push(fn); return fetchCount + freshTimers.length }, cancelTimer: () => {} })
const oldLoad = restarted.start()
restarted.stop()
await restarted.start()
settleOld({ ok: true, status: 200, json: async () => structuredClone(status) })
await oldLoad
assert.equal(restarted.status.devices[0].name, "Speaker")
assert.equal(freshTimers.length, 1)

let revoked = 0
const unauthorized = new BluetoothFeed({ publish: () => {}, unauthorized: () => { revoked++ },
  fetcher: async () => ({ status: 503, json: async () => ({ code: "setup_required" }) }),
  later: () => 1, cancelTimer: () => {} })
await unauthorized.start()
assert.equal(revoked, 1)
assert.equal(unauthorized.active, false)

const htmlStates = []
const htmlFeed = new BluetoothFeed({ publish: state => htmlStates.push(state),
  fetcher: async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token '<'") } }),
  later: () => 1, cancelTimer: () => {} })
await htmlFeed.start()
assert.equal(htmlStates.at(-1).status, null)
assert.equal(htmlStates.at(-1).error, "Galaxy could not reach Bluetooth. Refresh to reconnect.")
htmlFeed.stop()

for (const [code, words] of [["busy", /another operation/], ["park_required", /Park/], ["service_unavailable", /service/], ["adapter_unavailable", /adapter/]]) {
  let actionFailed = false
  const errorFeed = new BluetoothFeed({ publish: () => {},
    fetcher: async () => actionFailed ? ({ ok: false, status: 409, json: async () => ({ code, error: "private backend detail" }) }) :
      ({ ok: true, status: 200, json: async () => structuredClone(status) }),
    later: () => 1, cancelTimer: () => {} })
  await errorFeed.start()
  actionFailed = true
  await errorFeed.action("scan")
  assert.match(errorFeed.error, words)
  assert.doesNotMatch(errorFeed.error, /private backend/)
  assert.equal(errorFeed.status.powered, true)
  assert.equal(errorFeed.active, true)
  errorFeed.stop()
}
assert.equal(validBluetoothStatus({ ...status, errorCode: "service_unavailable" }), true)

// A window opened for a phone has no address until the first phone asks, and only phone windows may omit it.
assert.equal(validBluetoothStatus({ ...status, adapterAddress: "AA:BB:CC:DD:EE:FF", adapterName: "comma",
  pairing: { address: "", state: "pairing", prompt: null, incoming: true, name: "" } }), true)
assert.equal(validBluetoothStatus({ ...status,
  pairing: { address: "11:22:33:44:55:66", state: "pairing", prompt, incoming: true, name: "Pixel" } }), true)
assert.equal(validBluetoothStatus({ ...status, pairing: { address: "", state: "pairing", prompt: null } }), false)
assert.equal(validBluetoothStatus({ ...status, pairing: { address: "not-an-address", state: "pairing", prompt: null, incoming: true, name: "" } }), false)
assert.equal(validBluetoothStatus({ ...status, adapterName: 5 }), false)
assert.match(BluetoothPage.template, /Pair phone/)
assert.match(BluetoothPage.template, /action\('accept_pair'\)/)

assert.equal(BluetoothPage.components.ConnectionLogs, ConnectionLogs)
assert.ok(BluetoothPage.template.includes("<ConnectionLogs v-if=\"mode === 'local' && !unidenSettings\" panel"))
