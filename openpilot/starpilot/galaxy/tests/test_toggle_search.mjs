import assert from "node:assert/strict"
import { test } from "node:test"
import { readFileSync } from "node:fs"
import { SettingsSearchIndex, searchSettings, toolSearchEntries, ToggleSearch } from "../web/js/toggle-search.js"
import { SETTINGS_SECTIONS } from "../web/js/settings.js"

const response = (page, rows, title = page) => ({ ok: true, status: 200, json: async () => ({ page, title, rows }) })
const catalogTools = JSON.parse(readFileSync(new URL("../web/data/catalog.json", import.meta.url), "utf8")).tools

test("global search reads every current settings section and nested page, then routes to its owner", async () => {
  const pages = new Map()
  for (const section of SETTINGS_SECTIONS) for (const page of section.pages) {
    if (["ui_layout", "favorites"].includes(page)) continue
    pages.set(page, response(page, [{ label: `Saved ${page} setting`, value: "Off", available: true }]))
  }
  pages.set("conditional", response("conditional", [{ label: "CEM options", page: "conditional/cem" }], "Conditional modes"))
  pages.set("conditional/cem", response("conditional/cem", [{ label: "Remember manual choice", reason: "Saved choice", unit: "drives" }], "CEM"))
  const requested = []
  let state
  const index = new SettingsSearchIndex({
    fetcher: async (url, options) => {
      assert.equal(options.credentials, "same-origin")
      assert.equal(options.cache, "no-store")
      requested.push(decodeURIComponent(url.split("/").at(-1)))
      const page = pages.get(decodeURIComponent(url.split("/").at(-1)))
      assert.ok(page, `unexpected settings page ${url}`)
      return page
    },
    publish: (update) => { state = update },
  })
  await index.load()
  assert.equal(state.status, "ready")
  assert.deepEqual(new Set(requested), new Set(pages.keys()))
  assert.deepEqual(searchSettings(state.entries, "manual choice").map(({ page, label, section }) => ({ page, label, section })),
    [{ page: "conditional/cem", label: "Remember manual choice", section: "Longitudinal (Speed & Following)" }])
  assert.equal(searchSettings(state.entries, "saved drives")[0].page, "conditional/cem")
  assert.equal(searchSettings(state.entries, "torque")[0].page, "torque")
  const opened = []
  ToggleSearch.methods.choose.call({ state: { query: "manual", open: true }, openPage: (hit) => opened.push(hit.page) },
    searchSettings(state.entries, "manual")[0])
  assert.deepEqual(opened, ["conditional/cem"])
  index.stop()
  assert.deepEqual(state.entries, [])
})

test("search revokes cached labels on unauthorized response and ignores late reads", async () => {
  let finish
  const late = new Promise((resolve) => { finish = resolve })
  const published = []
  let unauthorized = 0
  const index = new SettingsSearchIndex({
    fetcher: (url) => url.endsWith("/aol") ? Promise.resolve({ ok: false, status: 401 }) : late,
    publish: (update) => published.push(update),
    unauthorized: () => { unauthorized++ },
  })
  const loading = index.load()
  finish(response("lane", [{ label: "Private setting" }]))
  await loading
  assert.equal(unauthorized, 1)
  assert.equal(index.status, "idle")
  assert.deepEqual(index.entries, [])
  assert.deepEqual(published.at(-1).entries, [])
})

test("keyboard selection and Escape are bounded to visible results", () => {
  const chosen = []
  let focused = false
  const context = { state: { query: "lane", open: true, active: 0 }, showResults: true,
    $nextTick: fn => fn(), $refs: { toggle: { getClientRects: () => [1], focus: () => { focused = true } } },
    results: [{ page: "lane" }, { page: "lane_change" }], choose(hit) { chosen.push(hit.page) } }
  const event = (key) => ({ key, prevented: false, preventDefault() { this.prevented = true } })
  ToggleSearch.methods.onKey.call(context, event("ArrowDown"))
  assert.equal(context.state.active, 1)
  ToggleSearch.methods.onKey.call(context, event("Enter"))
  assert.deepEqual(chosen, ["lane_change"])
  ToggleSearch.methods.onKey.call(context, event("Escape"))
  assert.equal(context.state.query, "")
  assert.equal(context.state.open, false)
  assert.equal(focused, true, "Escape restores focus to the compact search button")
})

test("global search finds visible tool tiles by title and description", () => {
  const tools = [{ name: "Navigation & Maps", path: "/navigation", description: "Plan routes and download maps" },
    { name: "Model Manager", path: "/manage_models", description: "Manage models" }]
  const entries = toolSearchEntries(tools)
  assert.equal(searchSettings(entries, "navigation and maps")[0].path, "/navigation")
  assert.equal(searchSettings(entries, "navigation & maps")[0].path, "/navigation")
  assert.equal(searchSettings(entries, "download maps")[0].path, "/navigation")
  assert.deepEqual(searchSettings(entries, "model manager").map(hit => hit.path), ["/manage_models"])
  assert.ok(entries.every(hit => hit.kind === "Tool"))
  assert.equal(entries[0].context, "")
})

test("nested camera tools appear as direct results with a useful location", () => {
  const entries = toolSearchEntries(catalogTools.filter(tool => tool.path === "/cameras"))
  const hits = searchSettings(entries, "V-ASM")
  assert.equal(hits[0].path, "/cameras/vasm")
  assert.equal(hits[0].kind, "Tool")
  assert.equal(hits[0].context, "Cameras")
})

test("tool search walks every depth and indexes other tool sections", () => {
  const tools = catalogTools.filter(tool => ["/logs", "/navigation"].includes(tool.path))
  const entries = toolSearchEntries(tools)
  assert.equal(searchSettings(entries, "crash reports")[0].path, "/logs/crashes")
  assert.equal(searchSettings(entries, "offline maps")[0].path, "/navigation/maps")
  assert.equal(searchSettings(entries, "destination")[0].label, "Destination")
  const nested = toolSearchEntries([{ name: "Parent", path: "/parent", children: [
    { name: "Child", path: "/parent/child", children: [{ name: "Grandchild", path: "/parent/child/grandchild" }] },
  ] }])
  assert.deepEqual(nested.map(hit => hit.path), ["/parent", "/parent/child", "/parent/child/grandchild"])
  assert.equal(nested[2].context, "Parent · Child")
})
