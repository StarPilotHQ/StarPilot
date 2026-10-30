import assert from "node:assert/strict"
import { StarPortLogSend, SEND_URL, MESSAGE_LIMIT } from "../web/js/starport-logs.js"

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fixture() {
  const requests = [], states = [], timers = []
  let unauthorized = 0
  const client = new StarPortLogSend({ publish: (state) => states.push(state), unauthorized: () => { unauthorized++ },
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })),
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
const sending = normal.client.send("  Black screen  ")
assert.equal(normal.states.at(-1).state, "sending")
assert.equal(normal.requests[1].options.method, "POST")
assert.deepEqual(JSON.parse(normal.requests[1].options.body), { message: "Black screen" })
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
assert.equal(await limited.client.send("x".repeat(61)), false)
assert.equal(limited.requests.length, 1)
assert.match(limited.states.at(-1).error, /60 characters/)
const busy = limited.client.send("hi")
await limited.reply(1, { error: "StarPort logs are already being sent." }, 409)
assert.equal(await busy, false)
assert.equal(limited.states.at(-1).error, "StarPort logs are already being sent.")

// Signed out.
const signedOut = fixture()
signedOut.client.start()
await signedOut.reply(0, {}, 401)
assert.equal(signedOut.unauthorized, 1)
assert.equal(await signedOut.client.send("hi"), false)

// Sending unavailable on this build hides the card.
const unset = fixture()
unset.client.start()
await unset.reply(0, { state: "idle", available: false })
assert.equal(unset.states.at(-1).available, false)
