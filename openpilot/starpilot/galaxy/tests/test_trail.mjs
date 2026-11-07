import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { toolTrail } from "../web/js/trail.js"
import { settingsTrailPages } from "../web/js/settings.js"

const catalog = JSON.parse(readFileSync(new URL("../web/data/catalog.json", import.meta.url), "utf8"))
const tools = catalog.tools || catalog
const labels = path => toolTrail(path, tools).map(item => item.label)

// Pages opened from Tools: Tools › tool › sub-page.
assert.deepEqual(labels("/logs/monitor"), ["Tools", "Logs & Diagnostics", "System Monitor"])
assert.deepEqual(labels("/cameras/events"), ["Tools", "Cameras & Monitoring", "Sentry"])
assert.deepEqual(labels("/manage_models"), ["Tools", "Model Manager"])
assert.deepEqual(labels("/navigation/maps"), ["Tools", "Navigation & Maps", "Offline Maps"])
assert.deepEqual(labels("/cameras/sentry-settings"), ["Tools", "Cameras & Monitoring", "Sentry settings"])
// A child listed under a tool whose path lies elsewhere still finds its tool.
assert.deepEqual(labels("/device-preferences"), ["Tools", "Logs & Diagnostics", "Force Drive State"])
// Each level links to its own page.
assert.deepEqual(toolTrail("/logs/monitor", tools).map(item => item.id), ["/tools", "/logs", "/logs/monitor"])
// No tree on Tools itself or pages outside Tools.
for (const path of ["/tools", "/", "/settings", "/recordings", ""]) assert.deepEqual(toolTrail(path, tools), [])

// Settings trees: nested IDs, and personalities under Profiles.
assert.deepEqual(settingsTrailPages("lane/activation/lines"), ["lane", "lane/activation", "lane/activation/lines"])
assert.deepEqual(settingsTrailPages("aggressive/braking"), ["profiles", "aggressive", "aggressive/braking"])
assert.deepEqual(settingsTrailPages("hub"), [])

// Toggles turns the tree on; Driving pages get it with Driving as the root.
const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
assert.match(app, /navigation-path :initial-page="state\.searchPage/)
assert.match(app, /:return-to="returnToDriving" trail-root="Driving"/)
assert.match(app, /<GxTrail v-if="pageTrail\.length"/)
const settings = readFileSync(new URL("../web/js/settings.js", import.meta.url), "utf8")
assert.match(settings, /<GxTrail v-if="trailItems\.length" :items="trailItems"/)
console.log("trail ok")
