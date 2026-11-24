import assert from "node:assert/strict"
import { StarPortLogsFeed, outcomeLabel } from "../web/js/starport-logs.js"

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fixture() {
  const requests = [], states = []
  let unauthorized = 0
  const feed = new StarPortLogsFeed({ publish: (state) => states.push(state), unauthorized: () => { unauthorized++ },
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, signal: options.signal, resolve })) })
  const reply = async (index, body, status = 200) => {
    requests[index].resolve({ ok: status === 200, status, json: async () => body })
    await flush()
  }
  return { feed, requests, states, reply, get unauthorized() { return unauthorized } }
}

assert.equal(outcomeLabel("failed: RFCOMM: timed out"), "Failed: RFCOMM: timed out")
assert.equal(outcomeLabel(""), "Unknown")

const listing = { schemaVersion: 1, sessions: [{ name: "session-000002-x.jsonl", size: 10, modifiedAt: 100, outcome: "projected", car: "Honda Civic" }],
  others: [{ name: "car_ui.log", size: 5, modifiedAt: 90 }] }

const normal = fixture()
normal.feed.start()
assert.equal(normal.requests[0].url, "./api/starport/logs")
assert.equal(normal.states.at(-1).status, "loading")
await normal.reply(0, listing)
assert.equal(normal.states.at(-1).status, "ready")
assert.deepEqual(normal.states.at(-1).sessions, listing.sessions)
assert.deepEqual(normal.states.at(-1).others, listing.others)
normal.feed.load()
assert.equal(normal.requests[1].url, "./api/starport/logs")
normal.feed.stop()
assert.equal(normal.requests[1].signal.aborted, true)
assert.deepEqual(normal.states.at(-1).sessions, [])

const stale = fixture()
stale.feed.start()
stale.feed.load()
await stale.reply(1, listing)
const count = stale.states.length
await stale.reply(0, { schemaVersion: 1, sessions: [], others: [] })
assert.equal(stale.states.length, count)

const revoked = fixture()
revoked.feed.start()
await revoked.reply(0, { error: "Sign in" }, 401)
assert.equal(revoked.unauthorized, 1)
assert.deepEqual(revoked.states.at(-1).sessions, [])

const broken = fixture()
broken.feed.start()
await broken.reply(0, { error: "Android Auto logs are unavailable" }, 503)
assert.equal(broken.states.at(-1).status, "unavailable")
assert.equal(broken.unauthorized, 0)
