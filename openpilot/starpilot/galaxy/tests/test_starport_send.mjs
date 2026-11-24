import assert from "node:assert/strict"
import { StarPortLogSend, SEND_URL, BLUETOOTH_SEND_URL, MESSAGE_LIMIT } from "../web/js/starport-logs.js"

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fixture() {
  const requests = [], states = [], timers = []
  let unauthorized = 0
  const client = new StarPortLogSend({ publish: (state) => states.push(state), unauthorized: () => { unauthorized++ },
    fetcher: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
    schedule: (fn) => { timers.push(fn); return timers.length }, cancel: () => {} })
  const reply = async (index, body, status = 200) => {
    requests[index].resolve({ ok: status >= 200 && status < 300, status, json: async () => body })
    await flush()
  }
  return { client, requests, states, timers, reply, get unauthorized() { return unauthorized } }
}

assert.equal(SEND_URL, "./api/starport/logs/send")
assert.equal(MESSAGE_LIMIT, 60)

// Status on open, then a send that is polled until it finishes.
const normal = fixture()
normal.client.start()
assert.equal(normal.requests[0].url, SEND_URL)
await normal.reply(0, { state: "idle", message: "", available: true })
assert.equal(normal.states.at(-1).state, "idle")
const sending = normal.client.send("  Black screen  ", "starfan_42")
assert.equal(normal.states.at(-1).state, "sending")
assert.equal(normal.requests[1].options.method, "POST")
assert.deepEqual(JSON.parse(normal.requests[1].options.body), { message: "Black screen", discord: "starfan_42" })
await normal.reply(1, { state: "sending", message: "Collecting logs" }, 202)
assert.equal(await sending, true)
assert.equal(normal.timers.length, 1)
normal.timers[0]()
await normal.reply(2, { state: "sent", message: "Logs sent. Thanks!" })
assert.equal(normal.states.at(-1).state, "sent")
assert.equal(normal.timers.length, 1)

// Too long is refused locally; a busy device reports its error.
const limited = fixture()
limited.client.start()
await limited.reply(0, { state: "idle" })
assert.equal(await limited.client.send("x".repeat(61), "starfan_42"), false)
assert.equal(limited.requests.length, 1)
assert.match(limited.states.at(-1).error, /60 characters/)
assert.equal(await limited.client.send("   ", "starfan_42"), false)
assert.equal(limited.requests.length, 1)
assert.match(limited.states.at(-1).error, /short note/)
assert.equal(await limited.client.send("hi", "  @ "), false)
assert.equal(limited.requests.length, 1)
assert.match(limited.states.at(-1).error, /Discord username/)
const busy = limited.client.send("hi", "starfan_42")
await limited.reply(1, { error: "StarPort logs are already being sent." }, 409)
assert.equal(await busy, false)
assert.equal(limited.states.at(-1).error, "StarPort logs are already being sent.")

// Signed out.
const signedOut = fixture()
signedOut.client.start()
await signedOut.reply(0, {}, 401)
assert.equal(signedOut.unauthorized, 1)
assert.equal(await signedOut.client.send("hi", "starfan_42"), false)

// Sending unavailable on this build hides the card.
const unset = fixture()
unset.client.start()
await unset.reply(0, { state: "idle", available: false })
assert.equal(unset.states.at(-1).available, false)
assert.equal(unset.states.at(-1).fork, false)

// A custom fork can still send, with a warning.
const fork = fixture()
fork.client.start()
await fork.reply(0, { state: "idle", available: true, fork: true })
assert.equal(fork.states.at(-1).available, true)
assert.equal(fork.states.at(-1).fork, true)

// A lost Bluetooth response retries the status read without repeating the upload.
const interrupted = fixture()
interrupted.client.start()
await interrupted.reply(0, { state: "sending" })
interrupted.timers[0]()
interrupted.requests[1].reject(new TypeError("Connection lost"))
await flush()
assert.equal(interrupted.timers.length, 2)
interrupted.timers[1]()
await interrupted.reply(2, { state: "sent", message: "Logs sent" })
assert.equal(interrupted.states.at(-1).state, "sent")
assert.ok(interrupted.requests.every(({ options }) => options.method !== "POST"))

// An older status read cannot replace a send's newer status, even across navigation.
const stale = fixture()
stale.client.start()
const pendingSend = stale.client.send("hello", "starfan_42")
await stale.reply(1, { state: "sending" }, 202)
assert.equal(await pendingSend, true)
await stale.reply(0, { state: "idle" })
assert.equal(stale.states.at(-1).state, "sending")
stale.client.start()
stale.client.start()
await stale.reply(2, {}, 401)
assert.equal(stale.unauthorized, 0)
await stale.reply(3, { state: "sent" })
assert.equal(stale.states.at(-1).state, "sent")

// The Discord username is sent as typed; the device strips the @.
{
  const followUp = fixture()
  followUp.client.start()
  await followUp.reply(0, { state: "idle", message: "", available: true })
  followUp.client.send("No video", "  @star.fan_42 ")
  assert.deepEqual(JSON.parse(followUp.requests[1].options.body), { message: "No video", discord: "@star.fan_42" })
}

// The Bluetooth Log uses the same client with its own route and wording.
{
  const requests = [], states = []
  const client = new StarPortLogSend({ url: BLUETOOTH_SEND_URL, unavailable: "Sending the Bluetooth Log is unavailable",
    publish: (state) => states.push(state), fetcher: (url) => { requests.push(url); return Promise.reject(new TypeError("offline")) },
    schedule: () => 1, cancel: () => {} })
  client.start()
  await flush()
  assert.equal(requests[0], "./api/controllers/log/send")
  assert.equal(states.at(-1).error, "offline")
  const failing = new StarPortLogSend({ url: BLUETOOTH_SEND_URL, unavailable: "Sending the Bluetooth Log is unavailable",
    publish: (state) => states.push(state), fetcher: () => Promise.resolve({ ok: false, status: 503, json: async () => ({}) }),
    schedule: () => 1, cancel: () => {} })
  failing.start()
  await flush()
  assert.equal(states.at(-1).error, "Sending the Bluetooth Log is unavailable")
}

// Clicking Send with an empty field outlines it like the theme maker's save prompt, without sending.
{
  const { LogSendCard } = await import("../web/js/starport-logs.js")
  const sent = [], focused = []
  const card = { ...LogSendCard.data(), feed: { send: async (...args) => { sent.push(args); return true } },
    $refs: { note: { focus: () => focused.push("note") }, discord: { focus: () => focused.push("discord") } } }
  const submit = LogSendCard.methods.submit.bind(card)
  await submit()
  assert.deepEqual(card.missing, ["note", "discord"])
  assert.deepEqual(focused, ["note"])
  card.note = "No pad"
  LogSendCard.watch.note.call(card, card.note)
  assert.deepEqual(card.missing, ["discord"])
  card.discord = "@"
  await submit()
  assert.deepEqual(card.missing, ["discord"])
  assert.equal(sent.length, 0)
  card.discord = "starfan_42"
  await submit()
  assert.deepEqual(card.missing, [])
  assert.deepEqual(sent, [["No pad", "starfan_42"]])
  assert.equal(card.note, "")
  assert.match(LogSendCard.template, /gx-field--required/)
}
console.log("StarPort log send: Discord follow-up passed")
