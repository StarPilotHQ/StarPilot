import assert from "node:assert/strict"
import { SettingsFeed } from "../web/js/settings.js"
import { GalaxySettingRow } from "../web/js/galaxy-setting-row.js"
import { compile } from "../web/vendor/vue/vue.esm-browser.js"

const requests = [], updates = []
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }
const feed = new SettingsFeed({ publish: state => updates.push(state), later: () => 1, cancelTimer: () => {},
  fetcher: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })) })
async function reply(index, body, status = 200) {
  requests[index].resolve({ ok: status === 200, status, json: async () => body }); await flush()
}
const row = { label: "Use StarPilot Widgets", value: "Off", defaultValue: "On", resetAvailable: true,
  choices: ["Off", "On"], page: "", available: true, action: true, revision: "same-settings" }
const page = { page: "appearance", parked: true, view: "opaque-view", rows: [row] }
feed.start("appearance")
await reply(0, page)
feed.resetDefault(0)
assert.equal(requests[1].url, "./api/settings/reset-default")
assert.deepEqual(JSON.parse(requests[1].options.body), { view: "opaque-view", row: 0 })
await reply(1, { intent: "reset-intent", question: "Reset to default?", proposed: "On" })
assert.equal(updates.at(-1).pending, null, "defaults never display a per-setting confirmation")
assert.equal(requests[2].url, "./api/settings/confirm")
assert.deepEqual(JSON.parse(requests[2].options.body), { intent: "reset-intent", confirmed: true, page: "appearance" })
await reply(2, { saved: true })
await reply(3, { ...page, view: "fresh", rows: [{ ...row, value: "On", resetAvailable: false }] })
feed.resetDefault(0)
assert.equal(requests.length, 4, "an unavailable default cannot be submitted")
feed.stop()
assert.ok(GalaxySettingRow.emits.includes("reset-default"))

const renderTemplate = compile(GalaxySettingRow.template, { decodeEntities: raw => raw })
const rowContext = context => ({ ...context, presentedRow: GalaxySettingRow.computed.presentedRow.call(context) })
const render = (context, cache) => renderTemplate(rowContext(context), cache)
function buttons(node) {
  if (!node || typeof node !== "object") return []
  return [...(node.type === "button" ? [node] : []), ...(Array.isArray(node.children) ? node.children.flatMap(buttons) : [])]
}
const context = { compact: false, row, index: 2, control: "switch", currentValue: "Off", dimmed: false, locked: false, busy: false, updating: false,
  showDefault: true, onSwitch: () => {}, $emit: (...args) => events.push(args) }
const events = []
const button = buttons(render(context, [])).find(node => node.children === "Default")
assert.ok(button, "the button is actually rendered for a setting with a default")
assert.equal(button.props.disabled, false)
assert.equal(button.props["aria-label"], "Reset Use StarPilot Widgets to default")
button.props.onClick()
assert.deepEqual(events, [["reset-default", 2]])
const savingRow = render({ ...context, locked: true, busy: true }, [])
assert.equal(savingRow.props.inert, true, "saving blocks interaction without dimming controls")
assert.equal(buttons(savingRow).find(node => node.children === "Default").props.disabled, false)
assert.equal(buttons(render({ ...context, dimmed: true }, [])).find(node => node.children === "Default").props.disabled, true)
assert.equal(buttons(render({ ...context, row: { ...row, defaultValue: null }, showDefault: false }, [])).some(node => node.children === "Default"), false)

// A successful mutation invalidates every active consumer without user refreshes.
let savedValue = "Off"
const dependent = []
const sharedFetcher = async (url) => ({ ok: true, status: 200, json: async () => {
  if (url.endsWith("/reset-default")) return { intent: "shared-default" }
  if (url.endsWith("/confirm")) { savedValue = "On"; return { saved: true } }
  const pageName = url.split("/").at(-1)
  return { page: pageName, view: pageName + savedValue, parked: true, rows: [{ ...row, value: savedValue }] }
} })
const source = new SettingsFeed({ publish() {}, fetcher: sharedFetcher, later: () => 1, cancelTimer() {} })
const consumer = new SettingsFeed({ publish: value => dependent.push(value), fetcher: sharedFetcher, later: () => 1, cancelTimer() {} })
await source.start("appearance")
await consumer.start("pip")
await source.resetDefault(0)
await flush()
assert.equal(dependent.at(-1).data.rows[0].value, "On")
source.stop()
consumer.stop()

assert.equal(GalaxySettingRow.computed.showDefault.call(rowContext({ row })), true)
assert.equal(GalaxySettingRow.computed.showDefault.call(rowContext({ row: { ...row, value: 'On' } })), false)
assert.equal(GalaxySettingRow.computed.showDefault.call(rowContext({ row: { ...row, value: '0.9', defaultValue: '0.90' } })), false)
assert.equal(buttons(render({ ...context, showDefault: false }, [])).some(node => node.children === 'Default'), false)
