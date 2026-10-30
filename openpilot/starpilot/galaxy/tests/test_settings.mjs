import assert from "node:assert/strict"
import { SettingsFeed, SettingsPage, SETTINGS_SECTIONS, FIRST_REQUEST_DEADLINE_MS, REQUEST_DEADLINE_MS } from "../web/js/settings.js"

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body,
  clone() { return response(body, status) } })

function fixture() {
  const requests = [], states = []
  const timers = new Map()
  let nextTimer = 0
  let unauthorized = 0
  const feed = new SettingsFeed({ publish: (state) => states.push(state), unauthorized: () => { unauthorized++ },
    later: (fn, ms) => { assert.ok([1500, 3000, REQUEST_DEADLINE_MS, FIRST_REQUEST_DEADLINE_MS].includes(ms)); const id = ++nextTimer; timers.set(id, { fn, ms }); return id },
    cancelTimer: (id) => timers.delete(id),
    fetcher: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })) })
  async function reply(index, body, status = 200) { requests[index].resolve(response(body, status)); await flush() }
  function expire(expectedMs) {
    assert.equal(timers.size, 1)
    const [id, { fn, ms }] = timers.entries().next().value
    assert.equal(ms, expectedMs, `unexpected request deadline ${ms}`)
    timers.delete(id)
    fn()
  }
  function firePoll() {
    const entry = [...timers.entries()].find(([, timer]) => timer.ms === 1500)
    assert.ok(entry)
    timers.delete(entry[0]); entry[1].fn()
  }
  return { feed, requests, states, reply, expire, firePoll, timers, get unauthorized() { return unauthorized } }
}

const lane = { page: "lane", title: "Lane Centering", subtitle: "Saved preference", parked: true, view: "opaque-view",
  rows: [{ revision: "row-1", label: "Enable Lane Centering", value: "Off", available: true, action: true, page: "", confirm: false,
    choices: ["Off", "On"], step: 0, repairValue: "" }] }

const normal = fixture()
normal.feed.start()
assert.equal(normal.requests[0].url, "./api/settings/pages/hub")
await normal.reply(0, { ...lane, page: "hub" })
normal.feed.load("lane")
await normal.reply(1, lane)
normal.feed.preview(0, 1)
assert.equal(normal.requests[2].url, "./api/settings/preview")
assert.deepEqual(JSON.parse(normal.requests[2].options.body), { view: "opaque-view", row: 0, direction: 1 })
await normal.reply(2, { intent: "opaque-intent", question: "Save this preference?", proposed: "On" })
assert.equal(normal.states.at(-1).pending.question, "Save this preference?")
assert.equal(normal.requests.length, 3) // Preview never saves.
normal.feed.confirm()
assert.equal(normal.states.at(-1).pending, null)
assert.equal(normal.states.at(-1).status, "saving")
normal.feed.load("hub") // Back or Refresh cannot supersede a dispatched save with an early stale read.
assert.equal(normal.requests.length, 4)
assert.equal(normal.feed.page, "lane")
assert.deepEqual(JSON.parse(normal.requests[3].options.body), { intent: "opaque-intent", confirmed: true, page: "lane" })
await normal.reply(3, { saved: true })
assert.equal(normal.requests[4].url, "./api/settings/pages/lane")
await normal.reply(4, { ...lane, view: "refreshed-view", rows: [{ ...lane.rows[0], value: "On" }] })
assert.equal(normal.states.at(-1).data.rows[0].value, "On")

const immediate = fixture()
immediate.feed.start("lane")
await immediate.reply(0, lane)
immediate.feed.preview(0, 1)
await immediate.reply(1, { intent: "quick-intent", question: "Save?", proposed: "On" })
immediate.feed.confirm()
await immediate.reply(2, { saved: true, page: { ...lane, view: "fresh-view", rows: [{ ...lane.rows[0], value: "On" }] } })
assert.equal(immediate.states.at(-1).status, "ready")
assert.equal(immediate.states.at(-1).data.view, "fresh-view")
assert.equal(immediate.requests.length, 3, "the save response supplies the fresh page without another blocking request")
immediate.feed.stop()

const refreshed = fixture()
refreshed.feed.start("lane")
await refreshed.reply(0, lane)
const keyContext = { state: { data: lane } }
const originalKey = SettingsPage.methods.rowKey.call(keyContext, lane.rows[0], 0)
refreshed.feed.load()
assert.equal(refreshed.states.at(-1).status, "ready")
assert.equal(refreshed.states.at(-1).data, lane)
await refreshed.reply(1, { ...lane, view: "new-view", rows: structuredClone(lane.rows) })
keyContext.state.data = refreshed.states.at(-1).data
assert.equal(SettingsPage.methods.rowKey.call(keyContext, keyContext.state.data.rows[0], 0), originalKey)
refreshed.feed.preview(0, 1)
assert.equal(JSON.parse(refreshed.requests[2].options.body).view, "new-view")
assert.equal(SettingsPage.methods.rowKey.call(keyContext, { ...lane.rows[0], revision: "new-vehicle" }, 0), originalKey)
assert.equal(SettingsPage.methods.rowKey.call(keyContext, { ...lane.rows[0], available: false }, 0), originalKey)
assert.equal(SettingsPage.methods.rowKey.call(keyContext, { ...lane.rows[0], value: "On" }, 0), originalKey)
refreshed.feed.load("torque")
assert.equal(refreshed.states.at(-1).status, "loading")
assert.equal(refreshed.states.at(-1).data, null)
refreshed.feed.stop()

const conflict = fixture()
conflict.feed.start()
await conflict.reply(0, lane)
conflict.feed.preview(0, 1)
await conflict.reply(1, { intent: "expired", question: "Save?" })
conflict.feed.confirm()
await conflict.reply(2, { error: "Changed" }, 409)
assert.equal(conflict.requests[3].url, "./api/settings/pages/hub")
await conflict.reply(3, lane)
assert.match(conflict.states.at(-1).error, /could not be confirmed/)

const stalePreview = fixture()
stalePreview.feed.start("standard/acceleration")
await stalePreview.reply(0, { ...lane, page: "standard/acceleration" })
stalePreview.feed.preview(0, 1)
await stalePreview.reply(1, { error: "Changed" }, 409)
assert.equal(stalePreview.states.at(-1).status, "unavailable")
assert.equal(stalePreview.states.at(-1).data, null)
assert.equal(stalePreview.feed.data, null)
assert.equal(stalePreview.states.at(-1).pending, null)
stalePreview.feed.load()
await stalePreview.reply(2, { ...lane, page: "standard/acceleration", view: "new-source" })
assert.equal(stalePreview.states.at(-1).data.view, "new-source")

const cancel = fixture()
cancel.feed.start()
await cancel.reply(0, lane)
cancel.feed.preview(0, 1)
await cancel.reply(1, { intent: "cancelled", question: "Save?" })
cancel.feed.cancel()
cancel.feed.confirm()
assert.equal(cancel.requests.length, 2)

const late = fixture()
late.feed.start()
late.feed.stop()
late.feed.start()
await late.reply(0, lane)
assert.equal(late.states.at(-1).status, "loading")
await late.reply(1, lane)
late.feed.preview(0, 1)
late.feed.stop() // Navigation or logout unmounts the page.
await late.reply(2, { intent: "stale", question: "Old confirmation" })
assert.equal(late.states.at(-1).status, "idle")
assert.equal(late.states.at(-1).pending, null)

const expired = fixture()
expired.feed.start()
await expired.reply(0, {}, 401)
assert.equal(expired.unauthorized, 1)
assert.equal(expired.states.at(-1).status, "idle")

assert.match(SettingsPage.template, /v-if="state.pending"/)
assert.match(SettingsPage.template, /:disabled="!!state\.error \|\| !row\.available"/)
assert.match(SettingsPage.template, /Local settings are unavailable in preview/)
assert.match(SettingsPage.template, /state.data\?\.subtitle/)
// Driving Screen Widgets opens the layout editor on its Display tab; target switches go through the router.
assert.match(SettingsPage.template, /:initial-panel="state\.layoutPanel" @target="openLayoutTarget"/)
assert.doesNotMatch(SettingsPage.template, /Use Colors &amp; Layout to arrange widgets and change colors/)
assert.match(SettingsPage.template, /Configure lane-change assistance, signal timing, steering pace and following gap\./)
assert.match(SettingsPage.template, /Adjust acceleration, braking and following for each driving personality\./)
assert.match(SettingsPage.template, /Tune low-speed following and jerk, blending toward Relaxed at higher speeds\./)
const app = (await import('node:fs')).readFileSync(new URL('../web/js/app.js', import.meta.url), 'utf8')
assert.match(app, /:initial-panel="route\.path === '\/appearance' \? 'display' : 'widgets'"/)
const filtered = SettingsPage.computed.visibleRows.call({ state: { query: "lane", data: { rows: [
  { label: "SLC", value: "Off", reason: "" }, { label: "Lane Centering", value: "On", reason: "" },
] } } })
assert.deepEqual(filtered.map(({ index }) => index), [1]) // Filter keeps the original owner row index.

const conditional = fixture()
conditional.feed.start()
await conditional.reply(0, { ...lane, page: "hub", rows: [{ label: "Conditional driving modes", page: "conditional",
  value: "Development saved settings", available: true, action: false }] })
conditional.feed.load("conditional")
assert.equal(conditional.requests[1].url, "./api/settings/pages/conditional")
await conditional.reply(1, { ...lane, page: "conditional", rows: [{ ...lane.rows[0], label: "Saved driving mode",
  value: "Stock", choices: ["Stock", "Conditional Experimental", "Conditional Chill"] }] })
conditional.feed.preview(0, 1)
await conditional.reply(2, { intent: "mode-once", question: "Save for a later drive?", proposed: "Conditional Experimental" })
assert.equal(conditional.states.at(-1).pending.proposed, "Conditional Experimental")
conditional.feed.stop() // Navigation revokes the pending one-time intent locally.
conditional.feed.confirm()
assert.equal(conditional.requests.length, 3)

const manual = fixture()
manual.feed.start()
await manual.reply(0, { ...lane, page: "hub" })
manual.feed.load("conditional/cem")
await manual.reply(1, { ...lane, page: "conditional/cem", rows: [{ ...lane.rows[0],
  label: "Remember manual choice", value: "Off", reason: "Clears this mode's remembered choice before saving" }] })
manual.feed.preview(0, 1)
await manual.reply(2, { intent: "persist-once", question: "Clear old choice and save?", proposed: "On" })
assert.equal(manual.states.at(-1).pending.proposed, "On")
manual.feed.confirm()
await manual.reply(3, { saved: true })
assert.equal(manual.requests[4].url, "./api/settings/pages/conditional%2Fcem")
await manual.reply(4, { ...lane, page: "conditional/cem", rows: [{ ...lane.rows[0],
  label: "Remember manual choice", value: "On" }] })
assert.equal(manual.states.at(-1).data.rows[0].value, "On")

// An ignored AbortSignal must not leave the page loading or replace a fresh view.
const timeout = fixture()
timeout.feed.start()
timeout.expire(FIRST_REQUEST_DEADLINE_MS) // A cold link gets the long first-request deadline.
assert.equal(timeout.states.at(-1).status, "unavailable")
assert.match(timeout.states.at(-1).error, /Reconnecting/)
assert.equal(timeout.requests[0].options.signal.aborted, true)
timeout.feed.load("lane")
await timeout.reply(1, { ...lane, view: "new-view" })
await timeout.reply(0, { ...lane, view: "late-view" })
assert.equal(timeout.states.at(-1).data.view, "new-view")
assert.equal(timeout.timers.size, 1)
timeout.feed.stop()

// Losing a save response cannot prove that the already dispatched save failed.
const uncertain = fixture()
uncertain.feed.start()
await uncertain.reply(0, lane)
uncertain.feed.preview(0, 1)
await uncertain.reply(1, { intent: "one-shot", question: "Save?" })
uncertain.feed.confirm()
uncertain.feed.preview(0, 1) // Do not overlap an in-flight save with a new intent.
uncertain.feed.load("slc")
assert.equal(uncertain.requests.length, 3)
uncertain.expire(REQUEST_DEADLINE_MS) // The link is warm after the first reply, so saves keep the short deadline.
assert.match(uncertain.states.at(-1).error, /Checking saved values/)
assert.equal(uncertain.states.at(-1).data, null)
assert.equal(uncertain.requests.length, 3) // Neither retry the POST nor silently read and claim failure.
uncertain.feed.confirm()
assert.equal(uncertain.requests.length, 3)
uncertain.feed.load()
await uncertain.reply(3, { ...lane, rows: [{ ...lane.rows[0], value: "On" }] })
await uncertain.reply(2, { saved: true })
assert.equal(uncertain.requests.length, 4)
assert.equal(uncertain.states.at(-1).data.rows[0].value, "On")

// Awaiting an old credential error body must not revoke a newer session/view.
const staleAccess = fixture()
staleAccess.feed.start()
let finishAccessBody
staleAccess.requests[0].resolve({ ok: false, status: 503,
  clone: () => ({ json: () => new Promise((resolve) => { finishAccessBody = resolve }) }) })
await flush()
staleAccess.feed.stop()
staleAccess.feed.start()
await staleAccess.reply(1, lane)
finishAccessBody({ code: "access_unavailable" })
await flush()
assert.equal(staleAccess.unauthorized, 0)
assert.equal(staleAccess.states.at(-1).data.view, lane.view)

// The same retirement applies when headers arrived but the success body stalls.
const bodyTimeout = fixture()
bodyTimeout.feed.start()
let finishBody
bodyTimeout.requests[0].resolve({ ok: true, status: 200,
  json: () => new Promise((resolve) => { finishBody = resolve }) })
await flush()
bodyTimeout.expire(FIRST_REQUEST_DEADLINE_MS) // The deadline is captured when the request is dispatched.
bodyTimeout.feed.load("lane")
await bodyTimeout.reply(1, { ...lane, view: "after-body-timeout" })
finishBody(lane)
await flush()
assert.equal(bodyTimeout.states.at(-1).data.view, "after-body-timeout")
assert.equal(bodyTimeout.timers.size, 1)
bodyTimeout.feed.stop()

// An older save's post-error read cannot attach its warning to a different page.
const replacedRead = fixture()
replacedRead.feed.start()
await replacedRead.reply(0, lane)
replacedRead.feed.preview(0, 1)
await replacedRead.reply(1, { intent: "uncertain", question: "Save?" })
replacedRead.feed.confirm()
await replacedRead.reply(2, {}, 409)
assert.equal(replacedRead.requests.length, 4)
replacedRead.feed.load("slc")
await replacedRead.reply(4, { ...lane, page: "slc", view: "new-page" })
await replacedRead.reply(3, lane)
assert.equal(replacedRead.states.at(-1).data.view, "new-page")
assert.equal(replacedRead.states.at(-1).error, "")

// Scalar controls preserve the original one-gesture save through the guarded owner.
const scalar = fixture()
scalar.feed.start("lane")
await scalar.reply(0, lane)
const savingScalar = scalar.feed.previewValue(0, "On")
assert.equal(scalar.states.at(-1).status, "updating")
assert.deepEqual(JSON.parse(scalar.requests[1].options.body), { view: "opaque-view", row: 0, value: "On" })
await scalar.reply(1, { intent: "scalar-once", question: "Save?", proposed: "On" })
assert.equal(scalar.states.some((state) => state.pending !== null), false)
assert.equal(scalar.requests[2].url, "./api/settings/confirm")
await scalar.reply(2, { saved: true })
assert.equal(scalar.states.at(-1).data.view, lane.view) // Keep the familiar rows during refresh.
assert.equal(scalar.states.at(-1).status, "saving")
await scalar.reply(3, { ...lane, view: "scalar-saved", rows: [{ ...lane.rows[0], value: "On" }] })
await savingScalar
assert.equal(scalar.states.at(-1).data.rows[0].value, "On")

const scalarRetired = fixture()
scalarRetired.feed.start("lane")
await scalarRetired.reply(0, lane)
scalarRetired.feed.previewValue(0, "On")
scalarRetired.feed.stop()
await scalarRetired.reply(1, { intent: "retired-scalar", proposed: "On" })
assert.equal(scalarRetired.requests.length, 2) // A hidden control never confirms its late preview.

const scalarRejected = fixture()
scalarRejected.feed.start("lane")
await scalarRejected.reply(0, lane)
scalarRejected.feed.previewValue(0, "On")
await scalarRejected.reply(1, { error: "Unavailable" }, 400)
assert.equal(scalarRejected.requests.length, 2)
assert.equal(scalarRejected.states.at(-1).status, "ready")
assert.equal(scalarRejected.states.at(-1).data.rows[0].value, "Off")

// A fresh parked reading can arrive after the first page response.
const parkedRecovery = fixture()
parkedRecovery.feed.start("lane")
await parkedRecovery.reply(0, { ...lane, parked: false })
assert.equal(parkedRecovery.timers.size, 1)
parkedRecovery.firePoll()
assert.equal(parkedRecovery.requests[1].url, "./api/settings/pages/lane")
assert.equal(parkedRecovery.states.at(-1).status, "ready")
assert.equal(parkedRecovery.states.at(-1).data.view, lane.view)
await parkedRecovery.reply(1, { ...lane, view: "fresh-parked" })
assert.equal(parkedRecovery.states.at(-1).data.parked, true)
assert.equal(parkedRecovery.timers.size, 1)
parkedRecovery.feed.stop()

// A background parked read yields to a user action and cannot replace its preview.
const parkedEdit = fixture()
parkedEdit.feed.start("lane")
await parkedEdit.reply(0, { ...lane, parked: false })
parkedEdit.firePoll()
parkedEdit.feed.preview(0, 1)
assert.equal(parkedEdit.requests[1].options.signal.aborted, true)
assert.equal(parkedEdit.requests[2].url, "./api/settings/preview")
await parkedEdit.reply(2, { intent: "edit", question: "Save?" })
await parkedEdit.reply(1, { ...lane, view: "stale-background" })
assert.equal(parkedEdit.states.at(-1).pending.intent, "edit")
assert.equal(parkedEdit.states.at(-1).data.view, lane.view)
assert.equal(parkedEdit.timers.size, 0)
parkedEdit.feed.cancel()
assert.equal(parkedEdit.timers.size, 1)
parkedEdit.feed.stop()
assert.equal(parkedEdit.timers.size, 0)

const parkedTimeout = fixture()
parkedTimeout.feed.start("lane")
await parkedTimeout.reply(0, { ...lane, parked: false })
parkedTimeout.firePoll()
parkedTimeout.expire(REQUEST_DEADLINE_MS)
assert.equal(parkedTimeout.states.at(-1).status, "ready")
assert.equal(parkedTimeout.states.at(-1).data.view, lane.view)
assert.match(parkedTimeout.states.at(-1).error, /Reconnecting/)
assert.equal(parkedTimeout.timers.size, 1)
parkedTimeout.feed.stop()

const wheelSection = SETTINGS_SECTIONS.find((section) => section.id === "wheel")
assert.equal(wheelSection.label, "Wheel Controls")
assert.deepEqual(wheelSection.pages, ["wheel"])
const wheelFeed = fixture()
wheelFeed.feed.start("wheel")
assert.equal(wheelFeed.requests[0].url, "./api/settings/pages/wheel")
wheelFeed.feed.stop()

const sentryLive = fixture()
sentryLive.feed.start("sentry")
const sentryPage = { ...lane, page: "sentry", parked: true, subtitle: "Arming · 82s" }
await sentryLive.reply(0, sentryPage)
sentryLive.firePoll()
assert.equal(sentryLive.states.at(-1).status, "ready")
await sentryLive.reply(1, { ...sentryPage, subtitle: "Arming · 81s" })
assert.equal(sentryLive.states.at(-1).data.subtitle, "Arming · 81s")
sentryLive.firePoll()
sentryLive.feed.preview(0, 1)
assert.equal(sentryLive.requests[2].options.signal.aborted, true)
await sentryLive.reply(3, { intent: "sentry-edit", question: "Save?" })
await sentryLive.reply(2, { ...sentryPage, subtitle: "Monitoring motion" })
assert.equal(sentryLive.states.at(-1).pending.intent, "sentry-edit")
assert.equal(sentryLive.states.at(-1).data.subtitle, "Arming · 81s")
sentryLive.feed.cancel()
sentryLive.firePoll()
sentryLive.expire(REQUEST_DEADLINE_MS)
assert.match(sentryLive.states.at(-1).data.subtitle, /unavailable/)
assert.equal(sentryLive.states.at(-1).data.rows, sentryPage.rows)
sentryLive.feed.stop()
assert.equal(sentryLive.timers.size, 0)

const htmlStates = []
const htmlFeed = new SettingsFeed({ publish: state => htmlStates.push(state),
  fetcher: async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token '<'") } }),
  later: () => 1, cancelTimer: () => {} })
htmlFeed.start("aol")
await flush()
assert.equal(htmlStates.at(-1).status, "unavailable")
assert.equal(htmlStates.at(-1).data, null)
assert.equal(htmlStates.at(-1).error, "Galaxy could not load settings. Refresh to reconnect.")
htmlFeed.stop()

const sections = (await import('../web/js/settings.js')).SETTINGS_SECTIONS
assert.equal(sections.at(-1).id, 'developer')
const catalog = JSON.parse((await import('node:fs')).readFileSync(new URL('../web/data/catalog.json', import.meta.url), 'utf8'))
assert(!catalog.tools.some(tool => tool.path.startsWith('/developer')))
const category = {state:{section:'lateral',developerOpen:false,cloudOpen:false,data:{page:'hub'}},busy:false,
  feed:{active:true,stop(){this.active=false},start(page){this.active=true;category.started=page},
    load(page, options){category.loaded={page,options};category.state.data={page}}}}
SettingsPage.methods.selectSection.call(category, sections.at(-1))
assert.equal(category.state.developerOpen, true)
assert.equal(category.feed.active, true)
assert.deepEqual(category.loaded, {page: 'developer', options: {keepData: false, quiet: false}})
SettingsPage.methods.back.call(category)
assert.equal(category.state.developerOpen, true) // Developer is a direct section, with no cloud submenu.
SettingsPage.methods.selectSection.call(category, sections[0])
assert.equal(category.state.developerOpen, false)
assert.deepEqual(category.loaded, {page: 'hub', options: {keepData: false, quiet: false}})
