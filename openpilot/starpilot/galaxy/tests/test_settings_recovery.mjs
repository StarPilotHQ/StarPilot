import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { SettingsRecoveryPage } from "../web/js/settings-recovery.js"

const initial = { view: "view-1", rows: [{ id: 0, page: "appearance", label: "Wheel colors", current: "On", default: "Off", resetAvailable: true }] }
const calls = []
let saved = false
const oldFetch = globalThis.fetch
globalThis.fetch = async (path, options) => {
  calls.push({ path, options, body: options.body ? JSON.parse(options.body) : null })
  const value = path.endsWith("/preview") ? { intent: "confirmation-1", question: "Reset selected settings?", labels: ["Wheel colors"] }
    : path.endsWith("/confirm") ? (saved = true, { saved: true, applied: ["Wheel colors"], refresh: true })
      : (saved ? { view: "view-2", rows: [] } : initial)
  return { ok: true, status: 200, json: async () => value }
}
try {
  const state = { ...SettingsRecoveryPage.data(), mode: "local", unauthorized: () => assert.fail("unexpected logout") }
  for (const [name, method] of Object.entries(SettingsRecoveryPage.methods)) state[name] = method.bind(state)
  await state.refresh()
  assert.equal(state.snapshot.rows[0].default, "Off")
  await state.review([0])
  assert.equal(saved, false)
  assert.equal(state.pending.intent, "confirmation-1")
  assert.deepEqual(calls.at(-1).body, { view: "view-1", selected: [0] })
  await state.confirm()
  assert.equal(saved, true)
  assert.equal(state.snapshot.rows.length, 0)
  assert.equal(state.pending, null)
  assert.deepEqual(calls.find(call => call.path.endsWith("/confirm")).body, { intent: "confirmation-1", confirmed: true })
  assert.match(SettingsRecoveryPage.template, /Reset Selected/)
  assert.match(SettingsRecoveryPage.template, /Reset All Non-Default Settings/)
  assert.match(SettingsRecoveryPage.template, /review\(\[row.id\]\)/)
  assert.match(SettingsRecoveryPage.template, /Current:/)
  assert.match(SettingsRecoveryPage.template, /!row.resetAvailable/)
  assert.match(SettingsRecoveryPage.template, /filter\(row => row.resetAvailable\)/)
  assert.match(SettingsRecoveryPage.template, /Some settings are unavailable for this vehicle/)
  const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
  const tools = readFileSync(new URL("../web/js/diagnostic-tools.js", import.meta.url), "utf8")
  assert.match(app, /route.path === '\/settings\/recovery'/)
  assert.match(tools, /Changed Settings/)
  assert.match(tools, /Reset to Default/)
  assert.match(tools, /navigate', '\/settings\/recovery'/)
} finally { globalThis.fetch = oldFetch }
