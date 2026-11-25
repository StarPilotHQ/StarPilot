import assert from "node:assert/strict"
import { compile } from "../web/vendor/vue/vue.esm-browser.js"

globalThis.location = { hash: "#/logs/help" }

const { LOG_TOPICS, LogHelp, LogStageClient, MODEL_LIMIT, NOTE_LIMIT, STAGE_URL, captureZip, downloadCaptureZip } = await import("../web/js/log-help.js")
const { Logs } = await import("../web/js/logs.js")

// 1. Topic catalog & template verification (Option A: only 3 topics, zero legacy forms)
assert.deepEqual(LOG_TOPICS.map(t => t.id), ["starport", "connection", "controller"])
compile(LogHelp.template, { decodeEntities: v => v.replaceAll("&amp;", "&") })
compile(Logs.template, { decodeEntities: v => v.replaceAll("&amp;", "&") })
assert.ok(LogHelp.template.includes("https://firestar.link/discord"))
assert.ok(LogHelp.template.includes("Individual session files"))
assert.ok(!LogHelp.template.includes("Connecting via"))
assert.ok(!LogHelp.template.includes("Driving behavior report"))

// 2. Logs route resolution for /logs/help, /logs/help/*, and /logs/starport
for (const [path, expectedRoute, expectedTopic] of [
  ["/logs", false, "starport"],
  ["/logs/help", true, "starport"],
  ["/logs/starport", true, "starport"],
  ["/logs/help/connection", true, "connection"],
  ["/logs/help/controller", true, "controller"],
]) {
  assert.equal(Logs.computed.helpRoute.call({ path }), expectedRoute, `${path} helpRoute`)
  assert.equal(Logs.computed.helpTopic.call({ path }), expectedTopic, `${path} helpTopic`)
}

// 3. LogStageClient lifecycle, validation, polling, ready callback, reset, and 401
const CLAIM = "https://starpilot-log-relay.nathan-g-hubbard.workers.dev/report/galaxy/claim/" + "f".repeat(64)
const state = { state: "idle", message: "", claimUrl: "", kind: "", error: "" }
const readyUrls = []
const scheduled = []
const requests = []
let unauthorizedCount = 0

let pollStep = 0
const client = new LogStageClient({
  publish: update => Object.assign(state, update),
  unauthorized: () => { unauthorizedCount++ },
  schedule: (fn, ms) => { scheduled.push({ fn, ms }); return scheduled.length },
  cancel: () => {},
  onReady: url => readyUrls.push(url),
  fetcher: async (url, options) => {
    requests.push({ url, options })
    if (options.method === "POST") {
      return { ok: true, status: 202, json: async () => ({ state: "staging", message: "Collecting logs", claimUrl: "", kind: JSON.parse(options.body).kind }) }
    }
    pollStep++
    if (pollStep === 1) {
      return { ok: true, status: 200, json: async () => ({ state: "idle", message: "", claimUrl: "", kind: "", available: true, fork: false, noteLimit: NOTE_LIMIT, modelLimit: MODEL_LIMIT }) }
    }
    return { ok: true, status: 200, json: async () => ({ state: "ready", message: "Logs uploaded", claimUrl: CLAIM, kind: "connection", available: true }) }
  },
})

await client.start()
assert.equal(requests[0].url, STAGE_URL)
assert.equal(requests[0].options.cache, "no-store")
assert.equal(requests[0].options.credentials, "same-origin")
assert.equal(scheduled.length, 0, "idle state does not schedule polling")

assert.equal(await client.stage({ kind: "unknown", note: "hi" }), false)
assert.match(state.error, /Choose what/)
assert.equal(await client.stage({ kind: "starport", note: "   " }), false)
assert.match(state.error, /short note/)
assert.equal(await client.stage({ kind: "starport", note: "a".repeat(NOTE_LIMIT + 1) }), false)
assert.match(state.error, /200 characters/)
assert.equal(await client.stage({ kind: "connection", phone: "web", note: "hi" }), false)
assert.match(state.error, /iPhone or Android/)
assert.equal(await client.stage({ kind: "controller", model: "  ", note: "hi" }), false)
assert.match(state.error, /make and model/)
assert.equal(await client.stage({ kind: "controller", model: "m".repeat(MODEL_LIMIT + 1), note: "hi" }), false)
assert.match(state.error, /80 characters/)
assert.equal(requests.length, 1, "client-side validation prevents network calls")

assert.equal(await client.stage({ kind: "connection", phone: "ios", note: "Stuck connecting" }), true)
const postBody = JSON.parse(requests.at(-1).options.body)
assert.deepEqual(postBody, { kind: "connection", note: "Stuck connecting", phone: "ios" })
assert.equal(state.state, "staging")
assert.equal(scheduled.length, 1)

await client.poll()
assert.equal(state.state, "ready")
assert.equal(state.claimUrl, CLAIM)
assert.deepEqual(readyUrls, [CLAIM])

client.reset(CLAIM)
assert.equal(state.state, "idle")
assert.equal(state.claimUrl, "")
await client.poll()
assert.equal(state.state, "idle", "dismissed claimUrl stays hidden on subsequent polls")
assert.equal(readyUrls.length, 1)

client.fetcher = async () => ({ status: 401 })
await client.poll()
assert.equal(unauthorizedCount, 1)
assert.equal(client.active, false)

// 4. captureZip and downloadCaptureZip contracts
const originalDocument = globalThis.document
const clicked = []
globalThis.document = {
  createElement(tag) {
    assert.equal(tag, "a")
    return { href: "", download: "", click() { clicked.push(this.download) }, remove() {} }
  },
  body: { appendChild() {} },
}
try {
  const validZip = "UEsDBAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  const okConnection = await downloadCaptureZip("connection", {
    fetcher: async url => {
      assert.equal(url, "./api/local-access/capture")
      return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ schemaVersion: 1, zipBase64: validZip }) }
    },
  })
  assert.equal(okConnection, true)
  const okController = await downloadCaptureZip("controller", {
    fetcher: async url => {
      assert.equal(url, "./api/controllers/log/capture")
      return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ schemaVersion: 1, zipBase64: validZip }) }
    },
  })
  assert.equal(okController, true)
  assert.deepEqual(clicked, ["starpilot-connection-logs.zip", "starpilot-controller-logs.zip"])

  await assert.rejects(
    () => captureZip("connection", { fetcher: async () => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ schemaVersion: 1, zipBase64: btoa("not-a-zip-header-at-all-123456") }) }) }),
    /Invalid diagnostic capture/,
  )
} finally {
  globalThis.document = originalDocument
}

console.log("LogHelp UI routing, LogStageClient, and captureZip checks passed")
