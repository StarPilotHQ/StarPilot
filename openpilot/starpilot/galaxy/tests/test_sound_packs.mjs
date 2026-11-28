import assert from "node:assert/strict"
import { SoundPacks, SoundPacksFeed } from "../web/js/sound-packs.js"
import { SettingsPage } from "../web/js/settings.js"

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }
const reply = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
const packs = [{ id: "one", name: "One", installed: false }, { id: "two", name: "Two", installed: true }]
const snapshot = (job = null, parked = true, list = packs) => ({ parked, packs: list, job })
const job = (state, overrides = {}) => ({ id: "job-1", pack: "one", state, bytes: 2, total: 10, error: "", ...overrides })

function fixture() {
  const requests = [], states = [], installed = [], timers = new Map()
  let next = 0, unauthorized = 0
  const feed = new SoundPacksFeed({ publish: (update) => states.push(update), installed: (pack) => installed.push(pack),
    unauthorized: () => { unauthorized++ },
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })),
    later: (fn, ms) => { const id = ++next; timers.set(id, { fn, ms }); return id },
    cancelTimer: (id) => timers.delete(id) })
  async function respond(index, body, status = 200) { requests[index].resolve(reply(body, status)); await flush() }
  function fire(ms) {
    const entry = [...timers.entries()].find(([, value]) => value.ms === ms)
    assert.ok(entry, `missing ${ms} ms timer`)
    timers.delete(entry[0]); entry[1].fn()
  }
  return { feed, requests, states, installed, timers, respond, fire, get unauthorized() { return unauthorized } }
}

const normal = fixture()
normal.feed.start()
assert.equal(normal.requests[0].url, "./api/sounds")
assert.equal(normal.requests[0].options.credentials, "same-origin")
await normal.respond(0, snapshot())
normal.feed.download("one")
assert.equal(normal.requests[1].url, "./api/sounds/download")
assert.deepEqual(JSON.parse(normal.requests[1].options.body), { pack: "one" })
normal.feed.download("one")
assert.equal(normal.requests.length, 2) // No overlapping dispatch.
await normal.respond(1, snapshot(job("downloading")))
assert.equal(normal.timers.size, 1)
normal.fire(1000)
await normal.respond(2, snapshot(job("verifying", { bytes: 10 })))
normal.fire(1000)
await normal.respond(3, snapshot(job("complete", { bytes: 10 }), true,
  [{ ...packs[0], installed: true }, packs[1]]))
assert.deepEqual(normal.installed, ["one"])
assert.equal(normal.timers.size, 1)
normal.feed.refresh()
// Refresh is pending; complete job must not emit twice.
await normal.respond(4, snapshot(job("complete", { bytes: 10 }), true,
  [{ ...packs[0], installed: true }, packs[1]]))
assert.deepEqual(normal.installed, ["one"])
normal.feed.stop()

const parked = fixture()
parked.feed.start()
await parked.respond(0, snapshot(null, false))
assert.equal(parked.timers.size, 1)
parked.feed.download("one")
assert.equal(parked.requests.length, 1)
assert.equal(SoundPacks.methods.canDownload.call({ disabled: false, state: { busy: false, status: "ready", snapshot: snapshot(null, false) }, running: false }, packs[0]), false)
assert.equal(SoundPacks.methods.canDownload.call({ disabled: false, busy: true,
  state: { busy: true, status: "ready", snapshot: snapshot() }, running: false }, packs[0]), true,
  "saving blocks interaction without changing download eligibility or icon appearance")
parked.feed.refresh()
assert.equal(parked.timers.size, 1) // Only the read timeout remains; parked recheck was cancelled.
await parked.respond(1, snapshot())
parked.feed.download("missing")
parked.feed.download("two")
assert.equal(parked.requests.length, 2)
parked.feed.download("one")
await parked.respond(2, snapshot(job("downloading")))
parked.feed.cancel()
assert.equal(parked.requests[3].url, "./api/sounds/cancel")
assert.deepEqual(JSON.parse(parked.requests[3].options.body), { job: "job-1" })
await parked.respond(3, snapshot(job("cancelled")))
assert.equal(parked.timers.size, 1)
parked.feed.stop()

const racingDownload = fixture()
racingDownload.feed.start(); await racingDownload.respond(0, snapshot())
racingDownload.feed.refresh()
racingDownload.feed.download("one")
assert.equal(racingDownload.requests[1].options.signal.aborted, true)
assert.equal(racingDownload.requests[2].url, "./api/sounds/download")
await racingDownload.respond(2, snapshot(job("downloading")))
await racingDownload.respond(1, snapshot())
assert.equal(racingDownload.states.at(-1).snapshot.job.state, "downloading", "a late read cannot undo the download result")
racingDownload.feed.stop()

const parkedRecovery = fixture()
parkedRecovery.feed.start()
await parkedRecovery.respond(0, snapshot(null, false))
parkedRecovery.fire(5000)
assert.equal(parkedRecovery.requests[1].url, "./api/sounds")
assert.equal(parkedRecovery.states.at(-1).status, "ready")
assert.equal(parkedRecovery.states.at(-1).snapshot.parked, false)
await parkedRecovery.respond(1, snapshot(null, true))
assert.equal(parkedRecovery.states.at(-1).snapshot.parked, true)
assert.equal(parkedRecovery.timers.size, 1)
parkedRecovery.feed.stop()

const parkedUnmount = fixture()
parkedUnmount.feed.start()
await parkedUnmount.respond(0, snapshot(null, false))
parkedUnmount.feed.stop()
assert.equal(parkedUnmount.timers.size, 0)
assert.equal(parkedUnmount.requests.length, 1)

const late = fixture()
late.feed.start()
late.feed.stop()
late.feed.start()
await late.respond(0, snapshot(job("complete")))
assert.equal(late.states.at(-1).status, "loading")
assert.deepEqual(late.installed, [])
await late.respond(1, snapshot(job("downloading")))
assert.equal(late.timers.size, 1)
late.feed.stop()
assert.equal(late.timers.size, 0)
assert.equal(late.requests[1].options.signal.aborted, false) // Completed request; poll alone was retired.
assert.equal(late.states.at(-1).status, "idle")

const stale = fixture()
stale.feed.start()
stale.fire(4000)
assert.match(stale.states.at(-1).error, /timed out/)
assert.equal(stale.requests[0].options.signal.aborted, true)
stale.feed.refresh()
await stale.respond(1, snapshot())
await stale.respond(0, snapshot(job("complete")))
assert.equal(stale.states.at(-1).snapshot.job, null)
assert.deepEqual(stale.installed, [])
stale.feed.stop()

const failed = fixture()
failed.feed.start()
await failed.respond(0, snapshot(job("failed", { error: "Verification failed" })))
assert.equal(failed.states.at(-1).snapshot.job.error, "Verification failed")
assert.equal(failed.timers.size, 1) // Keep checking local status after a failed job.
failed.feed.download("one")
await failed.respond(1, { error: "Vehicle must be parked" }, 409)
assert.match(failed.states.at(-1).error, /Vehicle must be parked/)
assert.equal(failed.states.at(-1).busy, false)
failed.feed.download("one")
assert.equal(failed.requests.length, 2) // Refresh must resolve a stale catalog before another POST.
failed.feed.stop()

const uncertain = fixture()
uncertain.feed.start()
await uncertain.respond(0, snapshot())
uncertain.feed.download("one")
uncertain.fire(4000)
assert.match(uncertain.states.at(-1).error, /result is unknown/)
uncertain.feed.download("one")
assert.equal(uncertain.requests.length, 2)
uncertain.feed.refresh()
await uncertain.respond(2, snapshot(job("downloading")))
await uncertain.respond(1, snapshot(job("complete")))
assert.deepEqual(uncertain.installed, []) // Late POST cannot overtake a fresh GET.
uncertain.feed.stop()

const revoked = fixture()
revoked.feed.start()
await revoked.respond(0, { code: "access_unavailable" }, 503)
assert.equal(revoked.unauthorized, 1)
assert.equal(revoked.states.at(-1).status, "idle")

assert.match(SettingsPage.template, /<div v-if="state\.data\.page === 'sounds'"[^>]*><SoundPacks\b/)
const settingsRefresh = { state: { data: { page: "sounds" }, pending: { intent: "open" }, soundChoicesDirty: false },
  feed: { saving: false, loadCalls: [], load(page, options) { this.loadCalls.push([page, options]) } },
  flushSoundChoices: SettingsPage.methods.flushSoundChoices }
SettingsPage.methods.refreshSoundChoices.call(settingsRefresh)
assert.equal(settingsRefresh.feed.loadCalls.length, 0)
settingsRefresh.state.pending = null
SettingsPage.watch["state.pending"].call(settingsRefresh)
assert.deepEqual(settingsRefresh.feed.loadCalls, [["sounds", { keepData: true }]])
assert.match(SoundPacks.template, /Sound pack catalog/)
assert.match(SoundPacks.template, /Installed/)
assert.match(SoundPacks.template, /Cancel download/)
