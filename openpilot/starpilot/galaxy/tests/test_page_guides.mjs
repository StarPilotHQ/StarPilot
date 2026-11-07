import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { PAGE_GUIDES, pageGuide, guidedRows, guidedCards, guideSaveValue } from "../web/js/page-guides.js"
import { SettingsPage } from "../web/js/settings.js"
import { PAGE_COMPANIONS, PAGE_HIDDEN_ROWS } from "../web/js/menu.js"

// The Lane Changes page as the device sends it (labels from ui/lane_change_feature.py and ui/conditional_feature.py).
const row = (label, value, extra = {}) => ({ label, value, reason: "device text", available: true, action: true, choices: ["Off", "On"],
  defaultValue: value, resetAvailable: true, ...extra })
const number = (label, value, extra = {}) => row(label, value, { choices: [], step: 1, minimum: 0, maximum: 10, ...extra })
const laneRows = (overrides = {}) => [
  number("Lane Change Speed", "10"), row("Allow lane changes", "On"), number("Minimum lane-change speed", "5", { unit: "mph" }),
  row("One change per signal", "On"), row("Automatic Lane Changes", "On", { defaultValue: "Off" }),
  number("Automatic lane-change delay", "0.2"), number("Minimum adjacent lane width", "0"), row("Close lane-change gap", "Off"),
  number("Lane-change follow gap", "0.75"), row("Signal lane detection", "On"), number("Signal lane width", "1.524"),
].map(r => (r.label in overrides ? { ...r, ...overrides[r.label] } : r))
const layout = (rows, query = "", { hideRows = PAGE_HIDDEN_ROWS.lane_change } = {}) => {
  // As the menu shows the page: its hidden rows left out, the page's own search run, then the cards built.
  const view = { state: { query, data: { page: "lane_change", rows } }, hideRows }
  const visible = SettingsPage.computed.visibleRows.call(view)
  return guidedCards("lane_change", visible, query)
    .map(card => [card.title, card.entries.map(({ row: r, index }) => `${index} ${r.label}=${r.value}${r.available ? "" : " grey"}${r.guideSub ? " sub" : ""}`),
      ...(card.include ? [card.include.rows] : [])])
}

// Nudgeless (the comma's saved choice): one list with no header; Auto lane changes on top, the close-gap rows after the
// lane change rows, Turn Signal Guidance (from the Steering page) at the end. The signal-lane rows are not on this page.
assert.deepEqual(layout(laneRows()), [
  ["", ["1 Auto lane changes=On", "3 One change per signal=On", "0 Lane Change Speed=10", "4 Wheel nudge required=Off",
    "5 Nudgeless delay=0.2 sub", "6 Minimum adjacent lane width=0 sub", "2 Minimum lane-change speed=5", "7 Close lane-change gap=Off",
    "8 Lane-change follow gap=0.75 grey sub"], ["Turn Signal Guidance"]],
], "rows keep their device positions (saving goes by position)")
assert.match(guidedRows("lane_change", laneRows()).find(r => r.label === "Close lane-change gap").reason, /^For overtaking a slower car: works only when you pull out/)

// Signal lane detection / width live under Experimental conditions: hidden here, shown there as Lane Changes rows.
assert.deepEqual(PAGE_HIDDEN_ROWS.lane_change, ["Signal lane detection", "Signal lane width"])
assert.deepEqual(PAGE_COMPANIONS["conditional/cem"], { page: "lane_change", heading: "Signal speed: turn or lane change",
  onlyRows: ["Signal lane detection", "Signal lane width"] })
const companion = { state: { query: "", data: { page: "lane_change", rows: laneRows({ "Signal lane detection": { value: "Off" } }) } },
  onlyRows: PAGE_COMPANIONS["conditional/cem"].onlyRows }
companion.visibleRows = SettingsPage.computed.visibleRows.call(companion)
const companionCards = SettingsPage.computed.rowCards.call(companion)
assert.deepEqual(companionCards.map(card => [card.title, card.entries.map(({ row: r }) => `${r.label}${r.available ? "" : " grey"}`), !!card.include]),
  [["", ["Signal lane detection", "Signal lane width grey"], false]], "a plain list there, the width grayed while detection is off")
assert.equal(companion.visibleRows.find(({ row: r }) => r.label === "Signal lane width").row.reason, "Turn on Signal lane detection to use this. device text")
assert.equal(guidedRows("lane_change", laneRows()).find(r => r.label === "Signal lane detection").reason, "device text", "the device's own text")

// Wheel nudge required on grays (not hides) the nudgeless settings; Auto lane changes off grays everything that needs it.
const nudge = guidedRows("lane_change", laneRows({ "Automatic Lane Changes": { value: "Off" } }))
assert.match(nudge.find(r => r.label === "Nudgeless delay").reason, /^Turn off Wheel nudge required to use this\. How long after you signal/)
assert.equal(nudge.find(r => r.label === "Nudgeless delay").available, false)
assert.equal(layout(laneRows({ "Automatic Lane Changes": { value: "Off" } }))[0][1][4], "5 Nudgeless delay=0.2 grey sub")
const allOff = layout(laneRows({ "Automatic Lane Changes": { value: "Off" }, "Allow lane changes": { value: "Off" } }))
assert.deepEqual(allOff[0][1], ["1 Auto lane changes=Off", "3 One change per signal=On grey", "0 Lane Change Speed=10 grey",
  "4 Wheel nudge required=On grey", "5 Nudgeless delay=0.2 grey sub", "6 Minimum adjacent lane width=0 grey sub",
  "2 Minimum lane-change speed=5 grey", "7 Close lane-change gap=Off grey", "8 Lane-change follow gap=0.75 grey sub"])
assert.match(guidedRows("lane_change", laneRows({ "Allow lane changes": { value: "Off" } })).find(r => r.label === "Nudgeless delay").reason,
  /^Turn on Auto lane changes to use this\./, "the main switch is named first")

// Close gap on: its follow gap is usable.
assert.deepEqual(layout(laneRows({ "Close lane-change gap": { value: "On" } }))[0][1].slice(7), ["7 Close lane-change gap=On", "8 Lane-change follow gap=0.75 sub"])

// Grayed rows say which switch to turn on; a row the device made unavailable keeps the device's reason.
const shown = guidedRows("lane_change", laneRows({ "Allow lane changes": { value: "Off" }, "Close lane-change gap": { available: false, reason: "Needs speed control" } }))
assert.match(shown.find(r => r.label === "One change per signal").reason, /^Turn on Auto lane changes to use this\. On: one lane change/)
assert.equal(shown.find(r => r.label === "Close lane-change gap").reason, "Needs speed control")

// Wheel nudge required is the Automatic Lane Changes switch the other way round: it saves the device's own Off / On.
const toggle = guidedRows("lane_change", laneRows()).find(r => r.guideLabel === "Automatic Lane Changes")
assert.deepEqual([toggle.label, toggle.value, toggle.choices, toggle.defaultValue], ["Wheel nudge required", "Off", ["On", "Off"], "On"])
assert.equal(guideSaveValue(toggle, "On"), "Off", "nudge required = automatic off")
assert.equal(guideSaveValue(toggle, "Off"), "On", "no nudge = automatic on")
assert.equal(guidedRows("lane_change", laneRows()).find(r => r.guideLabel === "Allow lane changes").label, "Auto lane changes")
assert.equal(guideSaveValue(laneRows()[0], "7"), "7", "other rows save what was picked")
assert.equal(guideSaveValue(undefined, "On"), "On")
assert.match(SettingsPage.template, /feed\.previewValue\(index, guideSaveValue\(row, value\)\)/)
assert.equal(SettingsPage.methods.guideSaveValue, guideSaveValue)
// A card with no title has no header.
assert.match(SettingsPage.template, /<template v-if="card\.guide && card\.title">/)

// Speed text follows the device's units.
assert.match(guidedRows("lane_change", laneRows({ "Minimum lane-change speed": { unit: "km/h" } }))
  .find(r => r.label === "Minimum lane-change speed").reason, /start from 32 km\/h/)

// Nothing disappears: a row the guide does not know goes to "More settings" (first when it is a repair).
assert.deepEqual(layout([...laneRows(), row("A new Dom setting", "Off")]).at(-1), ["More settings", ["11 A new Dom setting=Off"]])
assert.deepEqual(layout([{ label: "Restore Lane Change defaults", value: "", available: true, action: true, confirm: true, choices: [] }])[0],
  ["More settings", ["0 Restore Lane Change defaults="]])
// Search keeps one card for the search box, and matches the new wording.
assert.deepEqual(layout(laneRows(), "zzz"), [["", []]])
assert.deepEqual(layout(laneRows(), "guidance")[0].slice(1), [["2 Minimum lane-change speed=5"], ["Turn Signal Guidance"]], "search finds the included row")

// Turn Signal Guidance: shown inside the Turns card from the Steering page, with new text there too.
assert.deepEqual(PAGE_GUIDES.lane_change.cards[0].include, { page: "torque", rows: ["Turn Signal Guidance"] })
assert.equal(PAGE_COMPANIONS.lane_change, undefined)
assert.deepEqual(PAGE_HIDDEN_ROWS.torque, ["Turn Signal Guidance"])
assert.match(guidedRows("torque", [row("Turn Signal Guidance", "On")])[0].reason, /^On: below 20 mph/)
assert.equal(guidedCards("torque", []), null, "the Steering page keeps its own layout")
assert.match(SettingsPage.template, /<SettingsPage v-if="card\.include && mode === 'local'"[^>]* bare embedded menu-mode/)
assert.equal(SettingsPage.name, "SettingsPage")

// Pages without a guide are untouched.
const plain = [row("Something", "On")]
assert.equal(guidedRows("profiles", plain), plain)
assert.equal(guidedCards("profiles", []), null)
assert.equal(pageGuide("constructor"), null)

// Every label a guide relies on still exists in the device code (a Dom rename would otherwise drop a row into "More settings").
const source = ["../../ui/lane_change_feature.py", "../../ui/conditional_feature.py", "../../ui/feature_settings_owner.py",
  "../../ui/personalities_page.py", "../../ui/slc_offset_feature.py", "../../ui/appearance_owner.py", "../../ui/long_profile_feature.py",
  "../../ui/traffic_feature.py"]
  .map(path => readFileSync(new URL(path, import.meta.url), "utf8")).join("\n")
// Driving Personalities labels are built by the device code from a name or category; check those builders instead.
const generated = [
  [/^(Aggressive|Standard|Relaxed|Traffic) Personality$/, 'name.title() + " Personality"'],
  [/^(Acceleration|Braking)$/, "label=category.title()"],
  [/^Custom (acceleration|braking)$/, 'f"Custom {category}"'],
  [/^Edit custom (acceleration|braking)$/, 'f"Edit custom {category}"'],
  [/^Following at \d+ mph$/, `f"Following at {row.label.removesuffix(' point')}"`],
  [/^\d+ mph point$/, 'f"{speed} mph point"'],
]
for (const [, builder] of generated) assert.ok(source.includes(builder), `device code no longer builds rows with ${builder}`)
for (const guide of Object.values(PAGE_GUIDES)) {
  for (const spec of [...(guide.cards || []).flatMap(card => card.rows), ...(guide.rows || [])]) {
    if (spec.pattern || generated.some(([pattern]) => pattern.test(spec.label))) continue
    assert.ok(source.includes(`"${spec.label}"`) || source.includes(`'${spec.label}'`), `device code no longer has the row "${spec.label}"`)
    for (const need of [...(spec.needs || []), ...(spec.showWhen ? [spec.showWhen] : [])]) {
      assert.ok(source.includes(`"${need.label}"`) || source.includes(`'${need.label}'`), `device code no longer has the row "${need.label}"`)
    }
  }
}
console.log("page guides ok")

// Speed Limit Controller: adoption, early adoption and the offsets are grayed while it is off; Show speed limit signs
// and the sources stay active (the signs use them). The page keeps its own layout and text.
const slcRows = (value, unit = "mph") => [row("Speed Limit Controller", value, { reason: "device text" }), row("Show speed limit signs", "On"),
  row("Automatic adoption", "On"), row("Adopt limits early", "Off", { choices: ["Off", "1 s"] }), row("Primary source", "Map Data"),
  row("Secondary source", "Dashboard"), { label: "Offset policy", value: "", heading: true, available: true, reason: "" },
  number(`0–<25 ${unit} offset`, "0", { unit }), number(`74–<99 ${unit} offset`, "0", { unit }),
  { label: `99 ${unit} and above`, value: "Zero offset", available: true, action: false, reason: "" }]
for (const unit of ["mph", "km/h"]) {
  const slcOff = guidedRows("slc", slcRows("Off", unit))
  assert.deepEqual(slcOff.map(r => [r.label, r.available]), [["Speed Limit Controller", true], ["Show speed limit signs", true],
    ["Automatic adoption", false], ["Adopt limits early", false], ["Primary source", true], ["Secondary source", true], ["Offset policy", true],
    [`0–<25 ${unit} offset`, false], [`74–<99 ${unit} offset`, false], [`99 ${unit} and above`, true]], `grayed while SLC is off (${unit})`)
  assert.equal(slcOff[2].reason, "Turn on Speed Limit Controller to use this. device text")
  assert.equal(slcOff[0].reason, "device text", "the page keeps its own text")
  assert.ok(guidedRows("slc", slcRows("On", unit)).every(r => r.available))
}
assert.equal(guidedCards("slc", []), null, "the SLC page keeps its own layout")
console.log("slc guides ok")
