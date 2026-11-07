import assert from "node:assert/strict"
import { guidedRows, guidedCards } from "../web/js/page-guides.js"

// Driving Personalities as the device sends it (ui/feature_settings_owner.py, 7 Nov 2026 layout).
const row = (label, value = "", extra = {}) => ({ label, value, reason: "device text", available: true, action: true, choices: [],
  defaultValue: value, resetAvailable: true, ...extra })
const cards = (page, rows) => guidedCards(page, guidedRows(page, rows).map((r, index) => ({ row: r, index })), "")
const layout = (page, rows) => cards(page, rows).map(card => [card.id, card.entries.map(({ row }) => (row.guideSub ? ">" : "") + row.label)])

// The hub lists the personalities only.
assert.deepEqual(layout("personalities", ["Aggressive", "Standard", "Relaxed", "Traffic"].map(name =>
  row(`${name} Personality`, "Following: Custom · Acceleration: Sport · Braking: Normal", { page: name.toLowerCase(), action: false }))),
  [["personalities", ["Aggressive", "Standard", "Relaxed", "Traffic"]]])

// A personality page: Acceleration and Braking (Custom toggle, then its choice, Edit link and SLC limit), then Following.
for (const page of ["aggressive", "traffic"]) {
  assert.deepEqual(layout(page, [
    row("Custom acceleration", "On"), row("Acceleration", "Custom"), row("Edit custom acceleration", "", { page: `${page}/acceleration` }),
    row("Custom braking", "Off"),
    row("Following", "Custom"), row("Edit custom following", "Simple: gap and smoothness", { page: `${page}/custom_following` }),
  ]), [
    ["acceleration", ["Custom acceleration", ">Acceleration", ">Edit custom acceleration"]],
    ["braking", ["Custom braking"]],
    ["following", ["Following", ">Edit custom following"]],
  ])
}

// Custom following: Simple or Advanced gap, then smoothness, then reset.
const jerks = ["Acceleration jerk", "Deceleration jerk", "Speed jerk", "Speed decrease jerk", "Danger jerk"].map(label => row(label, "100"))
assert.deepEqual(layout("standard/custom_following", [row("Customization", "Simple"), row("Low-speed follow", "1.6"),
  row("High-speed follow", "1.2"), ...jerks, row("Reset Profile to Default", "", { confirm: true })]), [
  ["gap", ["Customization", ">Low-speed follow", ">High-speed follow"]],
  ["smoothness", jerks.map(r => r.label)],
  ["reset", ["Reset Profile to Default"]],
])
assert.deepEqual(layout("standard/custom_following", [row("Customization", "Advanced"), row("Following at 0 mph", "2.0"), ...jerks])[0],
  ["gap", ["Customization", ">Following at 0 mph"]])

// The Edit page of a custom acceleration or braking curve shows the speed points only.
assert.deepEqual(layout("standard/acceleration", [row("Preset", "Custom"), row("0 mph point", "0.2"),
  row("10 mph point", "2.2")]), [["curve", ["0 mph point", "10 mph point"]]])
console.log("personalities guide ok")
